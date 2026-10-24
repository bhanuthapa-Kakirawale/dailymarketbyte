"""shadow_scheduler settings - the static, trusted job registry and lock/schedule constants.
No subprocess, no file I/O at import time: this module is pure data, built once.

JOB_REGISTRY is the ONE source of truth `runner.py` consults to build a child command - never
an env var, CLI-supplied path, or any market/log/session text.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from .models import JobSpec

OUT_SUBDIR = "shadow_scheduler"

# Bounded-wait lock policy (docs/SHADOW_PRODUCTION_OPERATIONS.md): long enough to outlast any
# observed intelligence_refresh/PRE/POST run, short enough that a scheduled job never waits
# past its own usefulness.
LOCK_WAIT_SECONDS = 15 * 60
LOCK_POLL_SECONDS = 15

TASK_SCHEDULER_FOLDER = "\\DMB\\"
EXECUTION_TIME_LIMIT = "PT50M"   # ISO-8601 duration; midpoint of the suggested 45-60 min range
EXECUTION_TIME_LIMIT_SECONDS = 50 * 60   # the same value, for arithmetic

# Runner wait budget (PK-OPS-1, 2026-10-24): a runner never WAITS (dependency or lock) past this
# many seconds after its own start, so it always writes its own terminal record before Task
# Scheduler's ExecutionTimeLimit above terminates it (a terminated runner's `finally` never
# runs - that is how the 2026-10-21 06:48 POST checkpoint was left WAITING_DEPENDENCY). Five
# minutes of margin; this never changes a schedule or a window, only how long a runner waits.
RUNNER_WAIT_BUDGET_SECONDS = EXECUTION_TIME_LIMIT_SECONDS - 5 * 60

# 1-hour operational EXECUTION WINDOW per job (PK-E follow-up, 2026-10-12), IST. Deliberately a
# different concept from EXECUTION_TIME_LIMIT above: that is the MAXIMUM RUNTIME of an already-
# launched process (50 minutes, enforced by Windows Task Scheduler itself); this is the window
# of wall-clock time during which the runner will even agree to START the child at all. A job
# invoked at/after its own scheduled_time but before scheduled_time + this many minutes may
# launch (subject to the global lock); at/after the deadline it is EXPIRED and the runner
# refuses to launch the child - StartWhenAvailable can still fire the Task Scheduler entry
# late, but the Python runner is what actually enforces the cutoff.
EXECUTION_WINDOW_MINUTES = 60

# Scheduler-level dependency ordering (PK-E follow-up, dependency ordering V1): a small static,
# trusted map - never derived from data/config/content - of which job must reach a terminal,
# ATTEMPTED outcome (see shadow_scheduler/dependency.py) before another job is allowed to launch
# its own child. intelligence_am before pre; intelligence_pm before post. The two intelligence
# jobs have no entry (no dependency of their own). This is ordering only: it never decides
# whether the dependent job's render is actually safe - PK-C remains the sole authority on that.
DEPENDENCIES: dict[str, str] = {"pre": "intelligence_am", "post": "intelligence_pm"}

# Poll interval for `dependency.wait()`'s bounded wait loop - short enough that a dependency
# satisfied early in the window doesn't cost meaningful wall-clock time, long enough not to spin.
DEPENDENCY_POLL_SECONDS = 10

JOB_REGISTRY: dict[str, JobSpec] = {
    "intelligence_am": JobSpec(
        job="intelligence_am",
        bat_relpath="scripts/run_intelligence_refresh.bat",
        scheduled_time="06:40", weekdays=(0, 1, 2, 3, 4), edition=None,
        business_latest_relpath=os.path.join("intelligence_refresh", "latest.json"),
        task_name="DMB-Shadow-Intelligence-AM"),
    "pre": JobSpec(
        job="pre",
        bat_relpath="scripts/run_production_pre.bat",
        scheduled_time="07:00", weekdays=(0, 1, 2, 3, 4), edition="PRE",
        business_latest_relpath=os.path.join("production_runs", "PRE", "latest.json"),
        task_name="DMB-Shadow-PRE"),
    "intelligence_pm": JobSpec(
        job="intelligence_pm",
        bat_relpath="scripts/run_intelligence_refresh.bat",
        scheduled_time="19:15", weekdays=(0, 1, 2, 3, 4), edition=None,
        business_latest_relpath=os.path.join("intelligence_refresh", "latest.json"),
        task_name="DMB-Shadow-Intelligence-PM"),
    "post": JobSpec(
        job="post",
        bat_relpath="scripts/run_production_post.bat",
        scheduled_time="19:30", weekdays=(0, 1, 2, 3, 4), edition="POST",
        business_latest_relpath=os.path.join("production_runs", "POST", "latest.json"),
        task_name="DMB-Shadow-POST"),
}


def shadow_out_dir(out_dir: str | None = None) -> str:
    if out_dir is not None:
        return out_dir
    import config
    return os.path.join(config.OUT_DIR, OUT_SUBDIR)


def repo_root() -> str:
    """The repo root, discovered by walking up from this file - never from an env var or
    CLI-supplied path, so there is exactly one trusted source for "where the repo lives"."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def execution_window(spec: JobSpec, now_ist: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """(scheduled_dt, deadline_dt) of the occurrence ON `now_ist`'s own calendar date, in
    `now_ist`'s own tzinfo. A start is valid only for `scheduled_dt <= now < deadline_dt`.

    This answers "where is TODAY's window", never "may this invocation launch" - an invocation
    BEFORE `scheduled_dt` is not inside today's window just because it is before its deadline
    (that was the 2026-10-21 06:48 defect: yesterday's missed 19:15/19:30 jobs, fired late by
    `StartWhenAvailable`, compared themselves against TODAY's 19:15/19:30 window and launched).
    Launch eligibility is `resolve_occurrence()` below."""
    hour, minute = (int(part) for part in spec.scheduled_time.split(":"))
    scheduled_dt = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    deadline_dt = scheduled_dt + dt.timedelta(minutes=EXECUTION_WINDOW_MINUTES)
    return scheduled_dt, deadline_dt


# Invocation classification (PK-OPS-1, 2026-10-24). Every invocation is attributed to exactly
# ONE scheduled occurrence (job, IST scheduled_date) and classified:
#   IN_WINDOW - window_start <= now < window_end of an occurrence on a scheduled weekday: the
#               only class that may launch the child.
#   EARLY     - before the NEXT occurrence's window_start, by at most
#               EARLY_ATTRIBUTION_MINUTES. Task Scheduler triggers never fire early, so this is
#               a manual run or clock skew just ahead of the window; attributed to that next
#               occurrence, rejected (runner_status EARLY).
#   LATE      - everything else outside a window: attributed to the most recent PAST occurrence
#               whose window has already closed (e.g. a `StartWhenAvailable` catch-up of last
#               night's job at 06:48, or of Friday's job on Monday morning); rejected
#               (runner_status EXPIRED).
# Weekdays only, from JobSpec.weekdays - deliberately NOT the NSE session calendar: the tasks
# fire Mon-Fri and the children themselves already SKIP a holiday, so session semantics are
# unchanged here.
EARLY_ATTRIBUTION_MINUTES = EXECUTION_WINDOW_MINUTES
IN_WINDOW, EARLY, LATE = "IN_WINDOW", "EARLY", "LATE"
_OCCURRENCE_SEARCH_DAYS = 8


@dataclass(frozen=True)
class Occurrence:
    job: str
    scheduled_date: dt.date          # the IST date of the scheduled occurrence it belongs to
    window_start: dt.datetime        # IST, inclusive
    window_end: dt.datetime          # IST, exclusive
    invocation: str                  # IN_WINDOW | EARLY | LATE


def to_ist(value: dt.datetime) -> dt.datetime:
    """Asia/Kolkata explicitly (root `config.IST` is `ZoneInfo("Asia/Kolkata")`); a naive value
    is taken as UTC, the convention every run record in this package uses."""
    import config as root_config
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(root_config.IST)


def window_for_date(spec: JobSpec, scheduled_date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    import config as root_config
    hour, minute = (int(part) for part in spec.scheduled_time.split(":"))
    start = dt.datetime(scheduled_date.year, scheduled_date.month, scheduled_date.day, hour,
                        minute, tzinfo=root_config.IST)
    return start, start + dt.timedelta(minutes=EXECUTION_WINDOW_MINUTES)


def resolve_occurrence(spec: JobSpec, now: dt.datetime) -> Occurrence:
    """The ONE invocation-attribution rule (see the block comment above). Pure; `now` is any
    tz-aware instant (or naive UTC). Both `runner.py` (enforcing) and `status.py`/`orphans.py`
    (reporting, incl. legacy records that predate the `scheduled_date` field) call this."""
    now_ist = to_ist(now)
    today = now_ist.date()
    days = [today + dt.timedelta(days=k)
            for k in range(-_OCCURRENCE_SEARCH_DAYS, _OCCURRENCE_SEARCH_DAYS + 1)]
    days = [d for d in days if d.weekday() in spec.weekdays]
    for d in days:
        start, end = window_for_date(spec, d)
        if start <= now_ist < end:
            return Occurrence(spec.job, d, start, end, IN_WINDOW)
    future = [d for d in days if window_for_date(spec, d)[0] > now_ist]
    past = [d for d in days if window_for_date(spec, d)[1] <= now_ist]
    if future:
        start, end = window_for_date(spec, future[0])
        if (start - now_ist) <= dt.timedelta(minutes=EARLY_ATTRIBUTION_MINUTES):
            return Occurrence(spec.job, future[0], start, end, EARLY)
    if past:
        start, end = window_for_date(spec, past[-1])
        return Occurrence(spec.job, past[-1], start, end, LATE)
    start, end = window_for_date(spec, future[0])   # pragma: no cover - weekdays always non-empty
    return Occurrence(spec.job, future[0], start, end, EARLY)


__all__ = ["OUT_SUBDIR", "LOCK_WAIT_SECONDS", "LOCK_POLL_SECONDS", "TASK_SCHEDULER_FOLDER",
          "EXECUTION_TIME_LIMIT", "EXECUTION_TIME_LIMIT_SECONDS", "RUNNER_WAIT_BUDGET_SECONDS",
          "EXECUTION_WINDOW_MINUTES", "DEPENDENCIES",
          "DEPENDENCY_POLL_SECONDS", "JOB_REGISTRY", "shadow_out_dir", "repo_root",
          "execution_window", "EARLY_ATTRIBUTION_MINUTES", "IN_WINDOW", "EARLY", "LATE",
          "Occurrence", "to_ist", "window_for_date", "resolve_occurrence"]

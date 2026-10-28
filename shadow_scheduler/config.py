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

# Execution budget (2026-10-28 timing packet). Task Scheduler terminates the RUNNER at
# ExecutionTimeLimit (PT50M) counted from its own start - a terminated runner's `finally` never
# runs, and its `.bat` child tree is not reliably terminated with it. So a runner spends at most
# RUNNER_WAIT_BUDGET_SECONDS waiting (dependency + lock) and never launches a child later than
# that: every launched child keeps >= CHILD_RUNTIME_RESERVE_SECONDS +
# RUNNER_FINALIZE_MARGIN_SECONDS (25 min) before the limit.
#   CHILD_RUNTIME_RESERVE_SECONDS: 20 min, >= 2.3x the slowest child observed in
#     output/shadow_scheduler/runs (2026-10-09..10-28): intelligence_pm 521 s (10-22),
#     pre 518 s (10-26), post ~408 s (10-24), intelligence_am 212 s (10-13).
#   RUNNER_FINALIZE_MARGIN_SECONDS: 5 min for the runner's own terminal record + lock release.
#   RUNNER_WAIT_BUDGET_SECONDS = 50 - 20 - 5 = 25 min: still covers the worst observed upstream
#     wait (post waited 640 s for intelligence_pm's 521 s run on 2026-10-22).
# Before this packet the wait budget was 45 min, which could still launch a child with ~5
# minutes left (PK-OPS-1, 2026-10-24, fixed only the "never finalized" half of that problem).
CHILD_RUNTIME_RESERVE_SECONDS = 20 * 60
RUNNER_FINALIZE_MARGIN_SECONDS = 5 * 60
RUNNER_WAIT_BUDGET_SECONDS = (EXECUTION_TIME_LIMIT_SECONDS - CHILD_RUNTIME_RESERVE_SECONDS
                              - RUNNER_FINALIZE_MARGIN_SECONDS)

# Permitted START windows (2026-10-28 timing packet), IST. A different concept from
# EXECUTION_TIME_LIMIT above (the maximum runtime of an already-launched process): this is the
# wall-clock range during which the runner will even agree to START the child. Each JobSpec
# carries its own window_start/window_end ("HH:MM", start inclusive, end exclusive, one
# calendar day - never across midnight) separately from its Task Scheduler trigger
# (`scheduled_time`). `StartWhenAvailable` can still fire a task late; the Python runner is what
# enforces the window. Values and derivation: docs/SHADOW_PRODUCTION_OPERATIONS.md
# "Schedule and permitted start windows".
#
# LEGACY_WINDOWS is the rule every record WITHOUT a stored attribution (`scheduled_date` +
# `invocation`, i.e. written before PK-OPS-1 on 2026-10-24) was produced under: trigger ..
# trigger + 60 min at the old 06:40 / 07:00 / 19:15 / 19:30 triggers. Frozen - such records are
# inferred under the rule that was actually in force, never re-attributed under today's wider
# windows (orphans.attribution).
LEGACY_WINDOW_MINUTES = 60
LEGACY_WINDOWS: dict[str, tuple[str, str]] = {
    "intelligence_am": ("06:40", "07:40"),
    "pre": ("07:00", "08:00"),
    "intelligence_pm": ("19:15", "20:15"),
    "post": ("19:30", "20:30"),
}
WINDOW_POLICY = "2"   # stamped on every new RunRecord (`window_policy`)

# Scheduler-level dependency ordering (PK-E follow-up, dependency ordering V1): a small static,
# trusted map - never derived from data/config/content - of which job must have COMPLETED
# (child exit 0) for the same scheduled_date (see shadow_scheduler/dependency.py) before another
# job is allowed to launch its own child. intelligence_am before pre; intelligence_pm before
# post. The two intelligence jobs have no entry (no dependency of their own). This is ordering
# only: it never decides whether the dependent job's render is actually safe - PK-C remains the
# sole authority on that.
# Missing-upstream grace: an upstream's lock wait is capped at its own window end, after which
# it always writes a terminal record - so no record at all this long after its window_end
# means it never ran (dependency.MISSING), not that it is still starting.
DEPENDENCY_MISSING_GRACE_SECONDS = 2 * 60
DEPENDENCIES: dict[str, str] = {"pre": "intelligence_am", "post": "intelligence_pm"}

# Poll interval for `dependency.wait()`'s bounded wait loop - short enough that a dependency
# satisfied early in the window doesn't cost meaningful wall-clock time, long enough not to spin.
DEPENDENCY_POLL_SECONDS = 10

JOB_REGISTRY: dict[str, JobSpec] = {
    "intelligence_am": JobSpec(
        job="intelligence_am",
        bat_relpath="scripts/run_intelligence_refresh.bat",
        scheduled_time="06:00", weekdays=(0, 1, 2, 3, 4), edition=None,
        business_latest_relpath=os.path.join("intelligence_refresh", "latest.json"),
        task_name="DMB-Shadow-Intelligence-AM",
        # 08:45: it rebuilds the previous session's EOD chain (nothing in it goes stale after
        # 08:00) and must still finish (worst 212 s) before PRE's 09:00 cutoff.
        window_start="06:00", window_end="08:45"),
    "pre": JobSpec(
        job="pre",
        bat_relpath="scripts/run_production_pre.bat",
        scheduled_time="06:30", weekdays=(0, 1, 2, 3, 4), edition="PRE",
        business_latest_relpath=os.path.join("production_runs", "PRE", "latest.json"),
        task_name="DMB-Shadow-PRE",
        window_start="06:00", window_end="09:00"),   # 09:00 = hard cutoff for a new PRE child
    "intelligence_pm": JobSpec(
        job="intelligence_pm",
        bat_relpath="scripts/run_intelligence_refresh.bat",
        scheduled_time="19:15", weekdays=(0, 1, 2, 3, 4), edition=None,
        business_latest_relpath=os.path.join("intelligence_refresh", "latest.json"),
        task_name="DMB-Shadow-Intelligence-PM",
        # 23:30: POST's last permitted start is 23:59 and PM must have finished by then -
        # 23:59 - CHILD_RUNTIME_RESERVE (20 min, >= 2.3x PM's worst 521 s) = 23:39, rounded down.
        window_start="19:15", window_end="23:30"),
    "post": JobSpec(
        job="post",
        bat_relpath="scripts/run_production_post.bat",
        scheduled_time="19:30", weekdays=(0, 1, 2, 3, 4), edition="POST",
        business_latest_relpath=os.path.join("production_runs", "POST", "latest.json"),
        task_name="DMB-Shadow-POST",
        window_start="19:00", window_end="23:59"),
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


def _hhmm(value: str) -> tuple[int, int]:
    hour, minute = (int(part) for part in value.split(":"))
    return hour, minute


def window_times(spec: JobSpec, windows: dict | None = None) -> tuple[str, str]:
    """("HH:MM", "HH:MM") permitted start window of `spec` - `windows[spec.job]` (e.g.
    LEGACY_WINDOWS) when given, else the JobSpec's own window_start/window_end."""
    if windows is not None:
        return windows[spec.job]
    return spec.window_start, spec.window_end


def _validate_registry() -> None:
    """Import-time invariants: every window lies inside ONE calendar day (never across
    midnight) and contains its own trigger, and the wait budget leaves the child its reserve."""
    for spec in JOB_REGISTRY.values():
        start, end = _hhmm(spec.window_start), _hhmm(spec.window_end)
        if not (start <= _hhmm(spec.scheduled_time) < end):
            raise ValueError(f"{spec.job}: trigger {spec.scheduled_time} outside its window "
                             f"{spec.window_start}-{spec.window_end}")
    if RUNNER_WAIT_BUDGET_SECONDS <= 0 or (
            RUNNER_WAIT_BUDGET_SECONDS + CHILD_RUNTIME_RESERVE_SECONDS
            + RUNNER_FINALIZE_MARGIN_SECONDS > EXECUTION_TIME_LIMIT_SECONDS):
        raise ValueError(f"runner wait budget leaves the child no reserve under "
                         f"{EXECUTION_TIME_LIMIT}")


def execution_window(spec: JobSpec, now_ist: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """(window_start, window_end) of the occurrence ON `now_ist`'s own calendar date, in IST.
    A start is valid only for `window_start <= now < window_end`.

    This answers "where is TODAY's window", never "may this invocation launch" - an invocation
    BEFORE `window_start` is not inside today's window just because it is before its end (that
    was the 2026-10-21 06:48 defect: yesterday's missed 19:15/19:30 jobs, fired late by
    `StartWhenAvailable`, compared themselves against TODAY's 19:15/19:30 window and launched).
    Launch eligibility is `resolve_occurrence()` below."""
    return window_for_date(spec, to_ist(now_ist).date())


# Invocation classification (PK-OPS-1, 2026-10-24). Every invocation is attributed to exactly
# ONE scheduled occurrence (job, IST scheduled_date) and classified:
#   IN_WINDOW - window_start <= now < window_end of an occurrence on a scheduled weekday: the
#               only class that may launch the child.
#   EARLY     - before the NEXT occurrence's window_start, by at most
#               EARLY_ATTRIBUTION_MINUTES (60). Task Scheduler triggers never fire early, so this is
#               a manual run or clock skew just ahead of the window; attributed to that next
#               occurrence, rejected (runner_status EARLY).
#   LATE      - everything else outside a window: attributed to the most recent PAST occurrence
#               whose window has already closed (e.g. a `StartWhenAvailable` catch-up of last
#               night's job at 06:48, or of Friday's job on Monday morning); rejected
#               (runner_status EXPIRED).
# Weekdays only, from JobSpec.weekdays - deliberately NOT the NSE session calendar: the tasks
# fire Mon-Fri and the children themselves already SKIP a holiday, so session semantics are
# unchanged here.
EARLY_ATTRIBUTION_MINUTES = 60
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


def window_for_date(spec: JobSpec, scheduled_date: dt.date,
                    windows: dict | None = None) -> tuple[dt.datetime, dt.datetime]:
    """(start, end) IST datetimes of `spec`'s permitted start window on `scheduled_date` - the
    JobSpec's own window, or `windows[spec.job]` (e.g. LEGACY_WINDOWS) when given."""
    import config as root_config
    start_hhmm, end_hhmm = window_times(spec, windows)

    def _at(hhmm: str) -> dt.datetime:
        hour, minute = _hhmm(hhmm)
        return dt.datetime(scheduled_date.year, scheduled_date.month, scheduled_date.day, hour,
                           minute, tzinfo=root_config.IST)
    return _at(start_hhmm), _at(end_hhmm)


def resolve_occurrence(spec: JobSpec, now: dt.datetime, *,
                       windows: dict | None = None) -> Occurrence:
    """The ONE invocation-attribution rule (see the block comment above). Pure; `now` is any
    tz-aware instant (or naive UTC). Both `runner.py` (enforcing) and `status.py`/`orphans.py`
    (reporting) call this; `windows=LEGACY_WINDOWS` replays the pre-2026-10-28 rule for a record
    that predates the `scheduled_date` field (orphans.attribution)."""
    now_ist = to_ist(now)
    today = now_ist.date()
    days = [today + dt.timedelta(days=k)
            for k in range(-_OCCURRENCE_SEARCH_DAYS, _OCCURRENCE_SEARCH_DAYS + 1)]
    days = [d for d in days if d.weekday() in spec.weekdays]
    for d in days:
        start, end = window_for_date(spec, d, windows)
        if start <= now_ist < end:
            return Occurrence(spec.job, d, start, end, IN_WINDOW)
    future = [d for d in days if window_for_date(spec, d, windows)[0] > now_ist]
    past = [d for d in days if window_for_date(spec, d, windows)[1] <= now_ist]
    if future:
        start, end = window_for_date(spec, future[0], windows)
        if (start - now_ist) <= dt.timedelta(minutes=EARLY_ATTRIBUTION_MINUTES):
            return Occurrence(spec.job, future[0], start, end, EARLY)
    if past:
        start, end = window_for_date(spec, past[-1], windows)
        return Occurrence(spec.job, past[-1], start, end, LATE)
    start, end = window_for_date(spec, future[0], windows)   # pragma: no cover
    return Occurrence(spec.job, future[0], start, end, EARLY)


_validate_registry()


__all__ = ["OUT_SUBDIR", "LOCK_WAIT_SECONDS", "LOCK_POLL_SECONDS", "TASK_SCHEDULER_FOLDER",
          "EXECUTION_TIME_LIMIT", "EXECUTION_TIME_LIMIT_SECONDS", "RUNNER_WAIT_BUDGET_SECONDS",
          "CHILD_RUNTIME_RESERVE_SECONDS", "RUNNER_FINALIZE_MARGIN_SECONDS",
          "LEGACY_WINDOW_MINUTES", "LEGACY_WINDOWS", "WINDOW_POLICY", "window_times",
          "DEPENDENCIES", "DEPENDENCY_MISSING_GRACE_SECONDS",
          "DEPENDENCY_POLL_SECONDS", "JOB_REGISTRY", "shadow_out_dir", "repo_root",
          "execution_window", "EARLY_ATTRIBUTION_MINUTES", "IN_WINDOW", "EARLY", "LATE",
          "Occurrence", "to_ist", "window_for_date", "resolve_occurrence"]

"""shadow_scheduler settings - the static, trusted job registry and lock/schedule constants.
No subprocess, no file I/O at import time: this module is pure data, built once.

JOB_REGISTRY is the ONE source of truth `runner.py` consults to build a child command - never
an env var, CLI-supplied path, or any market/log/session text.
"""
from __future__ import annotations

import datetime as dt
import os

from .models import JobSpec

OUT_SUBDIR = "shadow_scheduler"

# Bounded-wait lock policy (docs/SHADOW_PRODUCTION_OPERATIONS.md): long enough to outlast any
# observed intelligence_refresh/PRE/POST run, short enough that a scheduled job never waits
# past its own usefulness.
LOCK_WAIT_SECONDS = 15 * 60
LOCK_POLL_SECONDS = 15

TASK_SCHEDULER_FOLDER = "\\DMB\\"
EXECUTION_TIME_LIMIT = "PT50M"   # ISO-8601 duration; midpoint of the suggested 45-60 min range

# 1-hour operational EXECUTION WINDOW per job (PK-E follow-up, 2026-10-12), IST. Deliberately a
# different concept from EXECUTION_TIME_LIMIT above: that is the MAXIMUM RUNTIME of an already-
# launched process (50 minutes, enforced by Windows Task Scheduler itself); this is the window
# of wall-clock time during which the runner will even agree to START the child at all. A job
# invoked at/after its own scheduled_time but before scheduled_time + this many minutes may
# launch (subject to the global lock); at/after the deadline it is EXPIRED and the runner
# refuses to launch the child - StartWhenAvailable can still fire the Task Scheduler entry
# late, but the Python runner is what actually enforces the cutoff.
EXECUTION_WINDOW_MINUTES = 60

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
    """(scheduled_dt, deadline_dt) for `spec`, both on `now_ist`'s own calendar date and in
    `now_ist`'s own tzinfo (whatever that is - this function imposes no timezone of its own).
    A start is valid only for `scheduled_dt <= now < deadline_dt`; the deadline itself is
    already past the window. The ONE place this arithmetic is defined - `runner.py` (enforcing
    it) and `status.py` (reporting PENDING/WAITING/MISSED) both call this rather than each
    computing their own copy."""
    hour, minute = (int(part) for part in spec.scheduled_time.split(":"))
    scheduled_dt = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    deadline_dt = scheduled_dt + dt.timedelta(minutes=EXECUTION_WINDOW_MINUTES)
    return scheduled_dt, deadline_dt


__all__ = ["OUT_SUBDIR", "LOCK_WAIT_SECONDS", "LOCK_POLL_SECONDS", "TASK_SCHEDULER_FOLDER",
          "EXECUTION_TIME_LIMIT", "EXECUTION_WINDOW_MINUTES", "JOB_REGISTRY", "shadow_out_dir",
          "repo_root", "execution_window"]

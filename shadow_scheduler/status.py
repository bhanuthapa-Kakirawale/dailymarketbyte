"""shadow_scheduler.status: read-only aggregation of the 4 jobs' last runner outcome plus a
field-extraction-only read of the business package's own `latest.json` (PK-D's
`production_runs/{PRE,POST}/latest.json`, intelligence_refresh's own `latest.json`) - never
recomputed, never re-derived. Structurally read-only: this file contains no write-mode
`open()` call and no `os.replace()` anywhere - `tests/test_shadow_scheduler_security.py`
asserts this statically, not just behaviorally, which is why `records.py` (which writes) and
`status.py` (which never does) stay separate files rather than merged.

MISSED/PENDING/NOT_EXPECTED/WAITING reuses the existing `core.trading_calendar.SessionCalendar`
- never a second calendar implementation - plus `config.execution_window` (PK-E follow-up,
2026-10-12) for the 1-hour per-job window: WAITING means "inside the window, no run yet today",
distinct from MISSED ("the deadline has passed with no run today"). `now`/`calendar` are
always injected parameters, never a bare `datetime.now()` call, so every decision here is
deterministic and testable without a real wall clock. A job's own `runner_status` (including
EXPIRED - invoked at/after its deadline, child never launched) always takes display precedence
over `missed_state` once a run record exists for today; see `render_human`.

TODAY vs HISTORICAL (PK-E follow-up, 2026-10-16): `runner_status`/`business_status` in a
`job_status()` row are each `None` unless that record's OWN `started_at` (business falls back to
`completed_at`) converts to TODAY's IST calendar date - never simply the most recent record from
any prior day. A job that last ran yesterday evening shows as `PENDING`/`WAITING`/`MISSED` this
morning, not as yesterday's `COMPLETED`. The raw, undated historical value stays available under
`last_run` for a separate "most recent result" display; it must never be read as today's
operational state or folded into `overall_status()`'s counters.

Task-registration drift (a task missing/disabled/pointing at the wrong path) is a SEPARATE
signal, surfaced only by `scripts\\check_shadow_tasks.ps1` - this module never shells out to
PowerShell or a Task Scheduler API, keeping it read-only in the strictest sense. "Automation
health" is the union of this command and that script, not one combined signal.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from . import config, records
from .models import (ATTENTION, BROKEN, HEALTHY, JOBS, MISSED, NOT_EXPECTED, OK, PENDING,
                     UNKNOWN, WAITING)

# Business-side statuses that mean "needs a look" - shared across PK-D's vocabulary
# (SUCCESS/DEGRADED/BLOCKED/FAILED/SKIPPED/ALREADY_COMPLETED/LOCKED) and intelligence_refresh's
# (SUCCESS/DEGRADED/BLOCKED/FAILED/LOCKED). FAILED/LOCKED on the business side are read the
# same way - genuinely worth a look, but still a business outcome, not scheduler infra - so
# they are ATTENTION here too, never BROKEN (BROKEN is reserved for THIS layer's own infra
# failures).
_BUSINESS_ATTENTION_STATUSES = frozenset({"BLOCKED", "FAILED", "LOCKED"})

# Runner statuses that mean "needs a look" at the SCHEDULER layer (never BROKEN - nothing
# crashed). DEPENDENCY_FAILED joins LOCKED/EXPIRED here: the upstream intelligence job did not
# reach a usable outcome today, which is an operational/business-adjacent concern, not an infra
# crash. WAITING_DEPENDENCY is deliberately excluded - an in-flight wait is not itself unhealthy,
# the same treatment the ordinary RUNNING checkpoint already gets.
_RUNNER_ATTENTION_STATUSES = frozenset({"LOCKED", "EXPIRED", "DEPENDENCY_FAILED"})


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _parse_iso(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _ist_dt(value) -> dt.datetime | None:
    """`value` is a UTC ISO-8601 string (every run record / business latest.json in this repo
    stores `started_at`/`completed_at` that way); returns it converted to IST, or `None` if
    `value` is missing/unparsable. The ONE place this conversion happens, so `_missed_state`,
    `job_status` and `render_human` can never disagree on what "today" means for a given
    timestamp."""
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    import config as root_config
    return parsed.astimezone(root_config.IST)


def _missed_state(job_spec, scheduler_latest: dict | None, now_ist: dt.datetime,
                  calendar) -> str:
    today = now_ist.date()
    is_session = calendar.is_session(today)
    if is_session is None:
        return UNKNOWN
    if is_session is False:
        return NOT_EXPECTED

    scheduled_dt, deadline_dt = config.execution_window(job_spec, now_ist)
    ran_today = False
    if scheduler_latest:
        started_ist = _ist_dt(scheduler_latest.get("started_at"))
        ran_today = started_ist is not None and started_ist.date() == today
    if now_ist < scheduled_dt:
        return PENDING
    if ran_today:
        return OK
    # Still inside the 1-hour execution window with no run yet today - NOT a miss. Only
    # at/after the deadline, with still no run, does this become MISSED.
    if now_ist < deadline_dt:
        return WAITING
    return MISSED


def job_status(out_dir: str, business_root: str, job_spec, now_ist: dt.datetime,
               calendar) -> dict:
    today = now_ist.date()
    scheduler_latest = records.read_latest(out_dir, job_spec.job) or {}
    business_latest = _read_json(os.path.join(business_root, job_spec.business_latest_relpath)) or {}

    sched_started_ist = _ist_dt(scheduler_latest.get("started_at"))
    sched_date = sched_started_ist.date() if sched_started_ist else None
    sched_is_today = sched_date == today

    business_status_raw = (business_latest.get("orchestrator_status")
                           or business_latest.get("status"))
    business_ts_ist = (_ist_dt(business_latest.get("started_at"))
                      or _ist_dt(business_latest.get("completed_at")))
    business_date = business_ts_ist.date() if business_ts_ist else None
    # intelligence_am and intelligence_pm share ONE intelligence_refresh/latest.json (one
    # module, invoked on two schedules) - a bare "business file's own date is today" is not
    # enough: it would attribute the morning run's SUCCESS to the evening row before the
    # evening run has even happened. Requiring THIS job's own scheduler record to also be
    # from today ties the shown business result to this job's own today's invocation.
    business_is_today = business_date == today and sched_is_today

    return {
        "job": job_spec.job,
        "task_name": job_spec.task_name,
        "scheduled_time": job_spec.scheduled_time,
        "runner_status": scheduler_latest.get("runner_status") if sched_is_today else None,
        "child_exit_code": scheduler_latest.get("child_exit_code") if sched_is_today else None,
        "run_id": scheduler_latest.get("run_id") if sched_is_today else None,
        "ran_at": sched_started_ist.strftime("%H:%M") if sched_is_today else None,
        "business_status": business_status_raw if business_is_today else None,
        "missed_state": _missed_state(job_spec, scheduler_latest, now_ist, calendar),
        "dependency_job": scheduler_latest.get("dependency_job") if sched_is_today else None,
        "dependency_status": scheduler_latest.get("dependency_status") if sched_is_today else None,
        "dependency_wait_seconds": (scheduler_latest.get("dependency_wait_seconds")
                                    if sched_is_today else None),
        # Historical/latest business result - always the raw value, whatever day it's from.
        # Never used for today's operational state or `overall_status()`'s counters.
        "last_run": {
            "date": sched_date.isoformat() if sched_date else None,
            "runner_status": scheduler_latest.get("runner_status"),
            "business_status": business_status_raw,
        },
    }


def overall_status(out_dir: str | None = None, *, business_root: str | None = None,
                   now: dt.datetime | None = None, calendar=None) -> dict:
    """Read-only: no network call, no DB write, no render, no Task Scheduler mutation."""
    out_dir = out_dir or config.shadow_out_dir()
    if business_root is None:
        import config as root_config
        business_root = root_config.OUT_DIR
    if now is None:
        import config as root_config
        now = dt.datetime.now(root_config.IST)
    if calendar is None:
        from core.trading_calendar import SessionCalendar
        calendar = SessionCalendar()

    jobs = {}
    overall = HEALTHY
    for job in JOBS:
        spec = config.JOB_REGISTRY[job]
        row = job_status(out_dir, business_root, spec, now, calendar)
        jobs[job] = row
        if row["runner_status"] == "FAILED":
            overall = BROKEN
        elif overall != BROKEN and (row["missed_state"] == MISSED
                                    or row["runner_status"] in _RUNNER_ATTENTION_STATUSES
                                    or row["business_status"] in _BUSINESS_ATTENTION_STATUSES):
            overall = ATTENTION

    return {"jobs": jobs, "overall": overall, "generated_at": now.isoformat()}


_JOB_LABELS = {"intelligence_am": "Intelligence AM", "pre": "PRE",
              "intelligence_pm": "Intelligence PM", "post": "POST"}


def render_human(status: dict) -> str:
    """All counters and the state word shown per job come from TODAY-filtered fields only
    (see `job_status`) - a prior day's terminal record can never inflate these or make a
    not-yet-due job look COMPLETED. See `docs/SHADOW_PRODUCTION_OPERATIONS.md`."""
    lines = ["DMB SHADOW OPERATIONS", "-" * 62]
    missed = 0
    locked = 0
    expired = 0
    dependency_failed = 0
    for job in JOBS:
        row = status["jobs"][job]
        label = _JOB_LABELS.get(job, job)
        shown = row["runner_status"] or row["missed_state"]
        ran = row["ran_at"] or "-"
        business = f"  {row['business_status']}" if row["business_status"] else ""
        lines.append(f"{label:<18} {shown:<11} sched={row['scheduled_time']} ran={ran}{business}")
        if row["missed_state"] == MISSED:
            missed += 1
        if row["runner_status"] == "LOCKED":
            locked += 1
        if row["runner_status"] == "EXPIRED":
            expired += 1
        if row["runner_status"] == "DEPENDENCY_FAILED":
            dependency_failed += 1
    lines.append("-" * 62)
    lines.append(f"Missed tasks:        {missed}")
    lines.append(f"Lock conflicts:      {locked}")
    lines.append(f"Expired windows:     {expired}")
    lines.append(f"Dependency failures: {dependency_failed}")
    lines.append("")
    lines.append("AUTOMATION HEALTH:")
    lines.append(status["overall"])
    lines.append("-" * 62)
    return "\n".join(lines)


__all__ = ["job_status", "overall_status", "render_human"]

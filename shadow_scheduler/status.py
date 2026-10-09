"""shadow_scheduler.status: read-only aggregation of the 4 jobs' last runner outcome plus a
field-extraction-only read of the business package's own `latest.json` (PK-D's
`production_runs/{PRE,POST}/latest.json`, intelligence_refresh's own `latest.json`) - never
recomputed, never re-derived. Structurally read-only: this file contains no write-mode
`open()` call and no `os.replace()` anywhere - `tests/test_shadow_scheduler_security.py`
asserts this statically, not just behaviorally, which is why `records.py` (which writes) and
`status.py` (which never does) stay separate files rather than merged.

MISSED/PENDING/NOT_EXPECTED reuses the existing `core.trading_calendar.SessionCalendar` -
never a second calendar implementation. `now`/`calendar` are always injected parameters,
never a bare `datetime.now()` call, so every decision here is deterministic and testable
without a real wall clock.

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
from .models import ATTENTION, BROKEN, HEALTHY, JOBS, MISSED, NOT_EXPECTED, OK, PENDING, UNKNOWN

# Business-side statuses that mean "needs a look" - shared across PK-D's vocabulary
# (SUCCESS/DEGRADED/BLOCKED/FAILED/SKIPPED/ALREADY_COMPLETED/LOCKED) and intelligence_refresh's
# (SUCCESS/DEGRADED/BLOCKED/FAILED/LOCKED). FAILED/LOCKED on the business side are read the
# same way - genuinely worth a look, but still a business outcome, not scheduler infra - so
# they are ATTENTION here too, never BROKEN (BROKEN is reserved for THIS layer's own infra
# failures).
_BUSINESS_ATTENTION_STATUSES = frozenset({"BLOCKED", "FAILED", "LOCKED"})


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


def _missed_state(job_spec, scheduler_latest: dict | None, now_ist: dt.datetime,
                  calendar) -> str:
    today = now_ist.date()
    is_session = calendar.is_session(today)
    if is_session is None:
        return UNKNOWN
    if is_session is False:
        return NOT_EXPECTED

    scheduled_h, scheduled_m = (int(x) for x in job_spec.scheduled_time.split(":"))
    scheduled_dt = now_ist.replace(hour=scheduled_h, minute=scheduled_m, second=0,
                                   microsecond=0)
    ran_today = False
    if scheduler_latest:
        started = _parse_iso(scheduler_latest.get("started_at"))
        if started is not None:
            import config as root_config
            started_ist = started.astimezone(root_config.IST)
            ran_today = started_ist.date() == today
    if now_ist < scheduled_dt:
        return PENDING
    return OK if ran_today else MISSED


def job_status(out_dir: str, business_root: str, job_spec, now_ist: dt.datetime,
               calendar) -> dict:
    scheduler_latest = records.read_latest(out_dir, job_spec.job)
    business_latest = _read_json(os.path.join(business_root, job_spec.business_latest_relpath))
    business_status = None
    if business_latest:
        business_status = (business_latest.get("orchestrator_status")
                           or business_latest.get("status"))
    return {
        "job": job_spec.job,
        "task_name": job_spec.task_name,
        "scheduled_time": job_spec.scheduled_time,
        "runner_status": (scheduler_latest or {}).get("runner_status"),
        "child_exit_code": (scheduler_latest or {}).get("child_exit_code"),
        "run_id": (scheduler_latest or {}).get("run_id"),
        "business_status": business_status,
        "missed_state": _missed_state(job_spec, scheduler_latest, now_ist, calendar),
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
                                    or row["runner_status"] == "LOCKED"
                                    or row["business_status"] in _BUSINESS_ATTENTION_STATUSES):
            overall = ATTENTION

    return {"jobs": jobs, "overall": overall, "generated_at": now.isoformat()}


_JOB_LABELS = {"intelligence_am": "Intelligence AM", "pre": "PRE",
              "intelligence_pm": "Intelligence PM", "post": "POST"}


def render_human(status: dict) -> str:
    lines = ["DMB SHADOW OPERATIONS", "-" * 50]
    missed = 0
    locked = 0
    for job in JOBS:
        row = status["jobs"][job]
        label = _JOB_LABELS.get(job, job)
        shown = row["runner_status"] or row["missed_state"]
        lines.append(f"{label:<18} {shown:<10} {row['scheduled_time']}  "
                     f"business={row['business_status'] or '-'}")
        if row["missed_state"] == MISSED:
            missed += 1
        if row["runner_status"] == "LOCKED":
            locked += 1
    lines.append("")
    lines.append(f"Missed tasks:      {missed}")
    lines.append(f"Lock conflicts:    {locked}")
    lines.append("")
    lines.append("AUTOMATION HEALTH:")
    lines.append(status["overall"])
    lines.append("-" * 50)
    return "\n".join(lines)


__all__ = ["job_status", "overall_status", "render_human"]

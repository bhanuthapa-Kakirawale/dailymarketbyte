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

OCCURRENCE IDENTITY + ORPHANS (PK-OPS-1, 2026-10-24): "today's run" is now a record ATTRIBUTED
to today's scheduled occurrence (`config.resolve_occurrence` - stored `scheduled_date`, or the
same rule applied to a legacy record's `started_at`) whose invocation was IN_WINDOW. A record
that merely STARTED today is not enough: last night's 19:30 POST fired at 06:48 belongs to last
night (LATE -> EXPIRED), and a 19:14 manual PM run is EARLY - neither is ever counted as today's
scheduled execution. Both are listed per job under `rejected_invocations` (pushes ATTENTION). A
legacy record the OLD runner actually executed out-of-window (pre-fix 06:48 / Saturday 08:29
launches) is listed there as `OUT_OF_WINDOW_EXECUTION`. A non-terminal checkpoint whose runner
is verifiably gone is shown as `ORPHAN_SUSPECTED` (or `ABANDONED` once reconciled) - never as a
healthy RUNNING - and every unresolved non-terminal record on disk is listed under
`orphan_records` (ATTENTION until `python -m shadow_scheduler reconcile --execute` records it).
`previous_occurrence` shows the most recent closed window's outcome for information only - like
`last_run`, it never feeds today's counters.

Task-registration drift (a task missing/disabled/pointing at the wrong path) is a SEPARATE
signal, surfaced only by `scripts\\check_shadow_tasks.ps1` - this module never shells out to
PowerShell or a Task Scheduler API, keeping it read-only in the strictest sense. "Automation
health" is the union of this command and that script, not one combined signal.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from . import config, orphans, records
from .models import (ATTENTION, BROKEN, EARLY, EXPIRED, HEALTHY, JOBS, MISSED, NOT_EXPECTED, OK,
                     PENDING, TERMINAL_STATUSES, UNKNOWN, WAITING)

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
_RUNNER_ATTENTION_STATUSES = frozenset({"LOCKED", "EXPIRED", "DEPENDENCY_FAILED", "ABANDONED",
                                        orphans.ORPHAN_SUSPECTED})

OUT_OF_WINDOW_EXECUTION = "OUT_OF_WINDOW_EXECUTION"
OUT_OF_WINDOW_ATTEMPT = "OUT_OF_WINDOW_ATTEMPT"


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


def _not_after(rec: dict, now_ist: dt.datetime | None) -> bool:
    """Point-in-time: a record invoked after `now` does not exist yet as far as `now` is
    concerned (keeps a status taken at a past `now` - e.g. an incident replay - honest)."""
    if now_ist is None:
        return True
    started = _ist_dt(rec.get("started_at"))
    return started is None or started <= now_ist


def _in_window_records(out_dir: str, job: str, scheduled_date: dt.date,
                       now_ist: dt.datetime | None = None) -> list:
    """The records that ARE this occurrence's scheduled execution attempts (IN_WINDOW only)."""
    return [r for r in orphans.occurrence_records(out_dir, job, scheduled_date)
            if r["_attribution"]["invocation"] == config.IN_WINDOW and _not_after(r, now_ist)]


def _missed_state(job_spec, scheduler_latest: dict | None, now_ist: dt.datetime,
                  calendar, *, ran_in_window: bool | None = None) -> str:
    today = now_ist.date()
    is_session = calendar.is_session(today)
    if is_session is None:
        return UNKNOWN
    if is_session is False:
        return NOT_EXPECTED

    scheduled_dt, deadline_dt = config.execution_window(job_spec, now_ist)
    if ran_in_window is None:   # legacy callers: the latest pointer only
        ran_in_window = False
        if scheduler_latest:
            started_ist = _ist_dt(scheduler_latest.get("started_at"))
            attr = orphans.attribution(dict(scheduler_latest, job=job_spec.job))
            ran_in_window = (started_ist is not None and started_ist.date() == today
                             and attr["scheduled_date"] == today.isoformat()
                             and attr["invocation"] == config.IN_WINDOW)
    if now_ist < scheduled_dt:
        return PENDING
    if ran_in_window:
        return OK
    # Still inside the 1-hour execution window with no run yet today - NOT a miss. Only
    # at/after the deadline, with still no run, does this become MISSED.
    if now_ist < deadline_dt:
        return WAITING
    return MISSED


def _rejected_invocations(out_dir: str, job: str, now_ist: dt.datetime) -> list:
    """Records INVOKED today (IST), up to `now`, outside any window - whatever occurrence they
    belong to. `kind`: EARLY / EXPIRED (correctly rejected); OUT_OF_WINDOW_ATTEMPT (a pre-fix
    runner got as far as a checkpoint - no child provably ran); OUT_OF_WINDOW_EXECUTION (a
    pre-fix runner launched the child)."""
    today = now_ist.date()
    out = []
    for rec in records.list_records(out_dir):
        if rec.get("job") != job:
            continue
        started_ist = _ist_dt(rec.get("started_at"))
        if started_ist is None or started_ist.date() != today or started_ist > now_ist:
            continue
        attr = orphans.attribution(rec)
        if attr["invocation"] not in (config.EARLY, config.LATE):
            continue
        status = orphans.effective_status(out_dir, rec)
        if status in (EARLY, EXPIRED):
            kind = status
        elif status not in TERMINAL_STATUSES or status == "ABANDONED" or status == \
                orphans.ORPHAN_SUSPECTED:
            kind = OUT_OF_WINDOW_ATTEMPT
        else:
            kind = OUT_OF_WINDOW_EXECUTION
        out.append({"run_id": rec.get("run_id"), "invoked_at": started_ist.strftime("%H:%M"),
                    "scheduled_date": attr["scheduled_date"], "invocation": attr["invocation"],
                    "runner_status": status, "kind": kind,
                    "child_started": rec.get("child_started", True)})
    return out


def _previous_occurrence(out_dir: str, job_spec, now_ist: dt.datetime, calendar) -> dict | None:
    """The most recent occurrence whose window has CLOSED - information only."""
    probe_at = now_ist
    occ = config.resolve_occurrence(job_spec, probe_at)
    if occ.invocation != config.LATE:
        # inside today's window, or before it: step back to just before that window opens
        probe_at = occ.window_start - dt.timedelta(minutes=config.EARLY_ATTRIBUTION_MINUTES,
                                                   seconds=1)
        occ = config.resolve_occurrence(job_spec, probe_at)
    if occ.invocation != config.LATE:   # pragma: no cover - defensive
        return None
    is_session = calendar.is_session(occ.scheduled_date)
    attempts = _in_window_records(out_dir, job_spec.job, occ.scheduled_date, now_ist)
    primary = attempts[-1] if attempts else None
    if is_session is False:
        state = NOT_EXPECTED
    elif primary is not None:
        state = OK
    elif is_session is None:
        state = UNKNOWN
    else:
        state = MISSED
    return {"scheduled_date": occ.scheduled_date.isoformat(), "state": state,
            "runner_status": orphans.effective_status(out_dir, primary) if primary else None,
            "run_id": primary.get("run_id") if primary else None}


def job_status(out_dir: str, business_root: str, job_spec, now_ist: dt.datetime,
               calendar) -> dict:
    today = now_ist.date()
    scheduler_latest = records.read_latest(out_dir, job_spec.job) or {}
    business_latest = _read_json(os.path.join(business_root, job_spec.business_latest_relpath)) or {}

    # Raw "most recent invocation" (last_run) - from the latest pointer, any day, any class.
    latest_started_ist = _ist_dt(scheduler_latest.get("started_at"))
    latest_date = latest_started_ist.date() if latest_started_ist else None

    # TODAY's scheduled execution: an IN_WINDOW record attributed to today's occurrence.
    attempts = _in_window_records(out_dir, job_spec.job, today, now_ist)
    today_record = attempts[-1] if attempts else None
    sched_is_today = today_record is not None
    if sched_is_today:
        sched_started_ist = _ist_dt(today_record.get("started_at"))
        effective = orphans.effective_status(out_dir, today_record)
        reconciliation = (records.read_reconciliation(out_dir, today_record["run_id"])
                          if today_record.get("run_id") else None)
    else:
        sched_started_ist, effective, reconciliation = None, None, None
    current = today_record or {}

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
    business_is_today = (business_date == today and sched_is_today
                         and current.get("child_started", True) is not False)

    return {
        "job": job_spec.job,
        "task_name": job_spec.task_name,
        "scheduled_time": job_spec.scheduled_time,
        "scheduled_date": today.isoformat(),
        "runner_status": effective,
        "recorded_runner_status": current.get("runner_status") if sched_is_today else None,
        "reconciled": bool(reconciliation),
        "child_exit_code": current.get("child_exit_code") if sched_is_today else None,
        "run_id": current.get("run_id") if sched_is_today else None,
        "ran_at": (sched_started_ist.strftime("%H:%M")
                   if sched_is_today and sched_started_ist else None),
        "business_status": business_status_raw if business_is_today else None,
        "missed_state": _missed_state(job_spec, scheduler_latest, now_ist, calendar,
                                      ran_in_window=sched_is_today),
        "dependency_job": current.get("dependency_job") if sched_is_today else None,
        "dependency_status": current.get("dependency_status") if sched_is_today else None,
        "dependency_wait_seconds": (current.get("dependency_wait_seconds")
                                    if sched_is_today else None),
        "rejected_invocations": _rejected_invocations(out_dir, job_spec.job, now_ist),
        "previous_occurrence": _previous_occurrence(out_dir, job_spec, now_ist, calendar),
        # Historical/latest result - always the raw value, whatever day/occurrence it's from.
        # Never used for today's operational state or `overall_status()`'s counters.
        "last_run": {
            "date": latest_date.isoformat() if latest_date else None,
            "runner_status": scheduler_latest.get("runner_status"),
            "business_status": business_status_raw,
        },
    }


def _is_stale(verdict: dict, now: dt.datetime) -> bool:
    """An UNVERIFIABLE checkpoint is only flagged once it is older than the orphan module's own
    quiet threshold - a fresh in-flight record that merely can't be probed is not an orphan."""
    started = _parse_iso(verdict.get("started_at"))
    if started is None:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    return (now - started).total_seconds() >= orphans.LEGACY_QUIET_SECONDS


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
        # An infra launch failure is BROKEN whichever occurrence it is attributed to.
        if row["runner_status"] == "FAILED" or any(
                r["runner_status"] == "FAILED" for r in row["rejected_invocations"]):
            overall = BROKEN
        elif overall != BROKEN and (row["missed_state"] == MISSED
                                    or row["runner_status"] in _RUNNER_ATTENTION_STATUSES
                                    or row["business_status"] in _BUSINESS_ATTENTION_STATUSES
                                    or row["rejected_invocations"]):
            overall = ATTENTION

    # Every non-terminal checkpoint on disk that is not provably still running - an orphan is
    # never reported as healthy, whatever day it is from, until it is reconciled.
    orphan_records = [
        {k: v for k, v in o.items() if k in ("run_id", "job", "runner_status", "started_at",
                                              "classification", "reasons")}
        for o in orphans.scan(out_dir, business_root=business_root)
        if o["classification"] == orphans.ABANDONED
        or (o["classification"] == orphans.UNVERIFIABLE and _is_stale(o, now))]
    if orphan_records and overall != BROKEN:
        overall = ATTENTION

    return {"jobs": jobs, "overall": overall, "generated_at": now.isoformat(),
            "orphan_records": orphan_records}


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
    early = 0
    late = 0
    abandoned = 0
    child_nonzero = 0
    for job in JOBS:
        row = status["jobs"][job]
        label = _JOB_LABELS.get(job, job)
        shown = row["runner_status"] or row["missed_state"]
        ran = row["ran_at"] or "-"
        business = f"  {row['business_status']}" if row["business_status"] else ""
        lines.append(f"{label:<18} {shown:<11} sched={row['scheduled_time']} ran={ran}{business}")
        for rej in row.get("rejected_invocations") or []:
            what = {"EARLY": "rejected EARLY", "EXPIRED": "rejected LATE (EXPIRED)",
                    OUT_OF_WINDOW_ATTEMPT: f"out-of-window attempt by the pre-fix runner "
                                           f"({rej['runner_status']}, no child provably ran)",
                    OUT_OF_WINDOW_EXECUTION: "EXECUTED OUT OF WINDOW (pre-fix runner)"}[rej["kind"]]
            lines.append(f"{'':<18} - invoked {rej['invoked_at']} for the "
                         f"{rej['scheduled_date']} occurrence: {what}")
            if rej["kind"] == "EARLY":
                early += 1
            else:
                late += 1
        prev = row.get("previous_occurrence")
        if prev and prev["state"] == MISSED:
            lines.append(f"{'':<18} - previous occurrence {prev['scheduled_date']}: MISSED "
                         f"(no in-window run; information only)")
        if row["missed_state"] == MISSED:
            missed += 1
        if row["runner_status"] == "LOCKED":
            locked += 1
        if row["runner_status"] == "EXPIRED":
            expired += 1
        if row["runner_status"] == "DEPENDENCY_FAILED":
            dependency_failed += 1
        if row["runner_status"] in ("ABANDONED", orphans.ORPHAN_SUSPECTED):
            abandoned += 1
        if row["runner_status"] == "CHILD_NONZERO":
            child_nonzero += 1
    orphan_records = status.get("orphan_records") or []
    lines.append("-" * 62)
    lines.append(f"Missed tasks:        {missed}")
    lines.append(f"Lock conflicts:      {locked}")
    lines.append(f"Expired windows:     {expired}")
    lines.append(f"Dependency failures: {dependency_failed}")
    lines.append(f"Child nonzero:       {child_nonzero}")
    lines.append(f"Abandoned today:     {abandoned}")
    lines.append(f"Early rejections:    {early}")
    lines.append(f"Late rejections:     {late}")
    lines.append(f"Unresolved orphans:  {len(orphan_records)}")
    for o in orphan_records:
        lines.append(f"  {o['run_id']} {o['job']} {o['runner_status']} -> {o['classification']}")
    if orphan_records:
        lines.append("  (python -m shadow_scheduler reconcile  - dry run; --execute to record)")
    lines.append("")
    lines.append("AUTOMATION HEALTH:")
    lines.append(status["overall"])
    lines.append("-" * 62)
    return "\n".join(lines)


__all__ = ["job_status", "overall_status", "render_human"]

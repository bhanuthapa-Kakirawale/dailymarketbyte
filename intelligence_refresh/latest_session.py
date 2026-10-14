"""Catch up the latest final session: the one lever that exists for Institutional Flow,
Market Events, official snapshots, the canonical report itself and the derived intelligence
snapshot, all of which only ever expose "fetch latest/current" (registry.LATEST_SESSION_ONLY).

This never reimplements REPORT - it calls `products.report_job.run_report_job` in-process,
exactly the function `run_evening.bat`/`run_evening_full.bat` already call.

`run_report_job` itself always re-attempts official/institutional/market-events capture even
when the canonical report already exists (report_job.py's own capture calls are not gated on
"already built"), so the "already ran today" check here is load-bearing for idempotency, not
optional: without it, a second same-day `refresh` would re-trigger live network captures every
time.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

SUCCEEDED_STATUSES = ("SUCCESS", "DEGRADED")


def _report_job_records(out_dir: str, session: dt.date) -> list:
    folder = os.path.join(out_dir, "report_jobs", session.isoformat())
    records = []
    for path in sorted(glob.glob(os.path.join(folder, "report_job_*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                records.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return records


def inspect_latest(out_dir: str, now: dt.datetime, calendar=None) -> dict:
    """Read-only: has the latest final session already been successfully caught up today?"""
    from operations.sessions import latest_final_session
    session = latest_final_session(now, calendar)
    if session is None:
        return {"session": None, "already_run": False, "reason": "SESSION_UNKNOWN"}
    records = _report_job_records(out_dir, session)
    succeeded = [r for r in records if r.get("run_status") in SUCCEEDED_STATUSES]
    return {"session": session.isoformat(), "already_run": bool(succeeded),
           "records_found": len(records),
           "last_run_status": records[-1].get("run_status") if records else None}


def _canonical_report_status(out_dir: str, session: dt.date) -> dict:
    """The TRUE end-state for `session`'s canonical report, independently re-verified against
    the artifact itself - never trusts a report_job run-record file alone (a record can say
    SUCCESS while the artifact it describes was never actually persisted or has since been
    lost)."""
    from readiness.evidence import open_history
    from operations.report_lookup import find_canonical_report
    entry = {"expected_session": session.isoformat()}
    try:
        history = open_history(out_dir)
    except FileNotFoundError:
        entry.update(status="MISSING", report_id=None, path=None,
                     reason="no history database yet")
        return entry
    except Exception as exc:
        entry.update(status="HISTORY_UNAVAILABLE", report_id=None, path=None,
                     reason=f"{type(exc).__name__}: {exc}")
        return entry
    try:
        lookup = find_canonical_report(session, history=history)
    finally:
        history.close()
    entry.update(status="COMPLETE" if lookup.found else lookup.status,
                report_id=lookup.report_id, path=lookup.path, reason=lookup.reason)
    return entry


def ensure_latest(out_dir: str, now: dt.datetime, *, dry_run: bool = False,
                  report_job_fn=None, calendar=None) -> dict:
    """Ensure the latest final session has a successful REPORT job run. Never calls
    `report_job_fn` when `dry_run` is True, or when a session can't be resolved, or when a
    successful run already exists for it today AND the canonical report artifact it describes
    genuinely exists (a stale/orphaned run-record whose artifact is missing still triggers a
    rebuild attempt - safe and idempotent, since `run_report_job` itself no-ops when a valid
    report already exists)."""
    inspected = inspect_latest(out_dir, now, calendar)
    if inspected["session"] is None:
        return {"action": "SKIPPED", "reason": inspected["reason"], "session": None,
                "canonical_report": {"expected_session": None, "status": "UNKNOWN"}}

    session = dt.date.fromisoformat(inspected["session"])
    canonical = _canonical_report_status(out_dir, session)

    if inspected["already_run"] and canonical["status"] == "COMPLETE":
        return {"action": "SKIPPED", "reason": "ALREADY_RUN", "session": inspected["session"],
                "report_job_run_status": inspected["last_run_status"],
                "canonical_report": canonical}

    if dry_run:
        return {"action": "WOULD_RUN", "session": inspected["session"],
                "reason": "last_run_status=" + str(inspected["last_run_status"]),
                "canonical_report": canonical}

    report_job_fn = report_job_fn or _default_report_job_fn
    exception_info = None
    try:
        record = report_job_fn(now=now, calendar=calendar)
    except Exception as exc:
        # run_report_job already wrote its own FAILED/EXCEPTION record before re-raising
        # (products/report_job.py) - recover it so one transient failure degrades this run
        # instead of crashing the whole refresh (and skipping the unrelated 30-day reconciler).
        records = _report_job_records(out_dir, session)
        record = dict(records[-1]) if records else {}
        record.setdefault("run_status", "FAILED")
        record.setdefault("target_session", inspected["session"])
        exception_info = f"{type(exc).__name__}: {exc}"

    result = {"action": "RAN", "session": inspected["session"],
             "report_job_run_status": record.get("run_status"),
             "report_job_record_path": _record_relpath(out_dir, record),
             "canonical_report": _canonical_report_status(out_dir, session)}
    if inspected["already_run"]:
        result["rebuilt_stale_record"] = True
    if exception_info is not None:
        result["exception"] = exception_info
    return result


def _record_relpath(out_dir: str, record: dict) -> str | None:
    session = record.get("target_session")
    run_id = record.get("run_id")
    if not session or not run_id:
        return None
    rel = os.path.join("report_jobs", session, f"report_job_{run_id}.json")
    return rel.replace(os.sep, "/")


def _default_report_job_fn(*, now, calendar):
    from products.report_job import run_report_job
    return run_report_job(now=now, calendar=calendar)


__all__ = ["inspect_latest", "ensure_latest", "SUCCEEDED_STATUSES"]

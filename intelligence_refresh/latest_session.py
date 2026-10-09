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


def ensure_latest(out_dir: str, now: dt.datetime, *, dry_run: bool = False,
                  report_job_fn=None, calendar=None) -> dict:
    """Ensure the latest final session has a successful REPORT job run. Never calls
    `report_job_fn` when `dry_run` is True, or when a session can't be resolved, or when a
    successful run already exists for it today."""
    inspected = inspect_latest(out_dir, now, calendar)
    if inspected["session"] is None:
        return {"action": "SKIPPED", "reason": inspected["reason"], "session": None}
    if inspected["already_run"]:
        return {"action": "SKIPPED", "reason": "ALREADY_RUN", "session": inspected["session"],
                "report_job_run_status": inspected["last_run_status"]}
    if dry_run:
        return {"action": "WOULD_RUN", "session": inspected["session"],
                "reason": "last_run_status=" + str(inspected["last_run_status"])}

    report_job_fn = report_job_fn or _default_report_job_fn
    record = report_job_fn(now=now, calendar=calendar)
    return {"action": "RAN", "session": inspected["session"],
           "report_job_run_status": record.get("run_status"),
           "report_job_record_path": _record_relpath(out_dir, record)}


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

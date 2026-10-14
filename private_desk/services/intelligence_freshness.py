"""Data Quality panel: the latest `intelligence_refresh` run. Read-only JSON
(`output/intelligence_refresh/latest.json`) - the desk never triggers a refresh and never
mutates this state. No "run refresh" control (test-enforced): this module has no write path.
"""
from __future__ import annotations

import json
import os

from ..repository import DeskRepository


def intelligence_refresh_status(repo: DeskRepository) -> dict:
    path = repo.path("intelligence_refresh", "latest.json")
    if not os.path.exists(path):
        return {"status": "NO_RUN"}
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"status": "UNREADABLE", "error": str(exc)}
    lsr = d.get("latest_session_result") or {}
    cr_status = (lsr.get("canonical_report") or {}).get("status")
    if cr_status == "COMPLETE":
        display = "OK"
    elif cr_status in (None, "UNKNOWN"):
        display = "UNKNOWN"
    elif cr_status == "MISSING":
        display = "MISSING"
    else:
        # ARTIFACT_MISSING / UNREADABLE / SESSION_MISMATCH / HISTORY_UNAVAILABLE: a record/row
        # exists but is unusable - worse than merely missing.
        display = "FAILED"
    return {"status": d.get("orchestrator_status") or "UNKNOWN", "run_id": d.get("run_id"),
            "started_at": d.get("started_at"), "completed_at": d.get("completed_at"),
            "latest_session_result": lsr,
            "canonical_report": {"display": display,
                                 "expected_session": (lsr.get("canonical_report") or {}).get(
                                     "expected_session"),
                                 "raw_status": cr_status},
            "summary": d.get("summary") or {},
            "blocking": d.get("blocking_reasons") or [], "warnings": d.get("warnings") or [],
            "file": os.path.relpath(path, repo.out_dir)}


__all__ = ["intelligence_refresh_status"]

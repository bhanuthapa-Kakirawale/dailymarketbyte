"""PK-D panel: the latest PRE / POST production-orchestrator run. Read-only JSON
(`output/production_runs/{PRE,POST}/latest.json`) - the desk never triggers a run and never
mutates this state. No "run production" control (test-enforced): this module has no write path.
"""
from __future__ import annotations

import json
import os

from ..repository import DeskRepository

EDITIONS = ("PRE", "POST")


def production_orchestrator_status(repo: DeskRepository) -> list:
    out = []
    for edition in EDITIONS:
        path = repo.path("production_runs", edition, "latest.json")
        if not os.path.exists(path):
            out.append({"edition": edition, "status": "NO_RUN"})
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as exc:
            out.append({"edition": edition, "status": "UNREADABLE", "error": str(exc)})
            continue
        out.append({"edition": edition, "status": d.get("orchestrator_status") or "UNKNOWN",
                    "readiness_preflight": d.get("readiness_status_preflight"),
                    "readiness_post_render": d.get("readiness_status_post_render"),
                    "session": d.get("session_date"), "run_id": d.get("run_id"),
                    "started_at": d.get("started_at"), "completed_at": d.get("completed_at"),
                    "blocking": d.get("blocking_reasons") or [], "warnings": d.get("warnings") or [],
                    "file": os.path.relpath(path, repo.out_dir)})
    return out


__all__ = ["production_orchestrator_status"]

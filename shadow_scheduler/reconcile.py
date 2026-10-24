"""shadow_scheduler.reconcile: bounded, idempotent recovery of ABANDONED run records
(PK-OPS-1, 2026-10-24).

    python -m shadow_scheduler reconcile            DRY RUN (default): classify only, write nothing
    python -m shadow_scheduler reconcile --execute  write append-only markers for ABANDONED only

Contract:
- Only a record `orphans.classify` proves ABANDONED is ever reconciled - ACTIVE and
  UNVERIFIABLE records are listed and left alone, so a genuinely running job can never be
  rewritten. Each record is re-classified immediately before its marker is written.
- Append-only: the marker is `reconciliations/<run_id>.json`, exclusive-create. The original
  run file (its timestamps, run identity, RUNNING/WAITING_DEPENDENCY status and every evidence
  field) and the job's latest pointer are never rewritten; the marker stores the original
  record's SHA-256 so the evidence it was judged on is pinned.
- Idempotent: a second pass finds the marker and reports ALREADY_RECONCILED - never a
  duplicate, never an overwrite.
- Never a success: the effective status is ABANDONED, never COMPLETED; nothing is claimed about
  what the child did. A later manual replay is its own run record, never merged into this one.
- Never touches the global lock (a stale lock is reclaimed only by the next runner's own
  acquire()), never kills or signals a process, never launches anything.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import socket

from . import config, orphans, records
from .models import ABANDONED

DRY_RUN, EXECUTED = "DRY_RUN", "EXECUTED"
WOULD_RECONCILE, RECONCILED_NOW, ALREADY_RECONCILED, LEFT_ALONE = (
    "WOULD_RECONCILE", "RECONCILED", "ALREADY_RECONCILED", "LEFT_ALONE")


def _sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def reconcile(out_dir: str | None = None, *, execute: bool = False,
              business_root: str | None = None, now: dt.datetime | None = None,
              hostname: str | None = None, probe=None) -> dict:
    out_dir = out_dir or config.shadow_out_dir()
    if business_root is None:
        import config as root_config
        business_root = root_config.OUT_DIR
    now = now or dt.datetime.now(dt.timezone.utc)
    hostname = hostname or socket.gethostname()
    rows = []
    for verdict in orphans.scan(out_dir, now=now, hostname=hostname, probe=probe,
                                business_root=business_root):
        row = dict(verdict)
        if verdict["classification"] == orphans.RECONCILED:
            row["action"] = ALREADY_RECONCILED
        elif verdict["classification"] != orphans.ABANDONED:
            row["action"] = LEFT_ALONE
        elif not execute:
            row["action"] = WOULD_RECONCILE
        else:
            row["action"] = _reconcile_one(out_dir, verdict, now=now, hostname=hostname,
                                           probe=probe, business_root=business_root)
        rows.append(row)
    return {"mode": EXECUTED if execute else DRY_RUN, "generated_at": now.isoformat(),
            "hostname": hostname, "rows": rows,
            "counts": {a: sum(1 for r in rows if r["action"] == a)
                       for a in (WOULD_RECONCILE, RECONCILED_NOW, ALREADY_RECONCILED,
                                 LEFT_ALONE)}}


def _reconcile_one(out_dir, verdict, *, now, hostname, probe, business_root) -> str:
    run_id = verdict["run_id"]
    path = records.run_record_path(out_dir, run_id)
    original = records.read_record(path)
    if original is None:
        return LEFT_ALONE
    # Re-classify against the file as it is NOW - never act on a stale verdict.
    fresh = orphans.classify(original, out_dir=out_dir, now=now, hostname=hostname,
                             probe=probe, business_root=business_root)
    if fresh["classification"] == orphans.RECONCILED:
        return ALREADY_RECONCILED
    if fresh["classification"] != orphans.ABANDONED:
        return LEFT_ALONE
    marker = {
        "run_id": run_id, "job": original.get("job"),
        "effective_status": ABANDONED,
        "original_runner_status": original.get("runner_status"),
        "original_started_at": original.get("started_at"),
        "original_record_path": os.path.relpath(path, out_dir).replace(os.sep, "/"),
        "original_record_sha256": _sha256(path),
        "abandoned_kind": fresh.get("abandoned_kind"),
        "reasons": fresh["reasons"], "evidence": fresh["evidence"],
        "reconciled_at": now.isoformat(), "reconciled_by": {"hostname": hostname,
                                                            "pid": os.getpid()},
        "note": "Append-only reconciliation: the run file is unchanged. ABANDONED is never a "
                "success and says nothing about what the child did.",
    }
    return RECONCILED_NOW if records.write_reconciliation(out_dir, marker) else ALREADY_RECONCILED


def render_human(result: dict) -> str:
    lines = [f"DMB SHADOW SCHEDULER - ORPHAN RECONCILIATION ({result['mode']})", "-" * 62]
    if not result["rows"]:
        lines.append("No non-terminal run records.")
    for r in result["rows"]:
        attr = r["evidence"].get("attribution") or {}
        lines.append(f"{r['run_id']}  {r['job']:<16} {r['runner_status']:<19} "
                     f"-> {r['classification']:<12} [{r['action']}]")
        lines.append(f"    scheduled_date={attr.get('scheduled_date')} "
                     f"invocation={attr.get('invocation')} started_at={r['started_at']}")
        for reason in r["reasons"]:
            lines.append(f"    - {reason}")
    lines.append("-" * 62)
    for k, v in result["counts"].items():
        lines.append(f"{k:<20} {v}")
    if result["mode"] == DRY_RUN and result["counts"].get(WOULD_RECONCILE):
        lines.append("")
        lines.append("Dry run: nothing written. Re-run with --execute to write append-only "
                     "ABANDONED markers (run files are never rewritten).")
    return "\n".join(lines)


__all__ = ["DRY_RUN", "EXECUTED", "WOULD_RECONCILE", "RECONCILED_NOW", "ALREADY_RECONCILED",
          "LEFT_ALONE", "reconcile", "render_human"]

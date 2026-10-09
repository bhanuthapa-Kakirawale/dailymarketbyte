"""Reading/writing shadow_scheduler's OWN run records - never PK-D's or intelligence_refresh's
files (those are read-only, by path only, in status.py).

    output/shadow_scheduler/runs/<run_id>.json      immutable once terminal
    output/shadow_scheduler/latest/<job>.json       mutable pointer, one per job

Write order is correctness-critical, same rule as every other package in this repo: the
immutable run file is written FIRST (tmp + os.replace), THEN the job's latest.json pointer is
updated - never the reverse, so a reader of latest.json never points at a nonexistent file.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import uuid

from .models import RunRecord

RUNS_SUBDIR = "runs"
LATEST_SUBDIR = "latest"
LOGS_SUBDIR = "logs"


def new_run_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"{now:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


def runs_dir(out_dir: str) -> str:
    return os.path.join(out_dir, RUNS_SUBDIR)


def run_record_path(out_dir: str, run_id: str) -> str:
    return os.path.join(runs_dir(out_dir), f"{run_id}.json")


def latest_path(out_dir: str, job: str) -> str:
    return os.path.join(out_dir, LATEST_SUBDIR, f"{job}.json")


def log_dir(out_dir: str, ist_date: dt.date) -> str:
    return os.path.join(out_dir, LOGS_SUBDIR, ist_date.isoformat())


def log_path(out_dir: str, job: str, ist_started_at: dt.datetime) -> str:
    """Relative to `out_dir`, so the run record stays portable across machines/paths."""
    rel_dir = os.path.join(LOGS_SUBDIR, ist_started_at.date().isoformat())
    return os.path.join(rel_dir, f"{job}_{ist_started_at:%H%M%S}.log")


def _atomic_write(path: str, payload: dict, what: str) -> None:
    from operations.run_context import guard_write
    guard_write(path, what)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def write_record(out_dir: str, record: RunRecord) -> str:
    """Write the immutable run file, then the job's latest.json pointer. Returns the run
    file's path. Called once as a RUNNING checkpoint right after lock acquisition, and once
    more when the record becomes terminal - exactly like PK-D's own manifest pattern."""
    path = run_record_path(out_dir, record.run_id)
    _atomic_write(path, record.to_dict(), "shadow scheduler run record")
    write_latest(out_dir, record, path)
    return path


def write_latest(out_dir: str, record: RunRecord, path: str) -> str:
    rel = os.path.relpath(path, out_dir).replace(os.sep, "/")
    lpath = latest_path(out_dir, record.job)
    _atomic_write(lpath, record.to_latest_dict(rel), "shadow scheduler latest pointer")
    return lpath


def read_record(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_latest(out_dir: str, job: str) -> dict | None:
    return read_record(latest_path(out_dir, job))


def list_records(out_dir: str) -> list:
    """Best-effort: unreadable/corrupt files are skipped, never raised. Used by status.py
    only - never by runner.py (the runner only ever reads its own job's current state)."""
    out = []
    for p in sorted(glob.glob(os.path.join(runs_dir(out_dir), "*.json"))):
        d = read_record(p)
        if d is not None:
            out.append(d)
    return out


__all__ = ["RUNS_SUBDIR", "LATEST_SUBDIR", "LOGS_SUBDIR", "new_run_id", "runs_dir",
          "run_record_path", "latest_path", "log_dir", "log_path", "write_record",
          "write_latest", "read_record", "read_latest", "list_records"]

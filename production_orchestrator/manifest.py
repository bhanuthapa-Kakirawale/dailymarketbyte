"""Reading/writing the production-orchestrator run manifest to disk (PK-D).

    output/production_runs/<EDITION>/<session_date>/<run_id>.json   immutable once terminal
    output/production_runs/<EDITION>/latest.json                    mutable pointer, per edition

Write order is correctness-critical: the immutable run file is written FIRST (tmp + os.replace,
the same atomic pattern `readiness/report.py` uses), THEN `latest.json` is updated to point at
it - so `latest.json` never references a file that does not exist yet.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import subprocess
import uuid

from .models import RUNNING, RunManifest

RUNS_DIR = "production_runs"


def new_run_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"{now:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


def git_commit(base_dir: str) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=base_dir,
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def session_dir(out_dir: str, edition: str, session_date) -> str:
    return os.path.join(out_dir, RUNS_DIR, edition, str(session_date))


def run_path(out_dir: str, edition: str, session_date, run_id: str) -> str:
    return os.path.join(session_dir(out_dir, edition, session_date), f"{run_id}.json")


def latest_path(out_dir: str, edition: str) -> str:
    return os.path.join(out_dir, RUNS_DIR, edition, "latest.json")


def _atomic_write(path: str, payload: dict, what: str) -> None:
    from operations.run_context import guard_write
    guard_write(path, what)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def write_manifest(out_dir: str, manifest: RunManifest) -> str:
    """Write the immutable run file, then update latest.json. Returns the run file's path."""
    path = run_path(out_dir, manifest.edition, manifest.session_date, manifest.run_id)
    _atomic_write(path, manifest.to_dict(), "production run manifest")
    write_latest(out_dir, manifest, path)
    return path


def write_latest(out_dir: str, manifest: RunManifest, path: str) -> str:
    rel = os.path.relpath(path, out_dir).replace(os.sep, "/")
    lpath = latest_path(out_dir, manifest.edition)
    _atomic_write(lpath, manifest.to_latest_dict(rel), "production run latest pointer")
    return lpath


def read_latest(out_dir: str, edition: str) -> dict | None:
    path = latest_path(out_dir, edition)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def session_manifest_paths(out_dir: str, edition: str, session_date) -> list:
    """Every run manifest for (edition, session_date), oldest first by filename (run_id sorts
    chronologically - see new_run_id's UTC-stamp prefix)."""
    folder = session_dir(out_dir, edition, session_date)
    return sorted(glob.glob(os.path.join(folder, "*.json")))


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def running_manifest_to_resume(out_dir: str, edition: str, session_date) -> dict | None:
    """The newest manifest for (edition, session_date) still RUNNING, or None. A dict (not a
    RunManifest) - the caller only needs run_id/started_at/readiness verdicts to resume from."""
    best, best_path = None, None
    for path in session_manifest_paths(out_dir, edition, session_date):
        d = _load(path)
        if d and d.get("orchestrator_status") == RUNNING:
            if best is None or d.get("started_at", "") >= best.get("started_at", ""):
                best, best_path = d, path
    if best is not None:
        best["_path"] = best_path
    return best


__all__ = ["RUNS_DIR", "new_run_id", "git_commit", "session_dir", "run_path", "latest_path",
           "write_manifest", "write_latest", "read_latest", "session_manifest_paths",
           "running_manifest_to_resume"]

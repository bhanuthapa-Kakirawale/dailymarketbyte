"""Reading/writing the intelligence_refresh run manifest to disk.

    output/intelligence_refresh/runs/<run_id>.json   immutable once terminal
    output/intelligence_refresh/latest.json          mutable pointer

Write order is correctness-critical: the immutable run file is written FIRST (tmp +
os.replace), THEN `latest.json` is updated to point at it.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import subprocess
import uuid

from .models import RunManifest

RUNS_DIR = "intelligence_refresh"


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


def runs_dir(out_dir: str) -> str:
    return os.path.join(out_dir, RUNS_DIR, "runs")


def run_path(out_dir: str, run_id: str) -> str:
    return os.path.join(runs_dir(out_dir), f"{run_id}.json")


def latest_path(out_dir: str) -> str:
    return os.path.join(out_dir, RUNS_DIR, "latest.json")


def _atomic_write(path: str, payload: dict, what: str) -> None:
    from operations.run_context import guard_write
    guard_write(path, what)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def write_manifest(out_dir: str, manifest: RunManifest) -> str:
    path = run_path(out_dir, manifest.run_id)
    _atomic_write(path, manifest.to_dict(), "intelligence refresh run manifest")
    write_latest(out_dir, manifest, path)
    return path


def write_latest(out_dir: str, manifest: RunManifest, path: str) -> str:
    rel = os.path.relpath(path, out_dir).replace(os.sep, "/")
    lpath = latest_path(out_dir)
    _atomic_write(lpath, manifest.to_latest_dict(rel), "intelligence refresh latest pointer")
    return lpath


def read_latest(out_dir: str) -> dict | None:
    path = latest_path(out_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def all_run_paths(out_dir: str) -> list:
    return sorted(glob.glob(os.path.join(runs_dir(out_dir), "*.json")))


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def running_manifest_to_resume(out_dir: str) -> dict | None:
    """The newest manifest still RUNNING (never written as terminal), or None."""
    best, best_path = None, None
    for path in all_run_paths(out_dir):
        d = _load(path)
        if d and d.get("orchestrator_status") == "RUNNING":
            if best is None or d.get("started_at", "") >= best.get("started_at", ""):
                best, best_path = d, path
    if best is not None:
        best["_path"] = best_path
    return best


__all__ = ["RUNS_DIR", "new_run_id", "git_commit", "runs_dir", "run_path", "latest_path",
          "write_manifest", "write_latest", "read_latest", "all_run_paths",
          "running_manifest_to_resume"]

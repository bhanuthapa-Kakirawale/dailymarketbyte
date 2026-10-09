"""shadow_scheduler.runner: the thin runner. Acquires the global scheduler lock (bounded
wait), writes a RUNNING checkpoint record, launches the existing `.bat` entry point named in
`config.JOB_REGISTRY` as a subprocess, captures its exit code and console output verbatim,
releases the lock, writes the terminal record, and returns the runner's own process exit code.

No business/market/readiness logic lives here: the child command comes ONLY from the static
job registry, and a child that launches and returns nonzero (e.g. PK-D's own BLOCKED=20) is
never retried and never relabelled a "scheduler failure" - only a launch exception (the
executable couldn't even be started) is.
"""
from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import sys
import time

from . import config, records
from .lock import RunLock, ShadowLockTimeout
from .models import CHILD_NONZERO, COMPLETED, FAILED, INFRA_EXIT_CODES, LOCKED, RunRecord


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ist(utc_dt: dt.datetime) -> dt.datetime:
    import config as root_config
    return utc_dt.astimezone(root_config.IST)


def _business_latest_ref(business_root: str, relpath: str) -> str | None:
    """A path STRING reference, never file content - and only if the file actually exists."""
    if not relpath:
        return None
    full = os.path.join(business_root, relpath)
    return relpath.replace(os.sep, "/") if os.path.exists(full) else None


def run_job(job: str, *, cli_argv: list | None = None, out_dir: str | None = None,
           business_root: str | None = None, repo_root: str | None = None,
           python_executable: str | None = None, subprocess_run=subprocess.run,
           utcnow=None, lock_timeout_s: float | None = None, lock_poll_s: float | None = None,
           sleep=time.sleep, clock_monotonic=time.monotonic) -> int:
    if job not in config.JOB_REGISTRY:
        raise KeyError(f"unknown shadow_scheduler job: {job!r}")
    spec = config.JOB_REGISTRY[job]
    cli_argv = list(cli_argv) if cli_argv else []
    out_dir = out_dir or config.shadow_out_dir()
    repo_root = repo_root or config.repo_root()
    python_executable = python_executable or sys.executable
    utcnow = utcnow or _utc_now
    if business_root is None:
        import config as root_config
        business_root = root_config.OUT_DIR

    started_at_dt = utcnow()
    run_id = records.new_run_id(started_at_dt)
    child_command = [os.path.normpath(os.path.join(repo_root, spec.bat_relpath))]

    record = RunRecord(job=job, run_id=run_id, started_at=started_at_dt.isoformat(),
                       hostname=socket.gethostname(), pid=os.getpid(), repo_path=repo_root,
                       python_executable=python_executable, child_command=child_command,
                       runner_status="RUNNING",
                       log_path=records.log_path(out_dir, job, _ist(started_at_dt)))

    run_lock = RunLock(out_dir)
    try:
        reclaimed = run_lock.acquire_with_bounded_wait(
            run_id, job, cli_argv, timeout_s=lock_timeout_s, poll_s=lock_poll_s,
            sleep=sleep, clock=clock_monotonic)
    except ShadowLockTimeout as exc:
        # Never touches the holder's process or lock file - just records BUSY and exits.
        record.runner_status = LOCKED
        record.error = f"scheduler lock busy: {exc}"
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        records.write_record(out_dir, record)
        return INFRA_EXIT_CODES[LOCKED]

    record.reclaimed_stale_lock = reclaimed
    records.write_record(out_dir, record)   # RUNNING checkpoint

    try:
        log_abspath = os.path.join(out_dir, record.log_path)
        os.makedirs(os.path.dirname(log_abspath), exist_ok=True)
        with open(log_abspath, "w", encoding="utf-8") as logf:
            try:
                proc = subprocess_run(child_command, cwd=repo_root, stdout=logf,
                                      stderr=subprocess.STDOUT)
            except (OSError, ValueError) as exc:
                # The executable never started running at all - infra failure, not a business
                # outcome. Never retried.
                record.runner_status = FAILED
                record.child_exit_code = None
                record.error = f"{type(exc).__name__}: {exc}"
                exit_code = INFRA_EXIT_CODES[FAILED]
            else:
                # The child launched and ran to completion - its exit code is a BUSINESS
                # outcome (PK-D's own BLOCKED/DEGRADED/etc.) and is propagated verbatim,
                # never remapped, never retried.
                record.child_exit_code = proc.returncode
                record.runner_status = COMPLETED if proc.returncode == 0 else CHILD_NONZERO
                exit_code = proc.returncode
    finally:
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        record.business_latest_ref = _business_latest_ref(business_root,
                                                           spec.business_latest_relpath)
        records.write_record(out_dir, record)
        run_lock.release()

    return exit_code


__all__ = ["run_job"]

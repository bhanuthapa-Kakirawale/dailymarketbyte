"""shadow_scheduler.runner: the thin runner. Enforces the 1-hour execution window (PK-E
follow-up, 2026-10-12), acquires the global scheduler lock (bounded wait, capped at whatever
remains of that window), writes a RUNNING checkpoint record, launches the existing `.bat` entry
point named in `config.JOB_REGISTRY` as a subprocess, captures its exit code and console output
verbatim, releases the lock, writes the terminal record, and returns the runner's own process
exit code.

No business/market/readiness logic lives here: the child command comes ONLY from the static
job registry, and a child that launches and returns nonzero (e.g. PK-D's own BLOCKED=20) is
never retried and never relabelled a "scheduler failure" - only a launch exception (the
executable couldn't even be started) is.

Execution window: Windows Task Scheduler's `StartWhenAvailable` can still invoke a job late
(e.g. the laptop was off), but this runner - not Task Scheduler - is what refuses to launch the
child once `config.execution_window(spec, now_ist)`'s deadline has passed (EXPIRED, exit 0: a
deliberately-skipped opportunity, never a crash, never retried). The deadline is checked TWICE:
once before even attempting the global lock, and once again immediately before launching the
child - a job can start comfortably inside its window yet still lose the rest of it waiting for
another scheduled job to release the lock. The effective lock-wait timeout is capped at
whatever remains of the window so a bounded wait can never itself carry a job past its own
deadline.
"""
from __future__ import annotations

import datetime as dt
import os
import socket
import subprocess
import sys
import time

from . import config, dependency, records
from .lock import RunLock, ShadowLockTimeout
from .models import (CHILD_NONZERO, COMPLETED, DEPENDENCY_FAILED, EXPIRED, EXPIRED_EXIT_CODE,
                     FAILED, INFRA_EXIT_CODES, LOCKED, RunRecord, WAITING_DEPENDENCY)


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
           dependency_poll_s: float | None = None,
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

    now_ist = _ist(started_at_dt)
    scheduled_dt, deadline_dt = config.execution_window(spec, now_ist)

    record = RunRecord(job=job, run_id=run_id, started_at=started_at_dt.isoformat(),
                       hostname=socket.gethostname(), pid=os.getpid(), repo_path=repo_root,
                       python_executable=python_executable, child_command=child_command,
                       runner_status="RUNNING",
                       log_path=records.log_path(out_dir, job, now_ist),
                       scheduled_time=spec.scheduled_time,
                       window_start=scheduled_dt.isoformat(), window_end=deadline_dt.isoformat())

    def _expire(reason: str) -> int:
        record.runner_status = EXPIRED
        record.child_exit_code = None
        record.child_started = False
        record.error = reason
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        records.write_record(out_dir, record)
        return EXPIRED_EXIT_CODE

    def _dependency_failed(reason: str) -> int:
        record.runner_status = DEPENDENCY_FAILED
        record.child_exit_code = None
        record.child_started = False
        record.dependency_status = "FAILED"
        record.error = reason
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        records.write_record(out_dir, record)
        return INFRA_EXIT_CODES[DEPENDENCY_FAILED]

    # Check 1 of 2: before even attempting the global lock. A job invoked at/after its own
    # deadline (e.g. StartWhenAvailable firing hours late) must never even try to acquire the
    # lock, let alone wait on it.
    if now_ist >= deadline_dt:
        return _expire(f"invoked at {now_ist.isoformat()} IST, at/after the execution window "
                       f"deadline {deadline_dt.isoformat()} - child was not started")

    # Dependency ordering (PK-E follow-up): pre depends on intelligence_am, post depends on
    # intelligence_pm (config.DEPENDENCIES). This happens entirely BEFORE the global scheduler
    # lock is even attempted - a job must never wait for its dependency WHILE holding that lock,
    # or the two jobs could starve each other (the dependency can only ever acquire the lock
    # once the waiting job gives up or releases it). Bounded by this job's own execution-window
    # deadline; never touches/invokes the dependency job itself.
    dep_job = config.DEPENDENCIES.get(job)
    if dep_job:
        import config as root_config
        record.dependency_job = dep_job
        quick_outcome, quick_record = dependency.check(out_dir, dep_job, now_ist.date(),
                                                       root_config.IST)
        if quick_outcome == dependency.DEP_FAILED:
            return _dependency_failed(
                f"dependency {dep_job!r} had already reached a terminal non-satisfying outcome "
                f"today ({(quick_record or {}).get('runner_status')!r}) before {job!r} began")
        if quick_outcome == dependency.PENDING:
            # An actual wait is about to happen - checkpoint it (WAITING_DEPENDENCY) so a
            # concurrent `status` call can see it live, the same way the ordinary RUNNING
            # checkpoint is already visible once the lock is held.
            record.runner_status = WAITING_DEPENDENCY
            records.write_record(out_dir, record)
            effective_poll = (config.DEPENDENCY_POLL_SECONDS if dependency_poll_s is None
                             else dependency_poll_s)
            wait_result = dependency.wait(out_dir, dep_job, deadline_dt, utcnow=utcnow,
                                          ist=root_config.IST, sleep=sleep,
                                          poll_s=effective_poll, clock=clock_monotonic)
            record.dependency_wait_seconds = round(wait_result.waited_seconds, 3)
            if wait_result.outcome == "TIMED_OUT":
                return _expire(
                    f"own execution window deadline {deadline_dt.isoformat()} reached while "
                    f"waiting for dependency {dep_job!r} to complete today")
            if wait_result.outcome == dependency.DEP_FAILED:
                return _dependency_failed(
                    f"dependency {dep_job!r} reached a terminal non-satisfying outcome today "
                    f"while {job!r} was waiting")
            record.dependency_satisfied_at = wait_result.satisfied_at
            record.dependency_run_id = (wait_result.record or {}).get("run_id")
            record.runner_status = "RUNNING"   # restore the ordinary in-flight checkpoint status
        else:
            record.dependency_wait_seconds = 0.0
            record.dependency_run_id = (quick_record or {}).get("run_id")
        record.dependency_status = "SATISFIED"

    # The bounded lock wait must never itself carry this job past its own deadline: cap the
    # effective timeout at whatever remains of the window. Recomputed here (not reusing the
    # `now_ist` from above) because the dependency wait just above may have consumed real time.
    now_ist = _ist(utcnow())
    configured_wait = config.LOCK_WAIT_SECONDS if lock_timeout_s is None else lock_timeout_s
    remaining_window_s = (deadline_dt - now_ist).total_seconds()
    effective_wait = max(0.0, min(configured_wait, remaining_window_s))

    # Checkpoint B: a cheap, non-waiting re-validation immediately before attempting the global
    # lock. In practice a SATISFIED (terminal) upstream record cannot regress - this guards only
    # the genuine edge case of an operator manually re-running the dependency job mid-window.
    # Fails closed rather than re-entering a nested wait.
    if dep_job:
        recheck_outcome, _recheck_record = dependency.check(out_dir, dep_job, now_ist.date(),
                                                            root_config.IST)
        if recheck_outcome != dependency.SATISFIED:
            return _dependency_failed(
                f"dependency {dep_job!r} was no longer SATISFIED immediately before acquiring "
                f"the scheduler lock (recheck outcome={recheck_outcome!r})")

    run_lock = RunLock(out_dir)
    try:
        reclaimed = run_lock.acquire_with_bounded_wait(
            run_id, job, cli_argv, timeout_s=effective_wait, poll_s=lock_poll_s,
            sleep=sleep, clock=clock_monotonic)
    except ShadowLockTimeout as exc:
        # Never touches the holder's process or lock file. If the window has closed by the
        # time the wait gave up, this is EXPIRED (the deadline is what actually stopped it,
        # not the lock itself); otherwise it's a genuine LOCKED-within-window outcome.
        if _ist(utcnow()) >= deadline_dt:
            return _expire(f"global scheduler lock still busy at the execution window "
                           f"deadline {deadline_dt.isoformat()} - child was not started")
        record.runner_status = LOCKED
        record.child_started = False
        record.error = f"scheduler lock busy: {exc}"
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        records.write_record(out_dir, record)
        return INFRA_EXIT_CODES[LOCKED]

    record.reclaimed_stale_lock = reclaimed
    records.write_record(out_dir, record)   # RUNNING checkpoint

    # Check 2 of 2: immediately before launching the child. The lock may have been acquired
    # only after a wait that consumed the rest of the window (e.g. the holder released right
    # at the deadline) - the job must still refuse to launch.
    if _ist(utcnow()) >= deadline_dt:
        run_lock.release()
        return _expire(f"lock acquired at/after the execution window deadline "
                       f"{deadline_dt.isoformat()} - child was not started")

    # Checkpoint C: the last cheap, non-waiting dependency re-validation, immediately before
    # the child actually launches (lock already held - this does not wait, only observes).
    if dep_job:
        recheck_outcome, _recheck_record = dependency.check(
            out_dir, dep_job, _ist(utcnow()).date(), root_config.IST)
        if recheck_outcome != dependency.SATISFIED:
            run_lock.release()
            return _dependency_failed(
                f"dependency {dep_job!r} was no longer SATISFIED immediately before launching "
                f"the child (recheck outcome={recheck_outcome!r})")

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
                record.child_started = False
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

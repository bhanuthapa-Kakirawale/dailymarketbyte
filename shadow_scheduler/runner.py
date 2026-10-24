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
(e.g. the laptop was off or asleep), but this runner - not Task Scheduler - is what refuses to
launch the child outside the window. Every invocation is first attributed to ONE scheduled
occurrence (`config.resolve_occurrence`: job + IST scheduled_date) and only an IN_WINDOW
invocation (`window_start <= now < window_end`) may proceed: an EARLY one is rejected as
`EARLY`, a LATE one (e.g. last night's 19:30 job fired at 06:48 the next morning - the
2026-10-21 incident, where the old check compared it against TODAY's 19:30 window and only
asked "before the deadline?") is rejected as `EXPIRED`, attributed to the occurrence it really
belongs to. Both exit 0: a deliberately-skipped opportunity, never a crash, never retried, never
rescheduled. The window (start AND end, same scheduled_date) is re-checked after the dependency
wait, before the lock, and once more immediately before launching the child - a job can start
comfortably inside its window yet still lose the rest of it waiting. The effective lock-wait
timeout is capped at whatever remains of the window.

Dependencies are matched by the attributed scheduled_date, never by "today's wall-clock date",
so an upstream run that belonged to another occurrence (or was itself rejected out-of-window)
can never satisfy this one.

Never leave an orphan behind (PK-OPS-1): every wait (dependency, lock) is also capped at
`config.RUNNER_WAIT_BUDGET_SECONDS` after this runner's own start, so the runner always writes
its own terminal record before Task Scheduler's ExecutionTimeLimit (PT50M) can terminate it -
the 2026-10-21 06:48 POST checkpoint was left WAITING_DEPENDENCY exactly that way. Any
unexpected runner exception (e.g. the 2026-10-19 / 2026-10-21 RUNNING checkpoints, whose
runner died between the checkpoint and the child launch) is finalized as FAILED with the
exception text and the global lock released, instead of leaving a RUNNING checkpoint and a
stale lock. The child's launch phase and PID are checkpointed so a later orphan check can tell
"no child was ever launched" from "the child may still be running".
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
from .models import (CHILD_NONZERO, COMPLETED, DEPENDENCY_FAILED, EARLY, EARLY_EXIT_CODE,
                     EXPIRED, EXPIRED_EXIT_CODE, FAILED, INFRA_EXIT_CODES, LOCKED,
                     TERMINAL_STATUSES, RunRecord, WAITING_DEPENDENCY)


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


def _popen_and_wait(command, *, cwd, stdout, stderr, on_started):
    """The production launcher: `Popen` with a fixed argv list (no shell) so the child's PID can be
    checkpointed BEFORE waiting on it - the orphan check's evidence that the child itself may
    still be running even if this runner process is killed (e.g. by Task Scheduler's
    ExecutionTimeLimit)."""
    proc = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr)
    try:
        on_started(proc.pid)
    finally:
        proc.wait()
    return proc


def run_job(job: str, **kwargs) -> int:
    """Run one scheduled job. See the module docstring. An unexpected exception anywhere after
    the run record exists is finalized here (FAILED, exception text in `error`, lock released)
    - never a RUNNING / WAITING_DEPENDENCY checkpoint left behind by a live code path."""
    if job not in config.JOB_REGISTRY:
        raise KeyError(f"unknown shadow_scheduler job: {job!r}")
    holder: dict = {}
    try:
        return _run_job(job, _holder=holder, **kwargs)
    except Exception as exc:   # noqa: BLE001 - finalize, never leave an orphan
        return _finalize_crash(holder, exc)


def _finalize_crash(holder: dict, exc: Exception) -> int:
    import traceback
    record = holder.get("record")
    if record is None:
        raise exc
    utcnow = holder["utcnow"]
    if record.runner_status not in TERMINAL_STATUSES:
        record.runner_status = FAILED
        record.child_exit_code = None
        if record.child_phase != "LAUNCHED":
            record.child_started = False
        record.error = ("runner exception before a terminal record could be written: "
                        f"{type(exc).__name__}: {exc} | "
                        + " ".join(traceback.format_exc(limit=4).split()))[:4000]
    completed_at_dt = utcnow()
    record.completed_at = record.completed_at or completed_at_dt.isoformat()
    if record.duration is None:
        record.duration = (completed_at_dt - holder["started_at_dt"]).total_seconds()
    try:
        records.write_record(holder["out_dir"], record)
    except Exception:   # noqa: BLE001 - nothing more can be done; the lock is still released
        pass
    lock = holder.get("lock")
    if lock is not None:
        lock.release()
    if record.runner_status == FAILED:
        return INFRA_EXIT_CODES[FAILED]
    return record.child_exit_code if record.child_exit_code is not None else 0


def _run_job(job: str, *, _holder: dict, cli_argv: list | None = None,
             out_dir: str | None = None, business_root: str | None = None,
             repo_root: str | None = None, python_executable: str | None = None,
             subprocess_run=None, utcnow=None, lock_timeout_s: float | None = None,
             lock_poll_s: float | None = None, dependency_poll_s: float | None = None,
             sleep=time.sleep, clock_monotonic=time.monotonic) -> int:
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
    occurrence = config.resolve_occurrence(spec, started_at_dt)
    scheduled_dt, deadline_dt = occurrence.window_start, occurrence.window_end
    scheduled_date = occurrence.scheduled_date

    record = RunRecord(job=job, run_id=run_id, started_at=started_at_dt.isoformat(),
                       hostname=socket.gethostname(), pid=os.getpid(), repo_path=repo_root,
                       python_executable=python_executable, child_command=child_command,
                       runner_status="RUNNING",
                       log_path=records.log_path(out_dir, job, now_ist),
                       scheduled_time=spec.scheduled_time,
                       window_start=scheduled_dt.isoformat(), window_end=deadline_dt.isoformat(),
                       scheduled_date=scheduled_date.isoformat(),
                       invocation=occurrence.invocation, child_phase="NOT_LAUNCHED")
    _holder.update(record=record, utcnow=utcnow, started_at_dt=started_at_dt, out_dir=out_dir)

    # Every wait ends at the window deadline OR this runner's own wait budget, whichever is
    # first - so the runner finalizes its own record before Task Scheduler's ExecutionTimeLimit.
    budget_end = now_ist + dt.timedelta(seconds=config.RUNNER_WAIT_BUDGET_SECONDS)
    wait_deadline = min(deadline_dt, budget_end)

    def _in_window(at_utc: dt.datetime) -> bool:
        """Start AND end of the SAME attributed occurrence - never re-attributed mid-run."""
        return scheduled_dt <= _ist(at_utc) < deadline_dt

    def _closed_reason(at_utc: dt.datetime, what: str) -> str | None:
        """Why the child may no longer launch at `at_utc` (None = it still may)."""
        if not _in_window(at_utc):
            return (f"execution window {scheduled_dt.isoformat()} - {deadline_dt.isoformat()} "
                    f"of the {scheduled_date.isoformat()} occurrence closed {what} - child "
                    f"was not started")
        if _ist(at_utc) >= budget_end:
            return (f"runner wait budget ({config.RUNNER_WAIT_BUDGET_SECONDS}s after its own "
                    f"start, ending {budget_end.isoformat()}) exhausted {what}, ahead of Task "
                    f"Scheduler's {config.EXECUTION_TIME_LIMIT} ExecutionTimeLimit - child was "
                    f"not started")
        return None

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

    def _early(reason: str) -> int:
        record.runner_status = EARLY
        record.child_exit_code = None
        record.child_started = False
        record.error = reason
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        records.write_record(out_dir, record)
        return EARLY_EXIT_CODE

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

    # Check 1: the invocation itself, before the dependency wait and the global lock. Only an
    # IN_WINDOW invocation proceeds; nothing here waits for a window to open, and nothing is
    # rescheduled.
    if occurrence.invocation == config.EARLY:
        return _early(f"invoked at {now_ist.isoformat()} IST, before the execution window "
                      f"{scheduled_dt.isoformat()} - {deadline_dt.isoformat()} of the "
                      f"{scheduled_date.isoformat()} occurrence - child was not started")
    if occurrence.invocation == config.LATE:
        return _expire(f"invoked at {now_ist.isoformat()} IST, at/after the execution window "
                       f"deadline {deadline_dt.isoformat()} of the {scheduled_date.isoformat()} "
                       f"occurrence it belongs to - child was not started")

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
        quick_outcome, quick_record = dependency.check(out_dir, dep_job, scheduled_date,
                                                       root_config.IST)
        if quick_outcome == dependency.DEP_FAILED:
            return _dependency_failed(
                f"dependency {dep_job!r} had already reached a terminal non-satisfying outcome "
                f"for {scheduled_date.isoformat()} "
                f"({(quick_record or {}).get('runner_status')!r}) before {job!r} began")
        if quick_outcome == dependency.PENDING:
            # An actual wait is about to happen - checkpoint it (WAITING_DEPENDENCY) so a
            # concurrent `status` call can see it live, the same way the ordinary RUNNING
            # checkpoint is already visible once the lock is held.
            record.runner_status = WAITING_DEPENDENCY
            records.write_record(out_dir, record)
            effective_poll = (config.DEPENDENCY_POLL_SECONDS if dependency_poll_s is None
                             else dependency_poll_s)
            wait_result = dependency.wait(out_dir, dep_job, wait_deadline, utcnow=utcnow,
                                          ist=root_config.IST, sleep=sleep,
                                          poll_s=effective_poll, clock=clock_monotonic,
                                          scheduled_date=scheduled_date)
            record.dependency_wait_seconds = round(wait_result.waited_seconds, 3)
            if wait_result.outcome == "TIMED_OUT":
                record.dependency_status = "TIMED_OUT"
                bound = ("own execution window deadline" if wait_deadline == deadline_dt
                         else "runner wait budget end")
                return _expire(
                    f"{bound} {wait_deadline.isoformat()} reached while waiting for "
                    f"dependency {dep_job!r} to complete for {scheduled_date.isoformat()}")
            if wait_result.outcome == dependency.DEP_FAILED:
                return _dependency_failed(
                    f"dependency {dep_job!r} reached a terminal non-satisfying outcome for "
                    f"{scheduled_date.isoformat()} while {job!r} was waiting "
                    f"({(wait_result.record or {}).get('runner_status')!r})")
            record.dependency_satisfied_at = wait_result.satisfied_at
            record.dependency_run_id = (wait_result.record or {}).get("run_id")
            record.runner_status = "RUNNING"   # restore the ordinary in-flight checkpoint status
        else:
            record.dependency_wait_seconds = 0.0
            record.dependency_run_id = (quick_record or {}).get("run_id")
        record.dependency_status = "SATISFIED"

    # Check 2: re-validate the window after the dependency wait (which may have consumed real
    # time) - an upstream that only became SATISFIED at/after this job's deadline never makes
    # this job eligible just because its FIRST window check passed.
    closed = _closed_reason(utcnow(), "after the dependency wait")
    if closed:
        return _expire(closed)

    # The bounded lock wait must never itself carry this job past its own deadline: cap the
    # effective timeout at whatever remains of the window.
    now_ist = _ist(utcnow())
    configured_wait = config.LOCK_WAIT_SECONDS if lock_timeout_s is None else lock_timeout_s
    remaining_window_s = (wait_deadline - now_ist).total_seconds()
    effective_wait = max(0.0, min(configured_wait, remaining_window_s))

    # Checkpoint B: a cheap, non-waiting re-validation immediately before attempting the global
    # lock. In practice a SATISFIED (terminal) upstream record cannot regress - this guards only
    # the genuine edge case of an operator manually re-running the dependency job mid-window.
    # Fails closed rather than re-entering a nested wait.
    if dep_job:
        recheck_outcome, _recheck_record = dependency.check(out_dir, dep_job, scheduled_date,
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
        if _closed_reason(utcnow(), "while waiting for the global lock"):
            return _expire(f"global scheduler lock still busy at the wait deadline "
                           f"{wait_deadline.isoformat()} - child was not started")
        record.runner_status = LOCKED
        record.child_started = False
        record.error = f"scheduler lock busy: {exc}"
        completed_at_dt = utcnow()
        record.completed_at = completed_at_dt.isoformat()
        record.duration = (completed_at_dt - started_at_dt).total_seconds()
        records.write_record(out_dir, record)
        return INFRA_EXIT_CODES[LOCKED]

    _holder["lock"] = run_lock
    record.reclaimed_stale_lock = reclaimed
    records.write_record(out_dir, record)   # RUNNING checkpoint

    # Check 3: immediately before launching the child. The lock may have been acquired only
    # after a wait that consumed the rest of the window (e.g. the holder released right at the
    # deadline) - the job must still refuse to launch.
    closed = _closed_reason(utcnow(), "by the time the global lock was acquired")
    if closed:
        run_lock.release()
        return _expire(closed)

    # Checkpoint C: the last cheap, non-waiting dependency re-validation, immediately before
    # the child actually launches (lock already held - this does not wait, only observes).
    if dep_job:
        recheck_outcome, _recheck_record = dependency.check(
            out_dir, dep_job, scheduled_date, root_config.IST)
        if recheck_outcome != dependency.SATISFIED:
            run_lock.release()
            return _dependency_failed(
                f"dependency {dep_job!r} was no longer SATISFIED immediately before launching "
                f"the child (recheck outcome={recheck_outcome!r})")

    # LAUNCHING checkpoint: from here on a child may exist even if this runner dies before the
    # PID checkpoint below - the orphan check treats this phase as "child unknowable".
    record.child_phase = "LAUNCHING"
    records.write_record(out_dir, record)
    try:
        log_abspath = os.path.join(out_dir, record.log_path)
        os.makedirs(os.path.dirname(log_abspath), exist_ok=True)
        with open(log_abspath, "w", encoding="utf-8") as logf:
            def _child_started(child_pid: int) -> None:
                # Second RUNNING checkpoint: the child's own identity, written before waiting
                # on it. Best-effort - a checkpoint write failure must never kill the child.
                record.child_pid = child_pid
                record.child_started_at = utcnow().isoformat()
                record.child_phase = "LAUNCHED"
                try:
                    records.write_record(out_dir, record)
                except OSError:
                    pass

            try:
                if subprocess_run is None:
                    proc = _popen_and_wait(child_command, cwd=repo_root, stdout=logf,
                                           stderr=subprocess.STDOUT, on_started=_child_started)
                else:
                    proc = subprocess_run(child_command, cwd=repo_root, stdout=logf,
                                          stderr=subprocess.STDOUT)
            except (OSError, ValueError) as exc:
                # The executable never started running at all - infra failure, not a business
                # outcome. Never retried.
                record.runner_status = FAILED
                record.child_exit_code = None
                record.child_started = False
                record.child_phase = "NOT_LAUNCHED"
                record.error = f"{type(exc).__name__}: {exc}"
                exit_code = INFRA_EXIT_CODES[FAILED]
            else:
                # The child launched and ran to completion - its exit code is a BUSINESS
                # outcome (PK-D's own BLOCKED/DEGRADED/etc.) and is propagated verbatim,
                # never remapped, never retried.
                record.child_phase = "LAUNCHED"
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

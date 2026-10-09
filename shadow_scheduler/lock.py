"""shadow_scheduler.lock: a THIRD independently-implemented PID+hostname+liveness lock
(production_orchestrator and intelligence_refresh each already have their own un-shared copy
"so the two orchestrators stay independently runnable" - this one keeps shadow_scheduler out
of both their internals too). Scope: ONE lock file for the WHOLE scheduler
(output/shadow_scheduler/run.lock), independent of PK-D's own per-(edition, session) lock and
of intelligence_refresh's own module-wide lock - acquiring this lock never touches either.

Same liveness semantics as the other two: on win32, `is_process_alive` uses
`OpenProcess`/`CloseHandle` (never `os.kill(pid, 0)`, which can actually terminate the target
process on Windows). A same-host lock is stale ONLY on a verified-dead PID, never by age
alone; a cross-host lock falls back to a `CROSS_HOST_STALE_AFTER_SECONDS` age threshold since
liveness can't be verified remotely.

New here (does not exist in either copied original): `acquire_with_bounded_wait` - a short,
bounded poll loop for scheduled jobs that may legitimately need to wait for another DMB
scheduled job to finish. It never kills the holder and never deletes a live lock; only
`is_stale`'s existing rule ever reclaims.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys
import time

LOCK_SCHEMA = "dmb.shadow_scheduler.lock/1"
LOCK_FILE = "run.lock"
CROSS_HOST_STALE_AFTER_SECONDS = 4 * 3600


class LockConflict(RuntimeError):
    def __init__(self, held_by: dict):
        self.held_by = held_by
        super().__init__(
            f"locked by run_id={held_by.get('run_id')!r} job={held_by.get('job')!r} "
            f"pid={held_by.get('pid')} host={held_by.get('hostname')!r} "
            f"since {held_by.get('acquired_at')}")


class ShadowLockTimeout(LockConflict):
    """The bounded wait expired while the lock was still live/unverifiable."""


def lock_path(out_dir: str) -> str:
    return os.path.join(out_dir, LOCK_FILE)


def is_process_alive(pid: int) -> bool:
    """A conservative liveness check: True if the PID exists or we can't tell either way."""
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _parse_acquired_at(content: dict) -> dt.datetime | None:
    try:
        ts = dt.datetime.fromisoformat(content["acquired_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def is_stale(content: dict, now: dt.datetime | None = None,
            hostname: str | None = None) -> tuple:
    """Never stale purely from age on the SAME host - only a verified-dead PID reclaims a
    same-host lock."""
    if content.get("_unreadable"):
        return True, "lock file is unreadable/corrupted"
    now = now or dt.datetime.now(dt.timezone.utc)
    hostname = hostname or socket.gethostname()
    if content.get("hostname") != hostname:
        acquired = _parse_acquired_at(content)
        if acquired is None:
            return True, "cross-host lock has no parseable acquired_at"
        age = (now - acquired).total_seconds()
        if age > CROSS_HOST_STALE_AFTER_SECONDS:
            return True, f"cross-host lock is {age:.0f}s old, past the staleness threshold"
        return False, "cross-host lock within threshold - liveness cannot be verified remotely"
    pid = content.get("pid")
    if is_process_alive(pid):
        return False, f"same-host pid {pid} is running"
    return True, f"same-host pid {pid} is not running"


class RunLock:
    """Scope: the whole scheduler. `acquire()` raises LockConflict if a live/unverifiable lock
    is already held; otherwise returns the reclaimed lock's content (dict) or None."""

    def __init__(self, out_dir: str, *, now=None):
        self.path = lock_path(out_dir)
        self._clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._run_id: str | None = None

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"_unreadable": True}

    def acquire(self, run_id: str, job: str, cli_argv: list) -> dict | None:
        from operations.run_context import guard_write
        content = {"schema": LOCK_SCHEMA, "run_id": run_id, "job": job, "pid": os.getpid(),
                  "hostname": socket.gethostname(), "acquired_at": self._clock().isoformat(),
                  "cli_argv": list(cli_argv)}
        reclaimed = None
        for attempt in (1, 2):
            guard_write(self.path, "shadow scheduler run lock")
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(content, fh, indent=2)
                except Exception:
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    raise
                self._run_id = run_id
                return reclaimed
            except FileExistsError:
                existing = self._read()
                stale, _reason = is_stale(existing, self._clock())
                if not stale:
                    raise LockConflict(existing)
                if attempt == 2:
                    raise LockConflict(existing)
                reclaimed = existing
                try:
                    os.remove(self.path)
                except OSError:
                    pass
        raise LockConflict(self._read())   # pragma: no cover

    def acquire_with_bounded_wait(self, run_id: str, job: str, cli_argv: list, *,
                                  timeout_s: float | None = None, poll_s: float | None = None,
                                  sleep=time.sleep, clock=time.monotonic) -> dict | None:
        """Poll `acquire()` until it succeeds or `timeout_s` elapses. Never kills the holder,
        never deletes a live lock - every poll is a genuine `acquire()` attempt, so a lock that
        goes stale mid-wait is reclaimed the moment it's found stale, not blindly waited out.
        Raises `ShadowLockTimeout` (never silently gives up) if the deadline passes while the
        lock is still live/unverifiable.
        """
        from . import config
        timeout_s = config.LOCK_WAIT_SECONDS if timeout_s is None else timeout_s
        poll_s = config.LOCK_POLL_SECONDS if poll_s is None else poll_s
        deadline = clock() + timeout_s
        while True:
            try:
                return self.acquire(run_id, job, cli_argv)
            except LockConflict as exc:
                if clock() >= deadline:
                    raise ShadowLockTimeout(exc.held_by) from exc
                sleep(poll_s)

    def release(self) -> None:
        content = self._read()
        if content.get("run_id") == self._run_id and self._run_id is not None:
            try:
                os.remove(self.path)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


__all__ = ["LOCK_SCHEMA", "LOCK_FILE", "CROSS_HOST_STALE_AFTER_SECONDS", "LockConflict",
          "ShadowLockTimeout", "lock_path", "is_process_alive", "is_stale", "RunLock"]

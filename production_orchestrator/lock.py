"""The production run lock (PK-D): prevents a manual and a scheduled run (or two manual runs)
for the SAME (edition, session_date) from executing concurrently.

Nothing in the repo already provides this (no fcntl/msvcrt/portalocker/.lock anywhere) - it is
new. A PID+hostname+timestamp JSON file, not a bare OS advisory lock, so a crash leaves a
forensically useful trace (`reclaimed_stale_lock` in the next run's manifest) and so staleness
can be decided deterministically rather than just auto-released by the OS:

    same host   - staleness is decided ONLY by whether the PID is still alive, never by age
    other host  - liveness can't be verified remotely, so staleness falls back to age, with a
                  generous threshold (every real run here finishes in single-digit minutes)

`is_process_alive` is careful on Windows: `os.kill(pid, 0)` is NOT a safe liveness probe there -
CPython's os.kill on win32 sends real signals via TerminateProcess for anything other than
CTRL_C_EVENT/CTRL_BREAK_EVENT, so `os.kill(pid, 0)` can terminate the target process. Windows
liveness uses OpenProcess/CloseHandle instead, which only queries.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys

LOCK_SCHEMA = "dmb.production_orchestrator.lock/1"
LOCK_FILE = "run.lock"

# Cross-host-only staleness fallback. Every run this repo makes (PRE/POST render, readiness,
# REPORT) finishes in single-digit minutes; 4 hours is generous enough to never fire on a
# legitimately slow run while still letting a crashed remote machine's lock be recovered within
# the same operator day.
CROSS_HOST_STALE_AFTER_SECONDS = 4 * 3600


class LockConflict(RuntimeError):
    """A live (or unverifiable, not-yet-stale) lock is already held."""

    def __init__(self, held_by: dict):
        self.held_by = held_by
        super().__init__(
            f"locked by run_id={held_by.get('run_id')!r} pid={held_by.get('pid')} "
            f"host={held_by.get('hostname')!r} since {held_by.get('acquired_at')}")


def lock_path(out_dir: str, edition: str, session_date) -> str:
    return os.path.join(out_dir, "production_runs", edition, str(session_date), LOCK_FILE)


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
        return True          # exists, just owned by someone else - conservatively alive
    except OSError:
        return True          # unexpected errno - fail safe (never falsely declare it dead)
    return True


def _parse_acquired_at(content: dict) -> dt.datetime | None:
    try:
        ts = dt.datetime.fromisoformat(content["acquired_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def is_stale(content: dict, now: dt.datetime | None = None,
             hostname: str | None = None) -> tuple[bool, str]:
    """(stale?, reason). Never stale purely from age on the SAME host - only a verified-dead
    PID reclaims a same-host lock."""
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
            return True, (f"cross-host lock is {age:.0f}s old, past the "
                          f"{CROSS_HOST_STALE_AFTER_SECONDS}s cross-host staleness threshold")
        return False, "cross-host lock within threshold - liveness cannot be verified remotely"
    pid = content.get("pid")
    if is_process_alive(pid):
        return False, f"same-host pid {pid} is running"
    return True, f"same-host pid {pid} is not running"


class RunLock:
    """Scope: one (edition, session_date). `acquire()` raises LockConflict if a live/unverifiable
    lock is already held; otherwise returns the reclaimed lock's content (dict) or None."""

    def __init__(self, out_dir: str, edition: str, session_date, *, now=None):
        self.path = lock_path(out_dir, edition, session_date)
        self._clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._run_id: str | None = None

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"_unreadable": True}

    def acquire(self, run_id: str, intent: str, command: str, cli_argv: list) -> dict | None:
        from operations.run_context import guard_write
        content = {"schema": LOCK_SCHEMA, "run_id": run_id, "pid": os.getpid(),
                   "hostname": socket.gethostname(),
                   "acquired_at": self._clock().isoformat(),
                   "command": command, "intent": intent, "cli_argv": list(cli_argv)}
        reclaimed = None
        for attempt in (1, 2):
            guard_write(self.path, "production run lock")
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
                    # lost the reclaim race twice in a row - treat as a live conflict rather
                    # than loop forever
                    raise LockConflict(existing)
                reclaimed = existing
                try:
                    os.remove(self.path)
                except OSError:
                    pass
        raise LockConflict(self._read())   # pragma: no cover - loop always returns/raises above

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
           "lock_path", "is_process_alive", "is_stale", "RunLock"]

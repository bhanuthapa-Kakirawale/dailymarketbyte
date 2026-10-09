"""The intelligence_refresh run lock: one module-wide lock (unlike `production_orchestrator`'s
per-(edition, session) scope, since one `refresh` run spans up to ~31 sessions at once).
Independently implemented (not imported from `production_orchestrator.lock`) so the two
orchestrators stay independently runnable - but the same PID+hostname+liveness JSON pattern,
including the Windows `OpenProcess`/`CloseHandle` liveness check (`os.kill(pid, 0)` is not a
safe probe on win32 - it can terminate the target process) and the cross-host age fallback.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys

LOCK_SCHEMA = "dmb.intelligence_refresh.lock/1"
LOCK_FILE = "run.lock"
CROSS_HOST_STALE_AFTER_SECONDS = 4 * 3600


class LockConflict(RuntimeError):
    def __init__(self, held_by: dict):
        self.held_by = held_by
        super().__init__(
            f"locked by run_id={held_by.get('run_id')!r} pid={held_by.get('pid')} "
            f"host={held_by.get('hostname')!r} since {held_by.get('acquired_at')}")


def lock_path(out_dir: str) -> str:
    return os.path.join(out_dir, "intelligence_refresh", LOCK_FILE)


def is_process_alive(pid: int) -> bool:
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
    """Scope: the whole module. `acquire()` raises LockConflict if a live/unverifiable lock is
    already held; otherwise returns the reclaimed lock's content (dict) or None."""

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

    def acquire(self, run_id: str, cli_argv: list) -> dict | None:
        from operations.run_context import guard_write
        content = {"schema": LOCK_SCHEMA, "run_id": run_id, "pid": os.getpid(),
                  "hostname": socket.gethostname(), "acquired_at": self._clock().isoformat(),
                  "cli_argv": list(cli_argv)}
        reclaimed = None
        for attempt in (1, 2):
            guard_write(self.path, "intelligence refresh run lock")
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

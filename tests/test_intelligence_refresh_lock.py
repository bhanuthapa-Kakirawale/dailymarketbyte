"""intelligence_refresh.lock: same acquire/release/conflict/reclaim semantics as
production_orchestrator's lock, independently implemented and module-wide scoped (one lock
file for the whole refresh run, not per-session)."""
from __future__ import annotations

import datetime as dt
import json
import os
import socket

import pytest

from intelligence_refresh.lock import (CROSS_HOST_STALE_AFTER_SECONDS, LockConflict, RunLock,
                                       is_process_alive, is_stale, lock_path)

UTC = dt.timezone.utc


def test_acquire_creates_lock_file_and_release_removes_it(tmp_path):
    lock = RunLock(str(tmp_path))
    reclaimed = lock.acquire("run-1", ["refresh"])
    assert reclaimed is None
    assert os.path.exists(lock.path)
    with open(lock.path, encoding="utf-8") as fh:
        content = json.load(fh)
    assert content["run_id"] == "run-1"
    assert content["pid"] == os.getpid()
    assert content["hostname"] == socket.gethostname()
    lock.release()
    assert not os.path.exists(lock.path)


def test_second_acquire_while_live_lock_held_raises_lock_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr("intelligence_refresh.lock.is_process_alive", lambda pid: True)
    lock1 = RunLock(str(tmp_path))
    lock1.acquire("run-1", ["refresh"])
    lock2 = RunLock(str(tmp_path))
    with pytest.raises(LockConflict) as exc_info:
        lock2.acquire("run-2", ["refresh"])
    assert exc_info.value.held_by["run_id"] == "run-1"
    assert os.path.exists(lock1.path)


def test_same_host_dead_pid_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setattr("intelligence_refresh.lock.is_process_alive", lambda pid: False)
    lock1 = RunLock(str(tmp_path))
    lock1.acquire("run-1", ["refresh"])
    lock2 = RunLock(str(tmp_path))
    reclaimed = lock2.acquire("run-2", ["refresh"])
    assert reclaimed is not None and reclaimed["run_id"] == "run-1"
    with open(lock2.path, encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "run-2"


def test_cross_host_lock_within_threshold_is_not_stale():
    now = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    content = {"hostname": "other-machine", "pid": 123,
              "acquired_at": (now - dt.timedelta(hours=1)).isoformat()}
    stale, _ = is_stale(content, now, hostname="this-machine")
    assert stale is False


def test_cross_host_lock_past_threshold_is_stale():
    now = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    content = {"hostname": "other-machine", "pid": 123,
              "acquired_at": (now - dt.timedelta(seconds=CROSS_HOST_STALE_AFTER_SECONDS + 1)).isoformat()}
    stale, _ = is_stale(content, now, hostname="this-machine")
    assert stale is True


def test_corrupted_lock_file_is_treated_as_stale(tmp_path):
    path = lock_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    lock2 = RunLock(str(tmp_path))
    reclaimed = lock2.acquire("run-2", ["refresh"])
    assert reclaimed == {"_unreadable": True}


def test_is_process_alive_current_process_is_true():
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_invalid_pid_is_false():
    assert is_process_alive(-1) is False
    assert is_process_alive(None) is False

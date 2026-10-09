"""production_orchestrator.lock: acquire/release, conflict, stale-lock reclamation.

Same-host staleness is decided ONLY by liveness (never age); cross-host staleness is decided
ONLY by age (liveness can't be verified remotely). Fully offline, isolated to tmp_path.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket

import pytest

from production_orchestrator.lock import (CROSS_HOST_STALE_AFTER_SECONDS, LockConflict, RunLock,
                                           is_process_alive, is_stale, lock_path)

UTC = dt.timezone.utc


def test_acquire_creates_lock_file_and_release_removes_it(tmp_path):
    lock = RunLock(str(tmp_path), "POST", "2026-10-08")
    reclaimed = lock.acquire("run-1", "SHADOW", "post", ["post"])
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
    monkeypatch.setattr("production_orchestrator.lock.is_process_alive", lambda pid: True)
    lock1 = RunLock(str(tmp_path), "POST", "2026-10-08")
    lock1.acquire("run-1", "SHADOW", "post", ["post"])
    lock2 = RunLock(str(tmp_path), "POST", "2026-10-08")
    with pytest.raises(LockConflict) as exc_info:
        lock2.acquire("run-2", "SHADOW", "post", ["post"])
    assert exc_info.value.held_by["run_id"] == "run-1"
    # the loser never removed the winner's lock
    assert os.path.exists(lock1.path)


def test_same_host_dead_pid_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setattr("production_orchestrator.lock.is_process_alive", lambda pid: False)
    lock1 = RunLock(str(tmp_path), "POST", "2026-10-08")
    lock1.acquire("run-1", "SHADOW", "post", ["post"])
    lock2 = RunLock(str(tmp_path), "POST", "2026-10-08")
    reclaimed = lock2.acquire("run-2", "SHADOW", "post", ["post"])
    assert reclaimed is not None
    assert reclaimed["run_id"] == "run-1"
    with open(lock2.path, encoding="utf-8") as fh:
        content = json.load(fh)
    assert content["run_id"] == "run-2"


def test_same_host_alive_pid_is_never_reclaimed_regardless_of_age(tmp_path, monkeypatch):
    monkeypatch.setattr("production_orchestrator.lock.is_process_alive", lambda pid: True)
    very_old = (dt.datetime.now(UTC) - dt.timedelta(days=3)).isoformat()
    lock1 = RunLock(str(tmp_path), "POST", "2026-10-08")
    lock1.acquire("run-1", "SHADOW", "post", ["post"])
    with open(lock1.path, encoding="utf-8") as fh:
        content = json.load(fh)
    content["acquired_at"] = very_old
    with open(lock1.path, "w", encoding="utf-8") as fh:
        json.dump(content, fh)
    lock2 = RunLock(str(tmp_path), "POST", "2026-10-08")
    with pytest.raises(LockConflict):
        lock2.acquire("run-2", "SHADOW", "post", ["post"])


def test_cross_host_lock_within_threshold_is_not_stale():
    now = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    content = {"hostname": "other-machine", "pid": 123,
               "acquired_at": (now - dt.timedelta(hours=1)).isoformat()}
    stale, reason = is_stale(content, now, hostname="this-machine")
    assert stale is False
    assert "cannot be verified remotely" in reason


def test_cross_host_lock_past_threshold_is_stale():
    now = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    content = {"hostname": "other-machine", "pid": 123,
               "acquired_at": (now - dt.timedelta(seconds=CROSS_HOST_STALE_AFTER_SECONDS + 1)).isoformat()}
    stale, reason = is_stale(content, now, hostname="this-machine")
    assert stale is True
    assert "cross-host" in reason


def test_corrupted_lock_file_is_treated_as_stale(tmp_path):
    path = lock_path(str(tmp_path), "POST", "2026-10-08")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    lock2 = RunLock(str(tmp_path), "POST", "2026-10-08")
    reclaimed = lock2.acquire("run-2", "SHADOW", "post", ["post"])
    assert reclaimed == {"_unreadable": True}


def test_release_never_removes_a_lock_it_does_not_own(tmp_path, monkeypatch):
    monkeypatch.setattr("production_orchestrator.lock.is_process_alive", lambda pid: False)
    lock1 = RunLock(str(tmp_path), "POST", "2026-10-08")
    lock1.acquire("run-1", "SHADOW", "post", ["post"])
    # simulate someone else reclaiming + overwriting the file after lock1 acquired it
    lock2 = RunLock(str(tmp_path), "POST", "2026-10-08")
    lock2.acquire("run-2", "SHADOW", "post", ["post"])
    lock1.release()     # lock1's run_id no longer matches what's on disk
    assert os.path.exists(lock1.path)
    with open(lock1.path, encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "run-2"


def test_is_process_alive_current_process_is_true():
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_invalid_pid_is_false():
    assert is_process_alive(-1) is False
    assert is_process_alive(None) is False

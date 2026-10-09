"""shadow_scheduler.lock: same acquire/release/conflict/reclaim semantics as
production_orchestrator's and intelligence_refresh's own locks (independently implemented,
module-wide scoped - one lock file for the whole scheduler), plus the new bounded-wait poll
loop used by scheduled jobs that may legitimately need to wait for another DMB scheduled job
to finish."""
from __future__ import annotations

import datetime as dt
import json
import os
import socket

import pytest

from shadow_scheduler.lock import (CROSS_HOST_STALE_AFTER_SECONDS, LockConflict, RunLock,
                                   ShadowLockTimeout, is_process_alive, is_stale, lock_path)

UTC = dt.timezone.utc


def test_acquire_creates_lock_file_and_release_removes_it(tmp_path):
    lock = RunLock(str(tmp_path))
    reclaimed = lock.acquire("run-1", "post", ["run", "post"])
    assert reclaimed is None
    assert os.path.exists(lock.path)
    with open(lock.path, encoding="utf-8") as fh:
        content = json.load(fh)
    assert content["run_id"] == "run-1"
    assert content["job"] == "post"
    assert content["pid"] == os.getpid()
    assert content["hostname"] == socket.gethostname()
    lock.release()
    assert not os.path.exists(lock.path)


def test_second_acquire_while_live_lock_held_raises_lock_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr("shadow_scheduler.lock.is_process_alive", lambda pid: True)
    lock1 = RunLock(str(tmp_path))
    lock1.acquire("run-1", "intelligence_am", [])
    lock2 = RunLock(str(tmp_path))
    with pytest.raises(LockConflict) as exc_info:
        lock2.acquire("run-2", "pre", [])
    assert exc_info.value.held_by["run_id"] == "run-1"
    assert exc_info.value.held_by["job"] == "intelligence_am"
    assert os.path.exists(lock1.path)


def test_same_host_dead_pid_is_reclaimed_never_a_live_one(tmp_path, monkeypatch):
    monkeypatch.setattr("shadow_scheduler.lock.is_process_alive", lambda pid: False)
    lock1 = RunLock(str(tmp_path))
    lock1.acquire("run-1", "post", [])
    lock2 = RunLock(str(tmp_path))
    reclaimed = lock2.acquire("run-2", "pre", [])
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
    reclaimed = lock2.acquire("run-2", "post", [])
    assert reclaimed == {"_unreadable": True}


def test_is_process_alive_current_process_is_true():
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_invalid_pid_is_false():
    assert is_process_alive(-1) is False
    assert is_process_alive(None) is False


# --- bounded wait: new to this package, not present in either copied original ---------------

def test_bounded_wait_returns_immediately_when_lock_is_free(tmp_path):
    lock = RunLock(str(tmp_path))
    reclaimed = lock.acquire_with_bounded_wait("run-1", "post", [], timeout_s=5, poll_s=1)
    assert reclaimed is None
    assert os.path.exists(lock.path)


def test_bounded_wait_polls_and_succeeds_once_the_holder_releases(tmp_path, monkeypatch):
    monkeypatch.setattr("shadow_scheduler.lock.is_process_alive", lambda pid: True)
    holder = RunLock(str(tmp_path))
    holder.acquire("holder-run", "intelligence_am", [])

    clock = {"t": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds
        if len(sleeps) == 2:
            holder.release()   # the other scheduled job finishes mid-wait

    waiter = RunLock(str(tmp_path))
    reclaimed = waiter.acquire_with_bounded_wait(
        "waiter-run", "pre", [], timeout_s=30, poll_s=5, sleep=fake_sleep,
        clock=lambda: clock["t"])
    assert reclaimed is None
    assert len(sleeps) == 2   # waited through exactly two poll intervals before succeeding
    with open(waiter.path, encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "waiter-run"


def test_bounded_wait_times_out_without_ever_touching_the_live_holder(tmp_path, monkeypatch):
    monkeypatch.setattr("shadow_scheduler.lock.is_process_alive", lambda pid: True)
    holder = RunLock(str(tmp_path))
    holder.acquire("holder-run", "post", [])

    clock = {"t": 0.0}

    def fake_sleep(seconds):
        clock["t"] += seconds

    waiter = RunLock(str(tmp_path))
    with pytest.raises(ShadowLockTimeout) as exc_info:
        waiter.acquire_with_bounded_wait("waiter-run", "pre", [], timeout_s=20, poll_s=5,
                                         sleep=fake_sleep, clock=lambda: clock["t"])
    assert exc_info.value.held_by["run_id"] == "holder-run"
    # the holder's lock file is untouched - never killed, never deleted
    assert os.path.exists(holder.path)
    with open(holder.path, encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "holder-run"


def test_bounded_wait_reclaims_a_lock_that_goes_stale_mid_wait(tmp_path, monkeypatch):
    alive = {"v": True}
    monkeypatch.setattr("shadow_scheduler.lock.is_process_alive", lambda pid: alive["v"])
    holder = RunLock(str(tmp_path))
    holder.acquire("holder-run", "intelligence_pm", [])

    clock = {"t": 0.0}

    def fake_sleep(seconds):
        clock["t"] += seconds
        alive["v"] = False   # the holder's process dies mid-wait

    waiter = RunLock(str(tmp_path))
    reclaimed = waiter.acquire_with_bounded_wait("waiter-run", "post", [], timeout_s=30,
                                                 poll_s=5, sleep=fake_sleep,
                                                 clock=lambda: clock["t"])
    assert reclaimed is not None and reclaimed["run_id"] == "holder-run"

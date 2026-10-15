"""shadow_scheduler's 1-hour per-job execution window (PK-E follow-up, 2026-10-12):
`scheduled_time <= now < scheduled_time + EXECUTION_WINDOW_MINUTES` is the only valid start
range. Windows Task Scheduler's own `StartWhenAvailable` can still invoke a job late - this
runner, not Task Scheduler, is what refuses to launch the child once the deadline has passed
(EXPIRED, exit 0, no child started, never retried). Every clock here is injected
(`utcnow`/`now_ist`); no test depends on the real wall clock.

Covers packet section 13 tests A-O (A/M/N/O are pure `status.py` checks; the rest exercise
`runner.run_job` end to end with a fake subprocess spy).
"""
from __future__ import annotations

import datetime as dt
import os

from shadow_scheduler import config, records, runner, status
from shadow_scheduler.lock import RunLock, lock_path
from shadow_scheduler.models import COMPLETED, EXPIRED, LOCKED, MISSED, NOT_EXPECTED, PENDING, \
    RunRecord, WAITING

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode


class _FakeSubprocess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return _FakeProc(self.returncode)


class _FakeClock:
    """Ties the monotonic poll clock and wall-clock `utcnow` together, so a simulated lock
    wait advances BOTH consistently - the only way to meaningfully test "the lock wait itself
    carried this job past its deadline" (packet tests I/J)."""

    def __init__(self, start_utc: dt.datetime):
        self.utc = start_utc
        self.mono = 0.0

    def utcnow(self) -> dt.datetime:
        return self.utc

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.mono += seconds
        self.utc += dt.timedelta(seconds=seconds)


def _seed_dependency_satisfied(out_dir, job, utcnow):
    """This file is about the execution-window boundary, not dependency ordering (that's
    test_shadow_scheduler_dependency.py / the dependency-ordering section of
    test_shadow_scheduler_runner.py) - pre-seed `job`'s upstream dependency (if any) as already
    COMPLETED today so these tests exercise exactly the window/lock behavior they document,
    unaffected by the dependency-wait layer."""
    dep_job = config.DEPENDENCIES.get(job)
    if not dep_job:
        return
    started = utcnow()
    rec = RunRecord(job=dep_job, run_id=f"seed-{dep_job}", started_at=started.isoformat(),
                    completed_at=started.isoformat(), duration=1.0, runner_status=COMPLETED)
    records.write_record(out_dir, rec)


def _run(tmp_path, job, utcnow, seed_dependency=True, **kwargs):
    kwargs.setdefault("out_dir", str(tmp_path / "shadow_scheduler"))
    kwargs.setdefault("business_root", str(tmp_path / "output"))
    kwargs.setdefault("repo_root", str(tmp_path / "repo"))
    kwargs.setdefault("python_executable", "D:\\repo\\venv\\Scripts\\python.exe")
    kwargs.setdefault("subprocess_run", _FakeSubprocess(returncode=0))
    fake = kwargs["subprocess_run"]
    if seed_dependency:
        _seed_dependency_satisfied(kwargs["out_dir"], job, utcnow)
    exit_code = runner.run_job(job, utcnow=utcnow, **kwargs)
    return exit_code, fake


class _FixedCalendar:
    def __init__(self, is_session):
        self._is_session = is_session

    def is_session(self, d):
        return self._is_session


# --- B/C/D: Intelligence AM (06:40-07:40) ---------------------------------------------------

def test_b_intelligence_am_at_exactly_scheduled_time_is_allowed(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 1, 10, tzinfo=UTC)   # 06:40:00 IST
    exit_code, fake = _run(tmp_path, "intelligence_am", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 1


def test_c_intelligence_am_one_second_before_deadline_is_allowed(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 2, 9, 59, tzinfo=UTC)   # 07:39:59 IST
    exit_code, fake = _run(tmp_path, "intelligence_am", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 1


def test_d_intelligence_am_at_exactly_the_deadline_is_expired(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 2, 10, 0, tzinfo=UTC)   # 07:40:00 IST
    exit_code, fake = _run(tmp_path, "intelligence_am", utcnow)
    assert exit_code == 0   # EXPIRED is scheduler-safe, never a crash code
    assert len(fake.calls) == 0   # no child launch (packet section 12)
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "intelligence_am")
    assert latest["runner_status"] == EXPIRED
    full = records.read_record(os.path.join(out_dir, latest["path"]))
    assert full["child_exit_code"] is None
    assert full["child_started"] is False
    assert not os.path.exists(lock_path(out_dir))   # the lock was never even attempted


# --- E/F: PRE (07:00-08:00) ------------------------------------------------------------------

def test_e_pre_one_second_before_deadline_is_allowed(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 2, 29, 59, tzinfo=UTC)   # 07:59:59 IST
    exit_code, fake = _run(tmp_path, "pre", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 1


def test_f_pre_at_exactly_the_deadline_is_expired(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 2, 30, 0, tzinfo=UTC)   # 08:00:00 IST
    exit_code, fake = _run(tmp_path, "pre", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 0
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == EXPIRED


# --- G/H: POST (19:30-20:30) -----------------------------------------------------------------

def test_g_post_one_second_before_deadline_is_allowed(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 14, 59, 59, tzinfo=UTC)   # 20:29:59 IST
    exit_code, fake = _run(tmp_path, "post", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 1


def test_h_post_at_exactly_the_deadline_is_expired(tmp_path):
    utcnow = lambda: dt.datetime(2026, 10, 9, 15, 0, 0, tzinfo=UTC)   # 20:30:00 IST
    exit_code, fake = _run(tmp_path, "post", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 0
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == EXPIRED


# --- I/J: lock-wait interaction with the deadline -------------------------------------------

def test_i_lock_wait_carries_the_job_past_its_deadline_is_expired_no_child(tmp_path):
    """POST starts at 20:25 IST, 5 minutes of window remain. The holder never releases; the
    lock wait is capped at the remaining window (5 min, well under the default 15-min bounded
    wait), so by the time it gives up, the wall clock has reached the deadline exactly -
    EXPIRED, not LOCKED, and the child is never launched."""
    out_dir = str(tmp_path / "shadow_scheduler")
    holder = RunLock(out_dir)
    holder.acquire("holder-run", "intelligence_pm", [])

    clock = _FakeClock(dt.datetime(2026, 10, 9, 14, 55, 0, tzinfo=UTC))   # 20:25:00 IST
    _seed_dependency_satisfied(out_dir, "post", clock.utcnow)   # isolate the LOCK-vs-deadline
    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "post", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=clock.utcnow, sleep=clock.sleep,
        clock_monotonic=clock.monotonic, lock_poll_s=30)
    assert exit_code == 0
    assert len(fake.calls) == 0   # never launched
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == EXPIRED
    # the holder's own lock is untouched - the timeout never touches the holder
    import json
    with open(lock_path(out_dir), encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "holder-run"


def test_j_lock_becomes_available_before_deadline_launches_the_child(tmp_path):
    """POST starts comfortably inside its window; the holder (another scheduled job) releases
    the lock after one poll interval, well before POST's own deadline - POST acquires the lock
    and launches normally."""
    out_dir = str(tmp_path / "shadow_scheduler")
    holder = RunLock(out_dir)
    holder.acquire("holder-run", "intelligence_pm", [])

    clock = _FakeClock(dt.datetime(2026, 10, 9, 14, 5, 0, tzinfo=UTC))   # 19:35:00 IST
    _seed_dependency_satisfied(out_dir, "post", clock.utcnow)   # isolate the lock-wait behavior
    released = {"done": False}
    real_sleep = clock.sleep

    def sleep_and_release(seconds):
        real_sleep(seconds)
        if not released["done"]:
            holder.release()
            released["done"] = True

    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "post", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=clock.utcnow, sleep=sleep_and_release,
        clock_monotonic=clock.monotonic, lock_poll_s=15)
    assert exit_code == 0
    assert len(fake.calls) == 1   # launched normally


# --- K: a laptop-late/StartWhenAvailable-style invocation inside the window ------------------

def test_k_late_start_still_inside_window_launches_normally(tmp_path):
    """Intelligence AM invoked at 07:00 IST - 20 minutes late relative to its 06:40 schedule,
    but still inside the 06:40-07:40 window (e.g. the laptop was off and just came back)."""
    utcnow = lambda: dt.datetime(2026, 10, 9, 1, 30, tzinfo=UTC)   # 07:00:00 IST
    exit_code, fake = _run(tmp_path, "intelligence_am", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 1


# --- L: invocation after the window (a second job, for diversity) ---------------------------

def test_l_invocation_after_the_window_never_launches(tmp_path):
    """Intelligence PM (19:15-20:15) invoked at 20:20 IST - 5 minutes after its own deadline."""
    utcnow = lambda: dt.datetime(2026, 10, 9, 14, 50, tzinfo=UTC)   # 20:20:00 IST
    exit_code, fake = _run(tmp_path, "intelligence_pm", utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 0
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "intelligence_pm")
    assert latest["runner_status"] == EXPIRED


# --- A/M/N/O: status.py (missed_state), no runner/lock/subprocess involved ------------------

def test_a_before_scheduled_time_is_pending_not_waiting_or_missed(tmp_path):
    now_ist = dt.datetime(2026, 10, 9, 6, 39, tzinfo=IST)   # 1 minute before 06:40
    spec = config.JOB_REGISTRY["intelligence_am"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == PENDING


def test_m_weekend_or_holiday_is_not_expected(tmp_path):
    now_ist = dt.datetime(2026, 10, 10, 8, 0, tzinfo=IST)   # Saturday, say
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(False))
    assert row["missed_state"] == NOT_EXPECTED


def test_n_inside_window_with_no_record_is_waiting_never_missed(tmp_path):
    now_ist = dt.datetime(2026, 10, 9, 7, 15, tzinfo=IST)   # inside PRE's 07:00-08:00 window
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == WAITING
    assert row["missed_state"] != MISSED


def test_o_after_deadline_with_no_record_is_missed(tmp_path):
    now_ist = dt.datetime(2026, 10, 9, 8, 1, tzinfo=IST)   # 1 minute after PRE's 08:00 deadline
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == MISSED

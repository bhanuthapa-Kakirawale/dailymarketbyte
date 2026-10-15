"""shadow_scheduler.dependency: `check()` (one point-in-time, non-blocking read) and `wait()`
(a bounded poll loop built on top of it, never touching the global scheduler lock). Full
run_job() integration (the morning/evening wake-up races, every upstream terminal state) lives
in the "# --- dependency ordering ---" section of tests/test_shadow_scheduler_runner.py; this
file is the unit-level coverage of the dependency module in isolation.
"""
from __future__ import annotations

import datetime as dt

from shadow_scheduler import dependency, records
from shadow_scheduler.models import (CHILD_NONZERO, COMPLETED, EXPIRED, FAILED, LOCKED, RUNNING,
                                     RunRecord, WAITING_DEPENDENCY)

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _write(out_dir, job, started_at_dt, runner_status):
    rec = RunRecord(job=job, run_id=f"r-{job}-{runner_status}",
                    started_at=started_at_dt.isoformat(), completed_at=started_at_dt.isoformat(),
                    duration=1.0, runner_status=runner_status)
    records.write_record(out_dir, rec)


# --- check(): one point-in-time, non-blocking read ------------------------------------------

def test_check_is_pending_when_no_record_exists_today(tmp_path):
    outcome, rec = dependency.check(str(tmp_path), "intelligence_am",
                                    dt.date(2026, 10, 9), IST)
    assert outcome == dependency.PENDING
    assert rec is None


def test_check_is_satisfied_for_completed(tmp_path):
    out_dir = str(tmp_path)
    _write(out_dir, "intelligence_am", dt.datetime(2026, 10, 9, 1, 10, tzinfo=UTC), COMPLETED)
    outcome, rec = dependency.check(out_dir, "intelligence_am", dt.date(2026, 10, 9), IST)
    assert outcome == dependency.SATISFIED
    assert rec["runner_status"] == COMPLETED


def test_check_is_satisfied_for_child_nonzero(tmp_path):
    """CHILD_NONZERO is still a completed ATTEMPT - never re-judged as a business failure here;
    PK-C alone decides whether that outcome is actually safe to build on."""
    out_dir = str(tmp_path)
    _write(out_dir, "intelligence_pm", dt.datetime(2026, 10, 9, 13, 50, tzinfo=UTC),
          CHILD_NONZERO)
    outcome, rec = dependency.check(out_dir, "intelligence_pm", dt.date(2026, 10, 9), IST)
    assert outcome == dependency.SATISFIED


def test_check_is_failed_for_failed_locked_and_expired(tmp_path):
    for status_value in (FAILED, LOCKED, EXPIRED):
        out_dir = str(tmp_path / status_value)
        _write(out_dir, "intelligence_am", dt.datetime(2026, 10, 9, 1, 10, tzinfo=UTC),
              status_value)
        outcome, rec = dependency.check(out_dir, "intelligence_am", dt.date(2026, 10, 9), IST)
        assert outcome == dependency.DEP_FAILED, status_value
        assert rec["runner_status"] == status_value


def test_check_is_pending_for_running_or_waiting_dependency(tmp_path):
    for status_value in (RUNNING, WAITING_DEPENDENCY):
        out_dir = str(tmp_path / status_value)
        _write(out_dir, "intelligence_am", dt.datetime(2026, 10, 9, 1, 10, tzinfo=UTC),
              status_value)
        outcome, rec = dependency.check(out_dir, "intelligence_am", dt.date(2026, 10, 9), IST)
        assert outcome == dependency.PENDING, status_value


def test_check_rejects_a_prior_day_record_as_pending(tmp_path):
    """Current-day semantics (packet section 14): a COMPLETED record from yesterday must not
    satisfy today's dependency."""
    out_dir = str(tmp_path)
    _write(out_dir, "intelligence_am", dt.datetime(2026, 10, 8, 1, 10, tzinfo=UTC), COMPLETED)
    outcome, rec = dependency.check(out_dir, "intelligence_am", dt.date(2026, 10, 9), IST)
    assert outcome == dependency.PENDING
    assert rec is None


# --- wait(): bounded poll loop, built on check() --------------------------------------------

def test_wait_returns_immediately_when_already_satisfied(tmp_path):
    out_dir = str(tmp_path)
    _write(out_dir, "intelligence_am", dt.datetime(2026, 10, 9, 1, 30, tzinfo=UTC), COMPLETED)
    sleeps = []
    deadline = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    result = dependency.wait(out_dir, "intelligence_am", deadline,
                             utcnow=lambda: dt.datetime(2026, 10, 9, 1, 30, tzinfo=UTC), ist=IST,
                             sleep=sleeps.append, poll_s=10, clock=lambda: 0.0)
    assert result.outcome == dependency.SATISFIED
    assert sleeps == []
    assert result.waited_seconds == 0.0


def test_wait_polls_then_satisfies_once_the_dependency_completes_mid_wait(tmp_path):
    out_dir = str(tmp_path)
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 1, 30, tzinfo=UTC)}   # 07:00:00 IST
    deadline = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if calls["n"] == 2:
            _write(out_dir, "intelligence_am", clock["utc"], COMPLETED)

    result = dependency.wait(out_dir, "intelligence_am", deadline, utcnow=lambda: clock["utc"],
                             ist=IST, sleep=fake_sleep, poll_s=60, clock=lambda: clock["t"])
    assert result.outcome == dependency.SATISFIED
    assert calls["n"] == 2
    assert result.waited_seconds == 120.0
    assert result.satisfied_at is not None


def test_wait_polls_then_fails_once_the_dependency_reaches_a_terminal_non_satisfying_status(
        tmp_path):
    out_dir = str(tmp_path)
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 1, 30, tzinfo=UTC)}
    deadline = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if calls["n"] == 1:
            _write(out_dir, "intelligence_am", clock["utc"], FAILED)

    result = dependency.wait(out_dir, "intelligence_am", deadline, utcnow=lambda: clock["utc"],
                             ist=IST, sleep=fake_sleep, poll_s=60, clock=lambda: clock["t"])
    assert result.outcome == dependency.DEP_FAILED
    assert calls["n"] == 1


def test_wait_times_out_at_the_deadline_without_the_dependency_ever_appearing(tmp_path):
    out_dir = str(tmp_path)
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 25, tzinfo=UTC)}   # 07:55:00 IST
    deadline = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)

    result = dependency.wait(out_dir, "intelligence_am", deadline, utcnow=lambda: clock["utc"],
                             ist=IST, sleep=fake_sleep, poll_s=60, clock=lambda: clock["t"])
    assert result.outcome == "TIMED_OUT"


def test_wait_never_touches_or_invokes_the_dependency_job(tmp_path, monkeypatch):
    """wait() must never launch a subprocess or write anything for the dependency job - it only
    reads shadow_scheduler's own records."""
    import subprocess
    out_dir = str(tmp_path)

    def banned(*a, **k):
        raise AssertionError("dependency.wait() must never invoke a subprocess")

    monkeypatch.setattr(subprocess, "run", banned)
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 1, 30, tzinfo=UTC)}
    deadline = dt.datetime(2026, 10, 9, 1, 31, tzinfo=UTC).astimezone(IST)

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)

    result = dependency.wait(out_dir, "intelligence_am", deadline, utcnow=lambda: clock["utc"],
                             ist=IST, sleep=fake_sleep, poll_s=10, clock=lambda: clock["t"])
    assert result.outcome == "TIMED_OUT"


def test_wait_boundary_satisfied_strictly_before_deadline_is_allowed(tmp_path):
    """Packet section 34: the dependency becomes SATISFIED at an instant strictly before the
    deadline - allowed, even if only by a slim margin."""
    out_dir = str(tmp_path)
    deadline_utc = dt.datetime(2026, 10, 9, 2, 30, 0, tzinfo=UTC)   # 08:00:00 IST
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 29, 0, tzinfo=UTC)}   # 07:59:00 IST

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if clock["utc"] < deadline_utc:
            _write(out_dir, "intelligence_am", clock["utc"], COMPLETED)

    deadline_ist = deadline_utc.astimezone(IST)
    result = dependency.wait(out_dir, "intelligence_am", deadline_ist, utcnow=lambda: clock["utc"],
                             ist=IST, sleep=fake_sleep, poll_s=30, clock=lambda: clock["t"])
    assert result.outcome == dependency.SATISFIED


def test_wait_boundary_deadline_wins_even_if_dependency_completes_at_the_same_instant(tmp_path):
    """The deadline check happens BEFORE check() each iteration: even if the dependency becomes
    satisfied in the same instant the deadline is reached, this must still be TIMED_OUT, not a
    late SATISFIED (packet section 6/19/33 - the window's deadline authority always wins)."""
    out_dir = str(tmp_path)
    deadline_utc = dt.datetime(2026, 10, 9, 2, 30, 0, tzinfo=UTC)   # 08:00:00 IST
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 25, 0, tzinfo=UTC)}   # 07:55:00 IST

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if clock["utc"] >= deadline_utc:
            # the dependency "finishes" exactly as (or after) the deadline is reached
            _write(out_dir, "intelligence_am", clock["utc"], COMPLETED)

    deadline_ist = deadline_utc.astimezone(IST)
    result = dependency.wait(out_dir, "intelligence_am", deadline_ist, utcnow=lambda: clock["utc"],
                             ist=IST, sleep=fake_sleep, poll_s=60, clock=lambda: clock["t"])
    assert result.outcome == "TIMED_OUT"

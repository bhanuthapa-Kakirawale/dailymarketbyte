"""2026-10-28 timing packet: Task Scheduler triggers decoupled from permitted START windows, a
wait budget that leaves every launched child its runtime reserve under PT50M, COMPLETED-only
dependencies, and an explicit MISSING upstream.

    Intelligence AM  trigger 06:00  window 06:00-08:45
    PRE              trigger 06:30  window 06:00-09:00   (09:00 = hard cutoff)
    Intelligence PM  trigger 19:15  window 19:15-23:30   (23:59 - 20 min PM reserve, rounded)
    POST             trigger 19:30  window 19:00-23:59

Every clock is injected and every child is a spy - no test launches a real PRE/POST/refresh
run, touches a real Windows task, or writes outside tmp_path.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket

import pytest

from shadow_scheduler import audit, config, dependency, orphans, reconcile, records, runner, \
    status
from shadow_scheduler.lock import RunLock, lock_path
from shadow_scheduler.models import (ABANDONED, CHILD_NONZERO, COMPLETED, DEPENDENCY_FAILED,
                                     EARLY, EXPIRED, FAILED, LOCKED, MISSED, NOT_EXPECTED, OK,
                                     PENDING, RUNNING, WAITING_DEPENDENCY, RunRecord)

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HOST = socket.gethostname()
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 2026-10-21 is a Wednesday and a regular NSE session; 10-24/25 are Sat/Sun.
WED, THU = dt.date(2026, 10, 21), dt.date(2026, 10, 22)


def _utc(y, m, d, hh, mm, ss=0):
    """An IST wall-clock instant, returned in UTC (what `utcnow` yields)."""
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=IST).astimezone(UTC)


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode


class _Spy:
    """Fake child launcher. `during` runs while the 'child' is executing (e.g. to advance the
    clock across midnight)."""

    def __init__(self, returncode=0, during=None):
        self.returncode = returncode
        self.during = during
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if self.during:
            self.during()
        return _FakeProc(self.returncode)


class _Clock:
    def __init__(self, start_utc):
        self.utc = start_utc
        self.mono = 0.0
        self.on_sleep = None

    def utcnow(self):
        return self.utc

    def monotonic(self):
        return self.mono

    def sleep(self, seconds):
        self.mono += seconds
        self.utc += dt.timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep(self.utc)


class _Calendar:
    def __init__(self, is_session):
        self._v = is_session

    def is_session(self, _d):
        return self._v


def _no_sleep(_s):
    raise AssertionError("this invocation must not wait")


def _seed(out_dir, job, started_utc, status_=COMPLETED, scheduled_date=None,
          invocation=config.IN_WINDOW, **fields):
    """A current-runner record: it always carries its stored attribution."""
    if scheduled_date is None:
        scheduled_date = config.resolve_occurrence(config.JOB_REGISTRY[job],
                                                   started_utc).scheduled_date
    rec = RunRecord(job=job, run_id=fields.pop("run_id", f"seed-{job}-{started_utc:%m%d%H%M%S}"),
                    started_at=started_utc.isoformat(),
                    completed_at=None if status_ in (RUNNING, WAITING_DEPENDENCY)
                    else started_utc.isoformat(),
                    duration=1.0, runner_status=status_,
                    scheduled_date=str(scheduled_date), invocation=invocation,
                    window_policy=config.WINDOW_POLICY, **fields)
    records.write_record(out_dir, rec)
    return rec


def _in_flight(out_dir, job, started_utc, run_id):
    """An upstream still running on another host - unverifiable, so it is never mistaken for
    an orphan: the dependent job genuinely has to wait for it."""
    return _seed(out_dir, job, started_utc, status_=RUNNING, run_id=run_id, hostname="",
                 pid=0)


def _run(tmp_path, job, clock, spy=None, **kw):
    out_dir = kw.pop("out_dir", str(tmp_path / "ss"))
    spy = spy if spy is not None else _Spy()
    code = runner.run_job(job, out_dir=out_dir, business_root=str(tmp_path / "biz"),
                          repo_root=str(tmp_path / "repo"), python_executable="python.exe",
                          subprocess_run=spy, utcnow=clock.utcnow, sleep=clock.sleep,
                          clock_monotonic=clock.monotonic, **kw)
    latest = records.read_latest(out_dir, job)
    full = records.read_record(os.path.join(out_dir, latest["path"])) if latest else None
    return code, spy, full


# === configuration =============================================================================

def test_trigger_times_and_permitted_windows():
    got = {j: (s.scheduled_time, s.window_start, s.window_end)
           for j, s in config.JOB_REGISTRY.items()}
    assert got == {"intelligence_am": ("06:00", "06:00", "08:45"),
                   "pre": ("06:30", "06:00", "09:00"),
                   "intelligence_pm": ("19:15", "19:15", "23:30"),
                   "post": ("19:30", "19:00", "23:59")}


def test_every_window_is_one_calendar_day_and_contains_its_trigger():
    for spec in config.JOB_REGISTRY.values():
        start, end = config.window_for_date(spec, WED)
        trigger = dt.datetime.combine(WED, dt.time.fromisoformat(spec.scheduled_time),
                                      tzinfo=start.tzinfo)
        assert start.date() == end.date() == WED, spec.job
        assert start <= trigger < end, spec.job


def test_wait_budget_leaves_every_child_its_reserve_under_the_task_time_limit():
    assert config.EXECUTION_TIME_LIMIT == "PT50M"
    assert config.EXECUTION_TIME_LIMIT_SECONDS == 50 * 60
    assert config.CHILD_RUNTIME_RESERVE_SECONDS == 20 * 60
    assert config.RUNNER_WAIT_BUDGET_SECONDS == 25 * 60
    assert (config.RUNNER_WAIT_BUDGET_SECONDS + config.CHILD_RUNTIME_RESERVE_SECONDS
            + config.RUNNER_FINALIZE_MARGIN_SECONDS) <= config.EXECUTION_TIME_LIMIT_SECONDS
    # The reserve covers the slowest child ever observed (intelligence_pm, 521 s on 10-22)
    # with more than 2x margin.
    assert config.CHILD_RUNTIME_RESERVE_SECONDS >= 2 * 521


def test_pm_cutoff_leaves_pm_its_runtime_reserve_before_posts_last_start():
    pm, post = config.JOB_REGISTRY["intelligence_pm"], config.JOB_REGISTRY["post"]
    _s, pm_end = config.window_for_date(pm, WED)
    _s, post_end = config.window_for_date(post, WED)
    assert post_end - pm_end >= dt.timedelta(seconds=config.CHILD_RUNTIME_RESERVE_SECONDS)
    # AM likewise finishes (worst 212 s) well before PRE's hard cutoff.
    _s, am_end = config.window_for_date(config.JOB_REGISTRY["intelligence_am"], WED)
    _s, pre_end = config.window_for_date(config.JOB_REGISTRY["pre"], WED)
    assert pre_end - am_end >= dt.timedelta(minutes=10)


def test_registry_validation_rejects_a_window_without_its_trigger(monkeypatch):
    import dataclasses
    bad = dataclasses.replace(config.JOB_REGISTRY["pre"], window_start="06:45")   # > 06:30
    monkeypatch.setitem(config.JOB_REGISTRY, "pre", bad)
    with pytest.raises(ValueError, match="outside its window"):
        config._validate_registry()


def test_legacy_windows_are_the_frozen_trigger_plus_60_minute_rule():
    assert config.LEGACY_WINDOWS == {"intelligence_am": ("06:40", "07:40"),
                                     "pre": ("07:00", "08:00"),
                                     "intelligence_pm": ("19:15", "20:15"),
                                     "post": ("19:30", "20:30")}


def test_install_script_expected_tasks_match_the_registry():
    src = open(os.path.join(REPO, "scripts", "install_shadow_tasks.ps1"), encoding="utf-8").read()
    rows = dict(re.findall(r'Job = "(\w+)";\s+Time = "(\d\d:\d\d)"', src))
    assert rows == {j: s.scheduled_time for j, s in config.JOB_REGISTRY.items()}
    assert f'$script:ShadowExecutionTimeLimitIso8601 = "{config.EXECUTION_TIME_LIMIT}"' in src


def test_naive_datetimes_are_utc_and_attribution_is_ist():
    occ = config.resolve_occurrence(config.JOB_REGISTRY["intelligence_am"],
                                    dt.datetime(2026, 10, 21, 0, 30))   # naive = 06:00 IST
    assert occ.invocation == config.IN_WINDOW and occ.scheduled_date == WED
    assert str(occ.window_start.tzinfo) == "Asia/Kolkata"
    # 18:29 UTC on Wed = 23:59 IST on Wed: POST's window has just closed on Wednesday's date,
    # even though it is still "Wednesday" in UTC as well.
    occ = config.resolve_occurrence(config.JOB_REGISTRY["post"],
                                    dt.datetime(2026, 10, 21, 18, 29))
    assert (occ.invocation, occ.scheduled_date) == (config.LATE, WED)


# === PRE ======================================================================================

@pytest.mark.parametrize("hhmmss,expected,launches", [
    ((5, 59, 59), EARLY, False),
    ((6, 0, 0), COMPLETED, True),
    ((6, 30, 0), COMPLETED, True),       # the trigger
    ((8, 59, 59), COMPLETED, True),
    ((9, 0, 0), EXPIRED, False),         # hard cutoff
    ((9, 30, 0), EXPIRED, False),
])
def test_pre_window_boundaries(tmp_path, hhmmss, expected, launches):
    clock = _Clock(_utc(2026, 10, 21, *hhmmss))
    _seed(str(tmp_path / "ss"), "intelligence_am", _utc(2026, 10, 21, 6, 0))
    code, spy, full = _run(tmp_path, "pre", clock)
    assert code == 0
    assert full["runner_status"] == expected
    assert bool(spy.calls) is launches
    assert full["scheduled_date"] == "2026-10-21"
    assert full["window_start"] == "2026-10-21T06:00:00+05:30"
    assert full["window_end"] == "2026-10-21T09:00:00+05:30"
    assert full["window_policy"] == config.WINDOW_POLICY
    if not launches:
        assert full["child_started"] is False and full["child_exit_code"] is None


def test_pre_waits_for_a_delayed_intelligence_am_and_then_launches(tmp_path):
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 6, 30))
    am = _in_flight(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 20), "am-slow")

    def finish(now):
        if now >= _utc(2026, 10, 21, 6, 45):
            am.runner_status, am.completed_at = COMPLETED, now.isoformat()
            records.write_record(out_dir, am)

    clock.on_sleep = finish
    code, spy, full = _run(tmp_path, "pre", clock, dependency_poll_s=30)
    assert code == 0 and len(spy.calls) == 1
    assert full["dependency_status"] == "SATISFIED" and full["dependency_run_id"] == "am-slow"
    assert 14 * 60 <= full["dependency_wait_seconds"] <= 16 * 60


def test_pre_waits_when_intelligence_am_has_not_started_yet_inside_its_window(tmp_path):
    """No AM record at all, but AM's own window is still open (e.g. both tasks fired on the
    same late wake): this is PENDING, never MISSING - PRE waits and then launches."""
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 7, 30))

    def appears(now):
        if now >= _utc(2026, 10, 21, 7, 34):
            _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 7, 30), run_id="am-late")

    clock.on_sleep = appears
    code, spy, full = _run(tmp_path, "pre", clock, dependency_poll_s=30)
    assert len(spy.calls) == 1 and full["dependency_run_id"] == "am-late"


@pytest.mark.parametrize("upstream", [FAILED, CHILD_NONZERO, EXPIRED, LOCKED])
def test_pre_never_launches_on_an_unsuccessful_intelligence_am(tmp_path, upstream):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 0), status_=upstream,
          child_exit_code=30 if upstream == CHILD_NONZERO else None)
    clock = _Clock(_utc(2026, 10, 21, 6, 30))
    clock.sleep = _no_sleep   # a terminal upstream outcome is never waited on
    code, spy, full = _run(tmp_path, "pre", clock)
    assert code == 5 and spy.calls == []
    assert full["runner_status"] == DEPENDENCY_FAILED and full["dependency_status"] == "FAILED"
    assert repr(upstream) in full["error"]


def test_pre_fails_fast_when_intelligence_am_never_ran_and_its_window_closed(tmp_path):
    clock = _Clock(_utc(2026, 10, 21, 8, 50))   # AM window closed 08:45 (+2 min grace)
    clock.sleep = _no_sleep
    code, spy, full = _run(tmp_path, "pre", clock)
    assert code == 5 and spy.calls == []
    assert full["runner_status"] == DEPENDENCY_FAILED
    assert full["dependency_status"] == dependency.MISSING
    assert "'MISSING'" in full["error"]


def test_missing_upstream_inside_the_grace_period_is_still_pending():
    out_dir = None
    # 08:46 is after AM's 08:45 window end but inside the 2-minute grace for its own record.
    outcome, rec = dependency.check(out_dir or "unused-dir", "intelligence_am", WED, IST,
                                    now=_utc(2026, 10, 21, 8, 46))
    assert (outcome, rec) == (dependency.PENDING, None)
    outcome, rec = dependency.check("unused-dir", "intelligence_am", WED, IST,
                                    now=_utc(2026, 10, 21, 8, 47))
    assert outcome == dependency.DEP_FAILED and rec["runner_status"] == dependency.MISSING


@pytest.mark.parametrize("record_date", ["2026-10-20", "2026-10-22"])
def test_pre_ignores_an_intelligence_am_completion_for_another_scheduled_date(tmp_path,
                                                                             record_date):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 5), scheduled_date=record_date,
          run_id="other-day")
    clock = _Clock(_utc(2026, 10, 21, 8, 50))
    code, spy, full = _run(tmp_path, "pre", clock)
    assert spy.calls == [] and full["dependency_status"] == dependency.MISSING
    # ... and a satisfied run for yesterday never satisfies today, even inside the window.
    outcome, _ = dependency.check(out_dir, "intelligence_am", WED, IST,
                                  now=_utc(2026, 10, 21, 6, 30))
    assert outcome == dependency.PENDING


def test_pre_concurrent_duplicate_invocation_never_launches_a_second_child(tmp_path):
    """A second PRE invocation while the first still holds the global lock waits (bounded) and
    records LOCKED - the holder's lock is never touched and no second child starts."""
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 0))
    holder = RunLock(out_dir)
    holder.acquire("first-pre", "pre", [])
    clock = _Clock(_utc(2026, 10, 21, 6, 31))
    code, spy, full = _run(tmp_path, "pre", clock, lock_poll_s=60)
    assert code == 3 and spy.calls == [] and full["runner_status"] == LOCKED
    with open(lock_path(out_dir), encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "first-pre"


def test_pre_sequential_duplicate_invocation_is_its_own_record_for_the_same_occurrence(tmp_path):
    """Two in-window invocations of one occurrence (e.g. a manual re-run) are two separate,
    immutable scheduler records for the SAME scheduled_date - never a rewrite of the first.
    Business idempotency (no second video) is PK-D's ALREADY_COMPLETED, not this layer."""
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 0))
    _code, _spy, first = _run(tmp_path, "pre", _Clock(_utc(2026, 10, 21, 6, 30)))
    _code, _spy, second = _run(tmp_path, "pre", _Clock(_utc(2026, 10, 21, 7, 0)))
    assert first["run_id"] != second["run_id"]
    assert first["scheduled_date"] == second["scheduled_date"] == "2026-10-21"
    assert records.read_record(records.run_record_path(out_dir, first["run_id"])) == first


def test_pre_dependency_wait_stops_at_the_runner_budget_not_the_window(tmp_path):
    """AM still running at 06:55: PRE (started 06:30) gives up at its 25-minute wait budget,
    although its own window runs to 09:00 - it never launches into the last 25 minutes of
    Task Scheduler's PT50M."""
    out_dir = str(tmp_path / "ss")
    _in_flight(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 20), "am-hung")
    clock = _Clock(_utc(2026, 10, 21, 6, 30))
    code, spy, full = _run(tmp_path, "pre", clock, dependency_poll_s=60)
    assert code == 0 and spy.calls == []
    assert full["runner_status"] == EXPIRED and full["dependency_status"] == "TIMED_OUT"
    assert "runner wait budget end" in full["error"]
    waited = (clock.utc - _utc(2026, 10, 21, 6, 30)).total_seconds()
    assert waited <= config.RUNNER_WAIT_BUDGET_SECONDS + 60


def test_pre_insufficient_remaining_execution_time_never_launches(tmp_path, monkeypatch):
    """The dependency is satisfied just inside the budget, but the clock is past the budget at
    the post-wait re-check (e.g. the machine slept): a child launched now could not finish
    under PT50M - EXPIRED with an explicit reason, no child, no lock."""
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 6, 30))
    real_wait = dependency.wait

    def wait_then_sleep_through_budget(*a, **kw):
        result = real_wait(*a, **kw)
        clock.utc = _utc(2026, 10, 21, 6, 55, 1)   # 25 min + 1 s after the runner's start
        return result

    _in_flight(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 20), "am")
    clock.on_sleep = lambda now: _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 20),
                                       run_id="am")
    monkeypatch.setattr(dependency, "wait", wait_then_sleep_through_budget)
    code, spy, full = _run(tmp_path, "pre", clock, dependency_poll_s=30)
    assert spy.calls == [] and full["runner_status"] == EXPIRED
    assert "insufficient remaining execution time" in full["error"]
    assert not os.path.exists(lock_path(out_dir))


def test_pre_window_is_rechecked_immediately_before_launch(tmp_path, monkeypatch):
    """The lock is acquired only as the 09:00 cutoff passes: the runner must refuse to launch
    and release the lock it just took."""
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 0))
    clock = _Clock(_utc(2026, 10, 21, 8, 59, 30))
    real_acquire = RunLock.acquire_with_bounded_wait

    def acquire_late(self, *a, **kw):
        out = real_acquire(self, *a, **kw)
        clock.utc = _utc(2026, 10, 21, 9, 0, 1)
        return out

    monkeypatch.setattr(RunLock, "acquire_with_bounded_wait", acquire_late)
    code, spy, full = _run(tmp_path, "pre", clock)
    assert code == 0 and spy.calls == [] and full["runner_status"] == EXPIRED
    assert "by the time the global lock was acquired" in full["error"]
    assert not os.path.exists(lock_path(out_dir))


def test_pre_unexpected_exception_after_the_lock_is_finalized_failed_and_unlocks(
        tmp_path, monkeypatch):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 21, 6, 0))
    real_check = dependency.check
    calls = {"n": 0}

    def flaky_check(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:   # checkpoint C - the lock is already held
            raise RuntimeError("disk vanished")
        return real_check(*a, **kw)

    monkeypatch.setattr(dependency, "check", flaky_check)
    code, spy, full = _run(tmp_path, "pre", _Clock(_utc(2026, 10, 21, 6, 30)))
    assert code == 4 and spy.calls == []
    assert full["runner_status"] == FAILED and "disk vanished" in full["error"]
    assert not os.path.exists(lock_path(out_dir))


# === POST ====================================================================================

@pytest.mark.parametrize("ymdhms,expected,launches,belongs_to", [
    ((2026, 10, 21, 18, 59, 59), EARLY, False, "2026-10-21"),
    ((2026, 10, 21, 19, 0, 0), COMPLETED, True, "2026-10-21"),
    ((2026, 10, 21, 19, 30, 0), COMPLETED, True, "2026-10-21"),    # the trigger
    ((2026, 10, 21, 23, 58, 59), COMPLETED, True, "2026-10-21"),
    ((2026, 10, 21, 23, 59, 0), EXPIRED, False, "2026-10-21"),
    ((2026, 10, 22, 0, 30, 0), EXPIRED, False, "2026-10-21"),      # after midnight -> still D
    ((2026, 10, 22, 6, 48, 0), EXPIRED, False, "2026-10-21"),      # the 10-21 06:48 shape
])
def test_post_window_boundaries(tmp_path, ymdhms, expected, launches, belongs_to):
    _seed(str(tmp_path / "ss"), "intelligence_pm", _utc(2026, 10, 21, 19, 15))
    code, spy, full = _run(tmp_path, "post", _Clock(_utc(*ymdhms)))
    assert code == 0
    assert full["runner_status"] == expected and bool(spy.calls) is launches
    assert full["scheduled_date"] == belongs_to


@pytest.mark.parametrize("hhmm,expected", [((19, 14), EARLY), ((19, 15), COMPLETED),
                                           ((23, 29), COMPLETED), ((23, 30), EXPIRED)])
def test_intelligence_pm_window_boundaries(tmp_path, hhmm, expected):
    _code, spy, full = _run(tmp_path, "intelligence_pm", _Clock(_utc(2026, 10, 21, *hhmm)))
    assert full["runner_status"] == expected
    assert bool(spy.calls) is (expected == COMPLETED)


def test_post_waits_for_a_delayed_intelligence_pm_and_then_launches(tmp_path):
    out_dir = str(tmp_path / "ss")
    pm = _in_flight(out_dir, "intelligence_pm", _utc(2026, 10, 21, 22, 10), "pm-late")
    clock = _Clock(_utc(2026, 10, 21, 22, 10))   # both fired late on the same wake

    def finish(now):
        if now >= _utc(2026, 10, 21, 22, 19):
            pm.runner_status, pm.completed_at = COMPLETED, now.isoformat()
            records.write_record(out_dir, pm)

    clock.on_sleep = finish
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=30)
    assert len(spy.calls) == 1 and full["dependency_run_id"] == "pm-late"
    assert full["scheduled_date"] == "2026-10-21"


@pytest.mark.parametrize("upstream", [FAILED, CHILD_NONZERO, EXPIRED])
def test_post_never_launches_on_an_unsuccessful_intelligence_pm(tmp_path, upstream):
    _seed(str(tmp_path / "ss"), "intelligence_pm", _utc(2026, 10, 21, 19, 15), status_=upstream)
    clock = _Clock(_utc(2026, 10, 21, 19, 30))
    clock.sleep = _no_sleep
    code, spy, full = _run(tmp_path, "post", clock)
    assert code == 5 and spy.calls == [] and full["runner_status"] == DEPENDENCY_FAILED


def test_intelligence_pm_after_its_cutoff_blocks_post_explicitly(tmp_path):
    """PM fired at 23:35 (past 23:30) is EXPIRED with no child; the POST fired with it is
    DEPENDENCY_FAILED at once - never a POST built on a refresh that did not run."""
    out_dir = str(tmp_path / "ss")
    _code, pm_spy, pm = _run(tmp_path, "intelligence_pm", _Clock(_utc(2026, 10, 21, 23, 35)))
    assert pm["runner_status"] == EXPIRED and pm_spy.calls == []
    clock = _Clock(_utc(2026, 10, 21, 23, 35, 5))
    clock.sleep = _no_sleep
    code, spy, full = _run(tmp_path, "post", clock)
    assert code == 5 and spy.calls == []
    assert full["dependency_status"] == "FAILED" and "'EXPIRED'" in full["error"]
    assert records.read_record(records.run_record_path(out_dir, pm["run_id"])) == pm


def test_post_with_no_intelligence_pm_record_waits_through_the_grace_then_fails(tmp_path):
    clock = _Clock(_utc(2026, 10, 21, 23, 31))   # PM window closed 23:30, grace to 23:32
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=15)
    assert code == 5 and spy.calls == []
    assert full["dependency_status"] == dependency.MISSING
    assert clock.utc <= _utc(2026, 10, 21, 23, 32, 15)


def test_post_ignores_an_intelligence_pm_completion_from_the_previous_day(tmp_path):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 20, 19, 15), run_id="tuesday-pm")
    clock = _Clock(_utc(2026, 10, 21, 23, 40))
    code, spy, full = _run(tmp_path, "post", clock)
    assert spy.calls == [] and full["dependency_status"] == dependency.MISSING


def test_post_launched_before_midnight_keeps_its_scheduled_date_across_midnight(tmp_path):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 23, 20))
    clock = _Clock(_utc(2026, 10, 21, 23, 50))

    def child_runs_past_midnight():
        clock.utc = _utc(2026, 10, 22, 0, 6)

    code, spy, full = _run(tmp_path, "post", clock, spy=_Spy(during=child_runs_past_midnight))
    assert code == 0 and len(spy.calls) == 1 and full["runner_status"] == COMPLETED
    assert full["scheduled_date"] == "2026-10-21" and full["invocation"] == config.IN_WINDOW
    assert full["completed_at"].startswith("2026-10-21T18:36")   # 00:06 IST on the 22nd
    # Seen the next morning: Thursday's POST is its own (PENDING) occurrence; Wednesday's
    # finished run is reported only as the previous occurrence.
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 22, 0, 30, tzinfo=IST),
                              calendar=_Calendar(True))
    row = s["jobs"]["post"]
    assert row["missed_state"] == PENDING and row["runner_status"] is None
    assert row["previous_occurrence"]["scheduled_date"] == "2026-10-21"
    assert row["previous_occurrence"]["state"] == OK


def test_post_waiting_at_midnight_expires_for_its_own_date_never_rolls_over(tmp_path):
    out_dir = str(tmp_path / "ss")
    _in_flight(out_dir, "intelligence_pm", _utc(2026, 10, 21, 23, 25), "pm-slow")
    clock = _Clock(_utc(2026, 10, 21, 23, 55))
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=30)
    assert spy.calls == [] and full["runner_status"] == EXPIRED
    assert full["dependency_status"] == "TIMED_OUT"
    assert full["scheduled_date"] == "2026-10-21"
    assert clock.utc < _utc(2026, 10, 22, 0, 0, 1)


def test_a_still_running_previous_day_job_never_overlaps_the_next_occurrence(tmp_path):
    """The previous evening's POST still holds the global lock at 06:00: the next morning's
    AM waits (bounded), records LOCKED and never runs alongside it - the holder is untouched."""
    out_dir = str(tmp_path / "ss")
    holder = RunLock(out_dir)
    holder.acquire("wed-post", "post", [])
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 22, 6, 0)),
                           lock_poll_s=60)
    assert code == 3 and spy.calls == [] and full["runner_status"] == LOCKED
    assert full["scheduled_date"] == "2026-10-22"
    with open(lock_path(out_dir), encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "wed-post"


# === general ==================================================================================

@pytest.mark.parametrize("job,ymdhm,belongs_to", [
    ("pre", (2026, 10, 24, 6, 30), "2026-10-23"),               # Saturday trigger time
    ("intelligence_am", (2026, 10, 25, 7, 0), "2026-10-23"),    # Sunday
    ("post", (2026, 10, 25, 20, 0), "2026-10-23"),
])
def test_weekend_invocations_belong_to_friday_and_never_launch(tmp_path, job, ymdhm,
                                                               belongs_to):
    code, spy, full = _run(tmp_path, job, _Clock(_utc(*ymdhm)))
    assert code == 0 and spy.calls == [] and full["runner_status"] == EXPIRED
    assert full["scheduled_date"] == belongs_to


def test_weekday_holiday_is_not_expected_in_status(tmp_path):
    s = status.overall_status(str(tmp_path / "ss"), business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 21, 9, 30, tzinfo=IST),
                              calendar=_Calendar(False))
    assert {s["jobs"][j]["missed_state"] for j in ("intelligence_am", "pre")} == {NOT_EXPECTED}


def test_status_uses_the_permitted_window_for_pending_waiting_and_missed(tmp_path):
    def state(hh, mm):
        s = status.overall_status(str(tmp_path / "ss"), business_root=str(tmp_path / "biz"),
                                  now=dt.datetime(2026, 10, 21, hh, mm, tzinfo=IST),
                                  calendar=_Calendar(True))
        return s["jobs"]["pre"]["missed_state"], s["jobs"]["post"]["missed_state"]
    assert state(5, 59) == (PENDING, PENDING)
    assert state(6, 10) == ("WAITING", PENDING)    # PRE window opens before its 06:30 trigger
    assert state(19, 10) == (MISSED, "WAITING")    # POST window opens before its 19:30 trigger
    assert state(23, 58) == (MISSED, "WAITING")
    text = status.render_human(status.overall_status(
        str(tmp_path / "ss"), business_root=str(tmp_path / "biz"),
        now=dt.datetime(2026, 10, 21, 6, 10, tzinfo=IST), calendar=_Calendar(True)))
    assert "sched=06:30 window=06:00-09:00" in text


def test_legacy_records_keep_the_attribution_they_were_written_under(tmp_path):
    """A pre-PK-OPS-1 record (no stored scheduled_date/invocation) is inferred under the old
    trigger + 60 min rule - never silently re-attributed as IN_WINDOW under today's windows."""
    legacy = {"job": "intelligence_pm", "run_id": "legacy-pm",
              "started_at": _utc(2026, 10, 21, 21, 16, 5).isoformat(),
              "runner_status": EXPIRED, "child_started": False}
    attr = orphans.attribution(legacy)
    assert attr == {"scheduled_date": "2026-10-21", "invocation": config.LATE, "inferred": True}
    # Today's rule would accept the same instant - and audit says so, separately.
    occ = config.resolve_occurrence(config.JOB_REGISTRY["intelligence_pm"],
                                    _utc(2026, 10, 21, 21, 16, 5))
    assert occ.invocation == config.IN_WINDOW
    out_dir = str(tmp_path / "ss")
    path = records.run_record_path(out_dir, "legacy-pm")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(legacy, fh)
    before = open(path, encoding="utf-8").read()
    row = audit.audit(out_dir, business_root=str(tmp_path / "biz"))["rows"][0]
    assert row["verdict"] == audit.CORRECTLY_REJECTED
    assert row["invocation"] == config.LATE and row["attribution_inferred"] is True
    assert row["current_invocation"] == config.IN_WINDOW
    assert row["current_runner_would"].startswith("PROCEED")
    assert open(path, encoding="utf-8").read() == before   # audit never writes


def test_stored_attribution_always_wins_over_any_window_rule():
    rec = {"job": "intelligence_am", "started_at": _utc(2026, 10, 27, 7, 42).isoformat(),
           "scheduled_date": "2026-10-27", "invocation": config.LATE}
    assert orphans.attribution(rec) == {"scheduled_date": "2026-10-27",
                                        "invocation": config.LATE, "inferred": False}


def test_orphan_reconciliation_stays_append_only_with_the_new_windows(tmp_path):
    """A WAITING_DEPENDENCY PRE checkpoint left by a killed runner: the dry run writes nothing;
    --execute (on tmp_path only) writes exactly one exclusive marker, never rewrites the run
    file, and a second pass is a no-op."""
    out_dir = str(tmp_path / "ss")
    rec = RunRecord(job="pre", run_id="pre-killed",
                    started_at=_utc(2026, 10, 21, 6, 30).isoformat(), hostname=HOST, pid=8380,
                    runner_status=WAITING_DEPENDENCY, scheduled_date="2026-10-21",
                    invocation=config.IN_WINDOW, child_phase="NOT_LAUNCHED",
                    window_policy=config.WINDOW_POLICY,
                    log_path="logs/2026-10-21/pre_063000.log")
    path = records.run_record_path(out_dir, rec.run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec.to_dict(), fh, indent=2)
    original = open(path, encoding="utf-8").read()

    def probe(_pid, _started):
        return {"state": orphans.DEAD, "created_at": None}

    now = _utc(2026, 10, 21, 9, 30)
    dry = reconcile.reconcile(out_dir, execute=False, now=now, hostname=HOST, probe=probe,
                              business_root=str(tmp_path / "biz"))
    assert records.read_reconciliation(out_dir, "pre-killed") is None
    assert dry["counts"].get("RECONCILED", 0) == 0
    reconcile.reconcile(out_dir, execute=True, now=now, hostname=HOST, probe=probe,
                        business_root=str(tmp_path / "biz"))
    assert records.read_reconciliation(out_dir, "pre-killed")["effective_status"] == ABANDONED
    again = reconcile.reconcile(out_dir, execute=True, now=now, hostname=HOST, probe=probe,
                                business_root=str(tmp_path / "biz"))
    assert again["counts"].get("RECONCILED", 0) == 0
    assert open(path, encoding="utf-8").read() == original
    # an abandoned upstream checkpoint is a terminal non-satisfying outcome for POST-style deps
    assert orphans.effective_status(out_dir, records.read_record(path)) == ABANDONED

"""PK-OPS-1 (2026-10-24): scheduler launch-window START enforcement, scheduled-occurrence
identity, dependency re-checks, runner self-finalization and safe orphan reconciliation.

Incident: on 2026-10-21 06:48 IST the previous evening's missed 19:15/19:30 jobs fired
(StartWhenAvailable) and launched, because the runner built the window on the INVOCATION's
calendar date and only checked `now >= deadline`. The same defect launched POST on Saturday
2026-10-24 08:29. Every clock here is injected; no test touches a real process, a real Windows
task, or production output. Section letters follow the packet's section-14 list (A-Q).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket

import pytest

from shadow_scheduler import audit, config, dependency, orphans, reconcile, records, runner, \
    status
from shadow_scheduler.lock import lock_path
from shadow_scheduler.models import (ABANDONED, CHILD_NONZERO, COMPLETED, DEPENDENCY_FAILED,
                                     EARLY, EXPIRED, FAILED, MISSED, NOT_EXPECTED, RUNNING,
                                     WAITING_DEPENDENCY, RunRecord)

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HOST = socket.gethostname()


def _utc(y, m, d, hh, mm, ss=0):
    """An IST wall-clock instant, returned in UTC (what `utcnow` yields)."""
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=IST).astimezone(UTC)


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode


class _Spy:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
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


def _seed(out_dir, job, started_utc, status_=COMPLETED, **fields):
    rec = RunRecord(job=job, run_id=fields.pop("run_id", f"seed-{job}-{started_utc:%H%M%S}"),
                    started_at=started_utc.isoformat(), completed_at=started_utc.isoformat(),
                    duration=1.0, runner_status=status_, **fields)
    records.write_record(out_dir, rec)
    return rec


def _run(tmp_path, job, clock, spy=None, **kw):
    out_dir = kw.pop("out_dir", str(tmp_path / "ss"))
    spy = spy or _Spy()
    code = runner.run_job(job, out_dir=out_dir, business_root=str(tmp_path / "biz"),
                          repo_root=str(tmp_path / "repo"), python_executable="python.exe",
                          subprocess_run=spy, utcnow=clock.utcnow, sleep=clock.sleep,
                          clock_monotonic=clock.monotonic, **kw)
    latest = records.read_latest(out_dir, job)
    full = records.read_record(os.path.join(out_dir, latest["path"])) if latest else None
    return code, spy, full


def _seed_upstream_for(out_dir, job, when_utc):
    """Seed an in-window COMPLETED upstream for `job`'s own occurrence at `when_utc`."""
    dep = config.DEPENDENCIES.get(job)
    if not dep:
        return
    occ = config.resolve_occurrence(config.JOB_REGISTRY[job], when_utc)
    start, _ = config.window_for_date(config.JOB_REGISTRY[dep], occ.scheduled_date)
    _seed(out_dir, dep, (start + dt.timedelta(minutes=1)).astimezone(UTC))


# --- occurrence attribution (pure) -----------------------------------------------------------

@pytest.mark.parametrize("job,ist,expected_date,expected_class", [
    ("intelligence_am", (2026, 10, 21, 6, 39), "2026-10-21", config.EARLY),
    ("intelligence_am", (2026, 10, 21, 6, 40), "2026-10-21", config.IN_WINDOW),
    ("intelligence_am", (2026, 10, 21, 7, 40), "2026-10-21", config.LATE),
    ("intelligence_pm", (2026, 10, 21, 6, 48), "2026-10-20", config.LATE),
    ("post", (2026, 10, 21, 6, 48), "2026-10-20", config.LATE),
    ("post", (2026, 10, 24, 8, 29), "2026-10-23", config.LATE),     # Saturday -> Friday
    ("post", (2026, 10, 26, 6, 48), "2026-10-23", config.LATE),     # Monday AM -> Friday
    ("intelligence_pm", (2026, 10, 24, 19, 20), "2026-10-23", config.LATE),   # Sat "window"
    ("intelligence_pm", (2026, 10, 21, 19, 14), "2026-10-21", config.EARLY),
    ("post", (2026, 10, 21, 20, 29, 59), "2026-10-21", config.IN_WINDOW),
    ("post", (2026, 10, 21, 20, 30), "2026-10-21", config.LATE),
])
def test_resolve_occurrence_attributes_every_invocation_to_one_occurrence(
        job, ist, expected_date, expected_class):
    occ = config.resolve_occurrence(config.JOB_REGISTRY[job], _utc(*ist))
    assert occ.scheduled_date.isoformat() == expected_date
    assert occ.invocation == expected_class
    assert str(occ.window_start.tzinfo) == "Asia/Kolkata"


# --- A-F: window start/end at the boundaries --------------------------------------------------

def test_a_intelligence_am_at_0639_is_early_and_launches_no_child(tmp_path):
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 21, 6, 39)))
    assert code == 0 and spy.calls == []
    assert full["runner_status"] == EARLY
    assert full["child_started"] is False and full["child_exit_code"] is None
    assert full["scheduled_date"] == "2026-10-21" and full["invocation"] == config.EARLY
    assert "before the execution window" in full["error"]
    assert not os.path.exists(lock_path(str(tmp_path / "ss")))   # never even tried the lock


def test_b_intelligence_am_at_0640_is_allowed(tmp_path):
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 21, 6, 40)))
    assert code == 0 and len(spy.calls) == 1
    assert full["runner_status"] == COMPLETED and full["invocation"] == config.IN_WINDOW


def test_c_pre_at_0700_is_allowed(tmp_path):
    clock = _Clock(_utc(2026, 10, 21, 7, 0))
    _seed_upstream_for(str(tmp_path / "ss"), "pre", clock.utc)
    code, spy, full = _run(tmp_path, "pre", clock)
    assert code == 0 and len(spy.calls) == 1 and full["runner_status"] == COMPLETED


def test_d_intelligence_pm_at_1914_is_early_and_launches_no_child(tmp_path):
    code, spy, full = _run(tmp_path, "intelligence_pm", _Clock(_utc(2026, 10, 21, 19, 14)))
    assert code == 0 and spy.calls == []
    assert full["runner_status"] == EARLY and full["scheduled_date"] == "2026-10-21"


def test_e_intelligence_pm_at_1915_is_allowed(tmp_path):
    code, spy, full = _run(tmp_path, "intelligence_pm", _Clock(_utc(2026, 10, 21, 19, 15)))
    assert code == 0 and len(spy.calls) == 1 and full["runner_status"] == COMPLETED


def test_f_post_at_1930_is_allowed(tmp_path):
    clock = _Clock(_utc(2026, 10, 21, 19, 30))
    _seed_upstream_for(str(tmp_path / "ss"), "post", clock.utc)
    code, spy, full = _run(tmp_path, "post", clock)
    assert code == 0 and len(spy.calls) == 1 and full["runner_status"] == COMPLETED


def test_early_invocation_never_waits_for_the_window_to_open(tmp_path):
    clock = _Clock(_utc(2026, 10, 21, 19, 14, 59))

    def no_sleep(_s):
        raise AssertionError("an EARLY invocation must never wait for its window")

    clock.sleep = no_sleep
    code, spy, full = _run(tmp_path, "intelligence_pm", clock)
    assert spy.calls == [] and full["runner_status"] == EARLY


# --- G: previous-day jobs invoked the next morning --------------------------------------------

@pytest.mark.parametrize("job,ist,belongs_to", [
    ("intelligence_pm", (2026, 10, 21, 6, 48, 44), "2026-10-20"),   # the incident
    ("post", (2026, 10, 21, 6, 48, 44), "2026-10-20"),
    ("intelligence_pm", (2026, 10, 24, 8, 29, 10), "2026-10-23"),   # Saturday recurrence
    ("post", (2026, 10, 24, 8, 29, 10), "2026-10-23"),
    ("post", (2026, 10, 26, 6, 48), "2026-10-23"),                  # Monday after a weekend
])
def test_g_previous_days_job_invoked_next_morning_launches_no_child(tmp_path, job, ist,
                                                                    belongs_to):
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(*ist))
    # Even with a same-morning "upstream" record present (as on 2026-10-21, when the stale PM
    # launched in the same millisecond), nothing may launch.
    _seed(out_dir, "intelligence_pm", clock.utc)
    code, spy, full = _run(tmp_path, job, clock)
    assert code == 0 and spy.calls == []
    assert full["runner_status"] == EXPIRED and full["child_started"] is False
    assert full["scheduled_date"] == belongs_to and full["invocation"] == config.LATE
    assert f"of the {belongs_to} occurrence" in full["error"]
    assert not os.path.exists(lock_path(out_dir))


def test_g_a_missed_job_never_becomes_todays_scheduled_job_in_status(tmp_path):
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 6, 48))
    _run(tmp_path, "post", clock)
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 21, 19, 45, tzinfo=IST),
                              calendar=_Calendar(True))
    row = s["jobs"]["post"]
    assert row["runner_status"] is None            # not today's run
    assert row["missed_state"] == "WAITING"        # today's own window is still open
    assert row["rejected_invocations"][0]["scheduled_date"] == "2026-10-20"
    assert row["rejected_invocations"][0]["kind"] == EXPIRED


# --- H/I: dependency re-check after waiting ---------------------------------------------------

def test_h_dependency_completing_after_the_deadline_never_launches(tmp_path):
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 20, 25))      # 5 minutes of POST window left
    deadline = _utc(2026, 10, 21, 20, 30)

    def upstream_appears(now):
        if now >= deadline:
            _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 19, 16), run_id="late-pm")

    clock.on_sleep = upstream_appears
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=60)
    assert code == 0 and spy.calls == []
    assert full["runner_status"] == EXPIRED and full["dependency_status"] == "TIMED_OUT"


def test_i_dependency_completing_within_the_window_is_allowed(tmp_path):
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 19, 30))

    def upstream_appears(now):
        if now >= _utc(2026, 10, 21, 19, 45):
            _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 19, 40), run_id="pm-ok")

    clock.on_sleep = upstream_appears
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=60)
    assert code == 0 and len(spy.calls) == 1
    assert full["dependency_status"] == "SATISFIED" and full["dependency_run_id"] == "pm-ok"


def test_window_is_rechecked_after_the_dependency_wait_not_only_at_invocation(tmp_path,
                                                                             monkeypatch):
    """The dependency is observed SATISFIED inside the window, but the clock crosses the
    deadline before the post-wait re-check - the first window check passing is not enough."""
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 20, 29, 50))
    real_wait = dependency.wait

    def wait_then_jump(*a, **kw):
        result = real_wait(*a, **kw)
        clock.utc = _utc(2026, 10, 21, 20, 30, 1)   # e.g. machine slept right after
        return result

    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 19, 20))
    monkeypatch.setattr(dependency, "wait", wait_then_jump)
    # Make the quick check PENDING so the wait path runs: an in-flight upstream record.
    monkeypatch.setattr(dependency, "check", _sequence(
        [(dependency.PENDING, None), (dependency.SATISFIED, {"run_id": "x"})] * 5))
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=1)
    assert spy.calls == [] and full["runner_status"] == EXPIRED
    assert "closed after the dependency wait" in full["error"]


def _sequence(values):
    it = iter(values)
    return lambda *a, **k: next(it)


def test_upstream_from_another_occurrence_never_satisfies(tmp_path):
    """The 06:48 PM run (attributed to the PREVIOUS evening) must never satisfy this
    evening's POST, although it started on the same calendar date."""
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 6, 48), run_id="stale-pm")
    outcome, rec = dependency.check(out_dir, "intelligence_pm", dt.date(2026, 10, 21), IST)
    assert outcome == dependency.PENDING and rec is None
    outcome, rec = dependency.check(out_dir, "intelligence_pm", dt.date(2026, 10, 20), IST)
    assert outcome == dependency.SATISFIED and rec["run_id"] == "stale-pm"


def test_an_early_upstream_rejection_is_ignored_not_a_failure(tmp_path):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 19, 14), status_=EARLY,
          scheduled_date="2026-10-21", invocation=config.EARLY)
    outcome, _ = dependency.check(out_dir, "intelligence_pm", dt.date(2026, 10, 21), IST)
    assert outcome == dependency.PENDING


def test_waits_are_capped_by_the_runner_budget_before_task_schedulers_time_limit(tmp_path):
    """2026-10-21: the 06:48 POST was killed by PT50M while waiting, leaving
    WAITING_DEPENDENCY. The runner now gives up (and records it) at 45 min."""
    clock = _Clock(_utc(2026, 10, 21, 19, 30))
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=60)
    assert spy.calls == [] and full["runner_status"] == EXPIRED
    waited = (clock.utc - _utc(2026, 10, 21, 19, 30)).total_seconds()
    assert waited <= config.RUNNER_WAIT_BUDGET_SECONDS + 60
    assert waited < config.EXECUTION_TIME_LIMIT_SECONDS
    assert "runner wait budget end" in full["error"]


def test_dependency_starting_late_inside_the_window_still_satisfies(tmp_path):
    out_dir = str(tmp_path / "ss")
    clock = _Clock(_utc(2026, 10, 21, 19, 30))
    pm_running = _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 19, 50),
                       status_=RUNNING, run_id="pm-late", hostname="", pid=0)

    def finish(now):
        if now >= _utc(2026, 10, 21, 19, 58):
            pm_running.runner_status = COMPLETED
            records.write_record(out_dir, pm_running)

    clock.on_sleep = finish
    code, spy, full = _run(tmp_path, "post", clock, dependency_poll_s=60)
    assert len(spy.calls) == 1 and full["dependency_run_id"] == "pm-late"


# --- runner self-finalization: no orphan from a live code path ---------------------------------

def test_runner_exception_after_the_running_checkpoint_is_finalized_failed_and_unlocks(
        tmp_path, monkeypatch):
    """The 2026-10-19 / 2026-10-21 RUNNING orphans: an exception between the checkpoint and the
    child launch. Now finalized as FAILED (exit 4) with the error text, lock released."""
    out_dir = str(tmp_path / "ss")
    real_write = records.write_record
    calls = {"n": 0}

    def flaky_write(out, rec):
        calls["n"] += 1
        if rec.runner_status == RUNNING and rec.child_phase == "LAUNCHING" and calls["n"] < 99:
            calls["n"] = 99
            raise PermissionError(5, "Access is denied")
        return real_write(out, rec)

    monkeypatch.setattr(records, "write_record", flaky_write)
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 21, 6, 41)))
    assert code == 4 and spy.calls == []
    assert full["runner_status"] == FAILED and full["child_started"] is False
    assert "PermissionError" in full["error"]
    assert not os.path.exists(lock_path(out_dir))


def test_os_replace_sharing_violation_is_retried(tmp_path, monkeypatch):
    real_replace = os.replace
    failures = {"left": 3}

    def flaky_replace(src, dst):
        if failures["left"]:
            failures["left"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(records.os, "replace", flaky_replace)
    monkeypatch.setattr(records, "REPLACE_RETRY_SLEEP_S", 0.0)
    rec = RunRecord(job="post", run_id="r1", started_at=_utc(2026, 10, 21, 19, 30).isoformat())
    path = records.write_record(str(tmp_path), rec)
    assert records.read_record(path)["run_id"] == "r1"
    assert records.read_latest(str(tmp_path), "post")["run_id"] == "r1"


def test_child_nonzero_is_still_child_nonzero_and_records_child_phase(tmp_path):
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 21, 6, 41)),
                           spy=_Spy(returncode=30))
    assert code == 30 and full["runner_status"] == CHILD_NONZERO
    assert full["child_phase"] == "LAUNCHED"


def test_production_launcher_checkpoints_the_child_pid_before_waiting(tmp_path, monkeypatch):
    """The default (Popen) path records child_pid while the child is still running."""
    seen = {}

    class _FakePopen:
        def __init__(self, command, **kwargs):
            self.pid = 424242
            self.returncode = None
            self.kwargs = kwargs

        def wait(self):
            rec = records.read_record(records.run_record_path(str(tmp_path / "ss"), seen["id"]))
            seen["mid_run"] = rec
            self.returncode = 0
            return 0

    real_new_id = records.new_run_id

    def capture_id(now=None):
        seen["id"] = real_new_id(now)
        return seen["id"]

    monkeypatch.setattr(records, "new_run_id", capture_id)
    monkeypatch.setattr(runner.subprocess, "Popen", _FakePopen)
    clock = _Clock(_utc(2026, 10, 21, 6, 41))
    code = runner.run_job("intelligence_am", out_dir=str(tmp_path / "ss"),
                          business_root=str(tmp_path / "biz"), repo_root=str(tmp_path / "repo"),
                          utcnow=clock.utcnow, sleep=clock.sleep,
                          clock_monotonic=clock.monotonic)
    assert code == 0
    assert seen["mid_run"]["runner_status"] == RUNNING
    assert seen["mid_run"]["child_pid"] == 424242
    assert seen["mid_run"]["child_phase"] == "LAUNCHED"
    assert "shell" not in _FakePopen("x").kwargs


# --- J-N: orphan classification + reconciliation ----------------------------------------------

def _probe(states):
    """Fake process probe: {pid: state}."""
    def probe(pid, _started):
        return {"state": states.get(pid, orphans.DEAD), "created_at": None}
    return probe


def _stale(out_dir, run_id, job="intelligence_pm", status_=RUNNING, started=None, **fields):
    started = started or _utc(2026, 10, 21, 6, 48, 44)
    rec = RunRecord(job=job, run_id=run_id, started_at=started.isoformat(), hostname=HOST,
                    pid=fields.pop("pid", 14996), runner_status=status_,
                    log_path=f"logs/2026-10-21/{job}_064844.log", **fields)
    path = records.run_record_path(out_dir, run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec.to_dict(), fh, indent=2)
    return path


NOW = _utc(2026, 10, 24, 12, 0)


def test_j_stale_running_record_with_dead_runner_and_dead_child_is_reconciled(tmp_path):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-dead", child_pid=777, child_phase="LAUNCHED",
           child_started_at=_utc(2026, 10, 21, 6, 49).isoformat())
    res = reconcile.reconcile(out_dir, execute=True, now=NOW, hostname=HOST,
                              probe=_probe({}), business_root=str(tmp_path / "biz"))
    assert res["counts"]["RECONCILED"] == 1
    marker = records.read_reconciliation(out_dir, "r-dead")
    assert marker["effective_status"] == ABANDONED
    assert marker["original_runner_status"] == RUNNING
    assert marker["abandoned_kind"] == "RUNNER_AND_CHILD_TERMINATED_UNFINALIZED"


def test_j_waiting_dependency_with_dead_runner_is_reconciled(tmp_path):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-wait", job="post", status_=WAITING_DEPENDENCY, pid=8380)
    res = reconcile.reconcile(out_dir, execute=True, now=NOW, hostname=HOST,
                              probe=_probe({}), business_root=str(tmp_path / "biz"))
    assert res["counts"]["RECONCILED"] == 1


@pytest.mark.parametrize("states,fields,expected", [
    ({14996: orphans.ALIVE}, {}, orphans.ACTIVE),                          # runner alive
    ({14996: orphans.UNKNOWN}, {}, orphans.UNVERIFIABLE),                  # cannot tell
    ({777: orphans.ALIVE}, {"child_pid": 777, "child_phase": "LAUNCHED"},  # runner dead,
     orphans.ACTIVE),                                                      # child alive
    ({777: orphans.UNKNOWN}, {"child_pid": 777, "child_phase": "LAUNCHED"},
     orphans.UNVERIFIABLE),
])
def test_k_an_active_or_unverifiable_job_is_never_reconciled(tmp_path, states, fields,
                                                             expected):
    out_dir = str(tmp_path / "ss")
    path = _stale(out_dir, "r-live", **fields)
    before = open(path, "rb").read()
    res = reconcile.reconcile(out_dir, execute=True, now=NOW, hostname=HOST,
                              probe=_probe(states), business_root=str(tmp_path / "biz"))
    assert res["rows"][0]["classification"] == expected
    assert res["rows"][0]["action"] == "LEFT_ALONE"
    assert records.read_reconciliation(out_dir, "r-live") is None
    assert open(path, "rb").read() == before


def test_k_a_live_lock_holder_is_never_reconciled_and_the_lock_is_never_touched(tmp_path):
    out_dir = str(tmp_path / "ss")
    real_now = dt.datetime.now(UTC)   # the record must post-date this process, or it is reuse
    _stale(out_dir, "r-holder", pid=os.getpid(), started=real_now)
    with open(lock_path(out_dir), "w", encoding="utf-8") as fh:
        json.dump({"run_id": "r-holder", "pid": os.getpid(), "hostname": HOST,
                   "acquired_at": real_now.isoformat()}, fh)
    lock_before = open(lock_path(out_dir), "rb").read()
    res = reconcile.reconcile(out_dir, execute=True, now=real_now, hostname=HOST,
                              business_root=str(tmp_path / "biz"))   # REAL probe: this process
    assert res["rows"][0]["classification"] == orphans.ACTIVE
    assert open(lock_path(out_dir), "rb").read() == lock_before


def test_k_reconciling_a_dead_lock_owner_leaves_the_lock_to_the_next_acquire(tmp_path):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-stale-lock", child_phase="NOT_LAUNCHED")
    with open(lock_path(out_dir), "w", encoding="utf-8") as fh:
        json.dump({"run_id": "r-stale-lock", "pid": 14996, "hostname": HOST,
                   "acquired_at": NOW.isoformat()}, fh)
    reconcile.reconcile(out_dir, execute=True, now=NOW, hostname=HOST, probe=_probe({}),
                        business_root=str(tmp_path / "biz"))
    assert os.path.exists(lock_path(out_dir))                  # reconciliation never removes it
    assert records.read_reconciliation(out_dir, "r-stale-lock")["abandoned_kind"] == \
        "RUNNER_TERMINATED_BEFORE_CHILD_LAUNCH"


def test_l_a_missing_log_alone_never_proves_an_orphan(tmp_path):
    out_dir = str(tmp_path / "ss")
    # Runner alive, log missing -> ACTIVE.
    _stale(out_dir, "r-a")
    v = orphans.classify(records.read_record(records.run_record_path(out_dir, "r-a")),
                         out_dir=out_dir, now=NOW, hostname=HOST,
                         probe=_probe({14996: orphans.ALIVE}))
    assert v["evidence"]["log_exists"] is False and v["classification"] == orphans.ACTIVE
    # Runner dead, log missing, but the run is recent and its child unknowable -> UNVERIFIABLE.
    _stale(out_dir, "r-b", started=NOW - dt.timedelta(minutes=30))
    v = orphans.classify(records.read_record(records.run_record_path(out_dir, "r-b")),
                         out_dir=out_dir, now=NOW, hostname=HOST, probe=_probe({}))
    assert v["evidence"]["log_exists"] is False and v["classification"] == orphans.UNVERIFIABLE


def test_l_a_recently_written_log_keeps_a_legacy_record_unverifiable(tmp_path):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-log", started=NOW - dt.timedelta(hours=5))
    log = os.path.join(out_dir, "logs", "2026-10-21", "intelligence_pm_064844.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    open(log, "w").close()
    recent = (NOW - dt.timedelta(minutes=10)).timestamp()
    os.utime(log, (recent, recent))
    v = orphans.classify(records.read_record(records.run_record_path(out_dir, "r-log")),
                         out_dir=out_dir, now=NOW, hostname=HOST, probe=_probe({}))
    assert v["classification"] == orphans.UNVERIFIABLE


def test_cross_host_and_pid_reuse(tmp_path):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-x")
    rec = records.read_record(records.run_record_path(out_dir, "r-x"))
    assert orphans.classify(rec, out_dir=out_dir, now=NOW, hostname="OTHER-HOST",
                            probe=_probe({}))["classification"] == orphans.UNVERIFIABLE
    v = orphans.classify(dict(rec, child_phase="NOT_LAUNCHED"), out_dir=out_dir, now=NOW,
                         hostname=HOST, probe=_probe({14996: orphans.REUSED}))
    assert v["classification"] == orphans.ABANDONED
    assert any("PID reused" in r for r in v["reasons"])


def test_real_probe_reports_this_process_alive_and_reuse_by_creation_time():
    me = orphans.probe_process(os.getpid(), dt.datetime.now(UTC))
    assert me["state"] == orphans.ALIVE
    if me["created_at"]:   # win32: creation time known -> a record "older" than us is reuse
        old = orphans.probe_process(os.getpid(), dt.datetime(2000, 1, 1, tzinfo=UTC))
        assert old["state"] == orphans.REUSED


def test_m_two_reconciliation_passes_are_idempotent_and_dry_run_writes_nothing(tmp_path):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-1")
    _stale(out_dir, "r-2", job="post", status_=WAITING_DEPENDENCY, pid=8380)
    dry = reconcile.reconcile(out_dir, now=NOW, hostname=HOST, probe=_probe({}),
                              business_root=str(tmp_path / "biz"))
    assert dry["counts"]["WOULD_RECONCILE"] == 2
    assert not os.path.exists(os.path.join(out_dir, records.RECONCILIATIONS_SUBDIR))
    first = reconcile.reconcile(out_dir, execute=True, now=NOW, hostname=HOST,
                                probe=_probe({}), business_root=str(tmp_path / "biz"))
    snap = {f: open(os.path.join(out_dir, "reconciliations", f), "rb").read()
            for f in os.listdir(os.path.join(out_dir, "reconciliations"))}
    second = reconcile.reconcile(out_dir, execute=True, now=NOW + dt.timedelta(hours=1),
                                 hostname=HOST, probe=_probe({}),
                                 business_root=str(tmp_path / "biz"))
    assert first["counts"]["RECONCILED"] == 2
    assert second["counts"]["RECONCILED"] == 0 and second["counts"]["ALREADY_RECONCILED"] == 2
    assert snap == {f: open(os.path.join(out_dir, "reconciliations", f), "rb").read()
                    for f in os.listdir(os.path.join(out_dir, "reconciliations"))}
    assert records.write_reconciliation(out_dir, {"run_id": "r-1"}) is False   # exclusive


def test_n_successful_history_and_original_evidence_are_preserved(tmp_path):
    out_dir = str(tmp_path / "ss")
    ok = _seed(out_dir, "intelligence_am", _utc(2026, 10, 20, 6, 40), run_id="ok-run")
    ok_path = records.run_record_path(out_dir, ok.run_id)
    stale_path = _stale(out_dir, "r-orphan")
    before = {p: open(p, "rb").read() for p in (ok_path, stale_path)}
    latest_before = open(records.latest_path(out_dir, "intelligence_pm"), "rb").read() \
        if os.path.exists(records.latest_path(out_dir, "intelligence_pm")) else None
    reconcile.reconcile(out_dir, execute=True, now=NOW, hostname=HOST, probe=_probe({}),
                        business_root=str(tmp_path / "biz"))
    assert {p: open(p, "rb").read() for p in (ok_path, stale_path)} == before
    marker = records.read_reconciliation(out_dir, "r-orphan")
    import hashlib
    assert marker["original_record_sha256"] == hashlib.sha256(before[stale_path]).hexdigest()
    assert marker["original_started_at"] == _utc(2026, 10, 21, 6, 48, 44).isoformat()
    assert records.read_reconciliation(out_dir, "ok-run") is None   # terminal: never touched
    if latest_before is not None:
        assert open(records.latest_path(out_dir, "intelligence_pm"), "rb").read() == latest_before
    # effective status reflects the marker; never a success
    assert orphans.effective_status(out_dir, records.read_record(stale_path)) == ABANDONED


def test_an_abandoned_upstream_fails_fast_instead_of_blocking_until_the_deadline(tmp_path,
                                                                                monkeypatch):
    """No stale record may block a session: an upstream checkpoint whose runner is verifiably
    gone (child never launched) is a terminal non-satisfying outcome."""
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "pm-dead", started=_utc(2026, 10, 21, 19, 16), child_phase="NOT_LAUNCHED",
           scheduled_date="2026-10-21", invocation=config.IN_WINDOW)
    monkeypatch.setattr(orphans, "probe_process", _probe({}))
    clock = _Clock(_utc(2026, 10, 21, 19, 30))

    def no_sleep(_s):
        raise AssertionError("must not wait on an abandoned upstream")

    clock.sleep = no_sleep
    code, spy, full = _run(tmp_path, "post", clock)
    assert code == 5 and spy.calls == [] and full["runner_status"] == DEPENDENCY_FAILED


def test_yesterdays_orphan_never_blocks_todays_session(tmp_path, monkeypatch):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "pm-yesterday", started=_utc(2026, 10, 20, 19, 16))   # alive per probe
    monkeypatch.setattr(orphans, "probe_process", _probe({14996: orphans.ALIVE}))
    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 21, 19, 16), run_id="pm-today")
    code, spy, full = _run(tmp_path, "post", _Clock(_utc(2026, 10, 21, 19, 30)))
    assert len(spy.calls) == 1 and full["dependency_run_id"] == "pm-today"


# --- status: orphans are never healthy; rejected invocations are never success --------------

class _Calendar:
    def __init__(self, is_session):
        self._v = is_session

    def is_session(self, _d):
        return self._v


def test_status_reports_an_orphan_as_attention_never_healthy(tmp_path, monkeypatch):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "r-orphan", started=_utc(2026, 10, 19, 7, 8))
    monkeypatch.setattr(orphans, "probe_process", _probe({}))
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 22, 6, 0, tzinfo=IST),
                              calendar=_Calendar(True))
    assert s["overall"] == "ATTENTION"
    assert [o["run_id"] for o in s["orphan_records"]] == ["r-orphan"]
    assert "Unresolved orphans:  1" in status.render_human(s)


def test_status_todays_orphan_is_orphan_suspected_then_abandoned_once_reconciled(
        tmp_path, monkeypatch):
    out_dir = str(tmp_path / "ss")
    _stale(out_dir, "am-today", job="intelligence_am", started=_utc(2026, 10, 22, 6, 41),
           child_phase="NOT_LAUNCHED", scheduled_date="2026-10-22",
           invocation=config.IN_WINDOW)
    monkeypatch.setattr(orphans, "probe_process", _probe({}))
    now = dt.datetime(2026, 10, 22, 6, 55, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"), now=now,
                              calendar=_Calendar(True))
    assert s["jobs"]["intelligence_am"]["runner_status"] == orphans.ORPHAN_SUSPECTED
    assert s["overall"] == "ATTENTION"
    reconcile.reconcile(out_dir, execute=True, hostname=HOST, probe=_probe({}),
                        business_root=str(tmp_path / "biz"))
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"), now=now,
                              calendar=_Calendar(True))
    row = s["jobs"]["intelligence_am"]
    assert row["runner_status"] == ABANDONED and row["reconciled"] is True
    assert row["recorded_runner_status"] == RUNNING      # original evidence still visible
    assert s["overall"] == "ATTENTION" and s["orphan_records"] == []


def test_status_never_counts_an_early_rejection_as_todays_run(tmp_path):
    out_dir = str(tmp_path / "ss")
    _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 22, 6, 39)))
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 22, 7, 45, tzinfo=IST),
                              calendar=_Calendar(True))
    row = s["jobs"]["intelligence_am"]
    assert row["runner_status"] is None and row["missed_state"] == MISSED
    assert row["rejected_invocations"][0]["kind"] == EARLY
    assert "Early rejections:    1" in status.render_human(s)


def test_status_distinguishes_child_nonzero_dependency_failed_and_lock_conflict(tmp_path):
    out_dir = str(tmp_path / "ss")
    _seed(out_dir, "intelligence_am", _utc(2026, 10, 22, 6, 41), status_=CHILD_NONZERO,
          child_exit_code=30)
    _seed(out_dir, "pre", _utc(2026, 10, 22, 7, 1), status_=DEPENDENCY_FAILED)
    _seed(out_dir, "intelligence_pm", _utc(2026, 10, 22, 19, 16), status_="LOCKED")
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 22, 19, 20, tzinfo=IST),
                              calendar=_Calendar(True))
    text = status.render_human(s)
    assert "Child nonzero:       1" in text
    assert "Dependency failures: 1" in text
    assert "Lock conflicts:      1" in text
    assert s["overall"] == "ATTENTION"


def test_status_previous_occurrence_shows_last_nights_miss_for_information_only(tmp_path):
    out_dir = str(tmp_path / "ss")
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 21, 6, 0, tzinfo=IST),
                              calendar=_Calendar(True))
    assert s["jobs"]["post"]["previous_occurrence"] == {
        "scheduled_date": "2026-10-20", "state": MISSED, "runner_status": None, "run_id": None}
    assert s["overall"] == "HEALTHY"   # information only: never feeds today's counters


# --- O: manual replay never counterfeits scheduled status -------------------------------------

def test_o_a_manual_replay_never_counterfeits_the_scheduled_run(tmp_path):
    """A manual `production_orchestrator post` (no scheduler record, e.g. 2026-10-21 21:17)
    updates the business latest.json, but the scheduled POST occurrence stays MISSED and the
    business result is not attributed to it."""
    out_dir = str(tmp_path / "ss")
    biz = tmp_path / "biz" / "production_runs" / "POST"
    biz.mkdir(parents=True)
    (biz / "latest.json").write_text(json.dumps({
        "orchestrator_status": "SUCCESS", "run_id": "manual",
        "started_at": _utc(2026, 10, 21, 21, 17).isoformat()}), encoding="utf-8")
    _seed(out_dir, "post", _utc(2026, 10, 21, 21, 16), status_=EXPIRED, child_started=False)
    s = status.overall_status(out_dir, business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 21, 21, 30, tzinfo=IST),
                              calendar=_Calendar(True))
    row = s["jobs"]["post"]
    assert row["runner_status"] is None and row["business_status"] is None
    assert row["missed_state"] == MISSED
    assert row["last_run"]["business_status"] == "SUCCESS"   # raw history only


# --- holiday / weekend: calendar semantics unchanged ------------------------------------------

def test_nse_holiday_weekday_invocation_in_window_still_launches_the_child(tmp_path):
    """2026-10-20 (Tue) is an NSE holiday: the runner stays weekday-based and launches; the
    child itself SKIPs (session semantics unchanged); status shows NOT_EXPECTED."""
    from core.trading_calendar import SessionCalendar
    assert SessionCalendar().is_session(dt.date(2026, 10, 20)) is False
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 20, 6, 40)))
    assert len(spy.calls) == 1 and full["invocation"] == config.IN_WINDOW
    s = status.overall_status(str(tmp_path / "ss"), business_root=str(tmp_path / "biz"),
                              now=dt.datetime(2026, 10, 20, 9, 0, tzinfo=IST))
    assert s["jobs"]["intelligence_am"]["missed_state"] == NOT_EXPECTED


def test_weekend_invocation_inside_clock_window_is_never_a_scheduled_run(tmp_path):
    code, spy, full = _run(tmp_path, "intelligence_pm", _Clock(_utc(2026, 10, 24, 19, 20)))
    assert spy.calls == [] and full["runner_status"] == EXPIRED
    assert full["scheduled_date"] == "2026-10-23"


# --- audit: read-only incident replay -------------------------------------------------------

def test_audit_flags_the_pre_fix_out_of_window_execution_and_writes_nothing(tmp_path):
    out_dir = str(tmp_path / "ss")
    # A record exactly like 2026-10-24 08:29 (old runner: Saturday window, COMPLETED).
    _seed(out_dir, "post", _utc(2026, 10, 24, 8, 29, 10), run_id="sat",
          window_start="2026-10-24T19:30:00+05:30")
    before = sorted(os.listdir(out_dir))
    res = audit.audit(out_dir, now=NOW, probe=_probe({}), business_root=str(tmp_path / "biz"))
    row = res["rows"][0]
    assert row["verdict"] == audit.OUT_OF_WINDOW_EXECUTION
    assert row["attributed_scheduled_date"] == "2026-10-23"
    assert row["current_runner_would"].startswith("REJECT as EXPIRED")
    assert sorted(os.listdir(out_dir)) == before


# --- P/Q: Windows tasks untouched; child commands / public-private behaviour unchanged --------

PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shadow_scheduler")


def test_p_no_python_module_can_mutate_a_windows_task():
    banned = ("Register-ScheduledTask", "Unregister-ScheduledTask", "Set-ScheduledTask",
              "schtasks", "Start-ScheduledTask", "Disable-ScheduledTask", "TaskScheduler")
    for name in os.listdir(PKG_DIR):
        if name.endswith(".py"):
            src = open(os.path.join(PKG_DIR, name), encoding="utf-8").read()
            for token in banned:
                assert token not in src, (name, token)


def _calls(name):
    import ast
    tree = ast.parse(open(os.path.join(PKG_DIR, name), encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            label = (f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute)
                     and isinstance(f.value, ast.Name) else getattr(f, "id", None))
            modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
            modes += [k.value.value for k in node.keywords
                      if k.arg == "mode" and isinstance(k.value, ast.Constant)]
            out.append((label, modes))
    return out


def test_p_new_modules_never_signal_kill_or_write():
    """orphans/audit are read-only; reconcile writes only through records.write_reconciliation.
    None of them kills, signals or deletes anything (orphans' posix-only `os.kill(pid, 0)`
    liveness probe is the single, documented exception and is never reached on win32)."""
    for name in ("orphans.py", "reconcile.py", "audit.py"):
        for label, modes in _calls(name):
            assert label not in ("os.remove", "os.unlink", "os.replace", "os.rename",
                                 "subprocess.run", "subprocess.Popen"), (name, label)
            if label == "os.kill":
                assert name == "orphans.py", name
            if label == "open":
                assert not any(set(str(m)) & set("wax+") for m in modes), (name, modes)


def test_q_child_commands_and_argv_are_unchanged(tmp_path):
    """Public/private behaviour is decided by the children; the scheduler still launches
    exactly the registry's .bat with no extra argument, unchanged by this packet."""
    assert {j: s.bat_relpath for j, s in config.JOB_REGISTRY.items()} == {
        "intelligence_am": "scripts/run_intelligence_refresh.bat",
        "pre": "scripts/run_production_pre.bat",
        "intelligence_pm": "scripts/run_intelligence_refresh.bat",
        "post": "scripts/run_production_post.bat"}
    assert {j: s.scheduled_time for j, s in config.JOB_REGISTRY.items()} == {
        "intelligence_am": "06:40", "pre": "07:00", "intelligence_pm": "19:15", "post": "19:30"}
    code, spy, full = _run(tmp_path, "intelligence_am", _Clock(_utc(2026, 10, 21, 6, 41)))
    assert spy.calls == [[os.path.normpath(os.path.join(str(tmp_path / "repo"),
                                                        "scripts/run_intelligence_refresh.bat"))]]

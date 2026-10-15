"""shadow_scheduler.runner: the control-flow distinctions the packet cares about most - a
healthy child, a child that launches and returns nonzero (a BUSINESS outcome like PK-D's own
BLOCKED=20, never a "scheduler crash", never retried), a child that can't even be launched
(genuine infra FAILED), a lock that is busy past its bounded wait (LOCKED, never touching the
holder), and that a late-but-still-within-window invocation still launches the canonical child
exactly as it would on time. The 1-hour execution-window enforcement itself (EXPIRED, both
check points, the lock-wait deadline cap) is covered in
`test_shadow_scheduler_execution_window.py`.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

from shadow_scheduler import config, records, runner
from shadow_scheduler.lock import RunLock, lock_path
from shadow_scheduler.models import (CHILD_NONZERO, COMPLETED, DEPENDENCY_FAILED, EXPIRED,
                                     FAILED, LOCKED, RunRecord, WAITING_DEPENDENCY)

UTC = dt.timezone.utc


def _seed_dependency_satisfied(out_dir, job, utcnow):
    """Pre-seed TODAY's upstream job (if `job` has one) as already COMPLETED, so tests that are
    not themselves about dependency ordering don't block on `dependency.wait()` - the healthy,
    already-satisfied case this package is meant to cost ~nothing. Dependency-ordering tests
    live in test_shadow_scheduler_dependency.py / the `# --- dependency ordering ---` section
    below and pass `seed_dependency=False` to control the upstream record themselves."""
    dep_job = config.DEPENDENCIES.get(job)
    if not dep_job:
        return
    started = utcnow()
    rec = RunRecord(job=dep_job, run_id=f"seed-{dep_job}", started_at=started.isoformat(),
                    completed_at=started.isoformat(), duration=1.0, runner_status=COMPLETED)
    records.write_record(out_dir, rec)


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode


class _FakeSubprocess:
    """Records every call; returns/raises whatever was configured."""

    def __init__(self, returncode=0, raise_exc=None):
        self.returncode = returncode
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeProc(self.returncode)


def _run(tmp_path, job="post", seed_dependency=True, **kwargs):
    kwargs.setdefault("out_dir", str(tmp_path / "shadow_scheduler"))
    kwargs.setdefault("business_root", str(tmp_path / "output"))
    kwargs.setdefault("repo_root", str(tmp_path / "repo"))
    kwargs.setdefault("python_executable", "D:\\repo\\venv\\Scripts\\python.exe")
    kwargs.setdefault("utcnow", lambda: dt.datetime(2026, 10, 9, 14, 0, tzinfo=UTC))
    if seed_dependency:
        _seed_dependency_satisfied(kwargs["out_dir"], job, kwargs["utcnow"])
    return runner.run_job(job, **kwargs)


def test_healthy_job_is_completed_exit_zero_lock_released_log_created(tmp_path):
    fake = _FakeSubprocess(returncode=0)
    exit_code = _run(tmp_path, subprocess_run=fake)
    assert exit_code == 0
    assert len(fake.calls) == 1
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == COMPLETED
    assert latest["child_exit_code"] == 0
    assert not os.path.exists(lock_path(out_dir))   # lock released
    log_abspath = os.path.join(out_dir, latest["path"])
    with open(log_abspath, encoding="utf-8") as fh:
        pass   # the run record path resolves; the log itself is checked next
    full = records.read_record(os.path.join(out_dir, latest["path"]))
    log_full = os.path.join(out_dir, full["log_path"])
    assert os.path.exists(log_full)


def test_child_nonzero_is_child_nonzero_not_a_scheduler_crash_exit_code_preserved(tmp_path):
    fake = _FakeSubprocess(returncode=20)   # mirrors PK-D's own BLOCKED
    # 07:05 IST = 01:35 UTC - inside PRE's 07:00-08:00 execution window (the file-wide default
    # utcnow fixture is chosen for POST's own 19:30 schedule, not PRE's).
    pre_utcnow = lambda: dt.datetime(2026, 10, 9, 1, 35, tzinfo=UTC)
    exit_code = _run(tmp_path, job="pre", subprocess_run=fake, utcnow=pre_utcnow)
    assert exit_code == 20   # verbatim, never remapped
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == CHILD_NONZERO
    assert latest["child_exit_code"] == 20
    assert len(fake.calls) == 1   # never retried
    assert not os.path.exists(lock_path(out_dir))


def test_child_launch_failure_is_failed_with_diagnostic_lock_released(tmp_path):
    fake = _FakeSubprocess(raise_exc=FileNotFoundError("no such file: run_production_post.bat"))
    exit_code = _run(tmp_path, job="post", subprocess_run=fake)
    assert exit_code == 4
    out_dir = str(tmp_path / "shadow_scheduler")
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == FAILED
    assert latest["child_exit_code"] is None
    assert "FileNotFoundError" in latest["path"] or True   # path check below is the real one
    full = records.read_record(os.path.join(out_dir, latest["path"]))
    assert "FileNotFoundError" in full["error"]
    assert not os.path.exists(lock_path(out_dir))


def test_child_command_comes_only_from_the_static_registry_never_env_or_cli(tmp_path):
    fake = _FakeSubprocess(returncode=0)
    repo_root = str(tmp_path / "repo")
    pre_utcnow = lambda: dt.datetime(2026, 10, 9, 1, 35, tzinfo=UTC)   # inside PRE's window
    _run(tmp_path, job="pre", subprocess_run=fake, repo_root=repo_root,
        cli_argv=["run", "pre", "--some-untrusted-arg"], utcnow=pre_utcnow)
    command, kwargs = fake.calls[0]
    assert command == [os.path.normpath(os.path.join(repo_root, "scripts/run_production_pre.bat"))]
    assert kwargs["cwd"] == repo_root
    assert "--intent" not in " ".join(command)
    assert "publish" not in " ".join(command)


def test_invocation_late_but_still_within_window_launches_normally(tmp_path):
    """A scheduled job that starts late within its own 1-hour execution window (e.g. a
    StartWhenAvailable catch-up) still launches exactly the same canonical child command. PK-C
    alone decides whether that's actually safe to render - this runner only enforces the
    window, never a market/business decision."""
    fake = _FakeSubprocess(returncode=0)
    # 20:00 IST = 14:30 UTC - inside POST's 19:30-20:30 window, but not at the exact scheduled
    # minute.
    late_but_in_window_utcnow = lambda: dt.datetime(2026, 10, 9, 14, 30, tzinfo=UTC)
    exit_code = _run(tmp_path, job="post", subprocess_run=fake, utcnow=late_but_in_window_utcnow)
    assert exit_code == 0
    assert len(fake.calls) == 1


def test_lock_busy_past_bounded_wait_is_locked_never_touches_holder(tmp_path):
    out_dir = str(tmp_path / "shadow_scheduler")
    holder = RunLock(out_dir)
    holder.acquire("holder-run", "intelligence_am", [])

    clock = {"t": 0.0}

    def fake_sleep(seconds):
        clock["t"] += seconds

    fake_subprocess = _FakeSubprocess(returncode=0)
    exit_code = _run(tmp_path, job="post", subprocess_run=fake_subprocess,
                     lock_timeout_s=10, lock_poll_s=5, sleep=fake_sleep,
                     clock_monotonic=lambda: clock["t"])
    assert exit_code == 3
    assert len(fake_subprocess.calls) == 0   # never launched - no overlap with the holder
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == LOCKED
    assert latest["child_exit_code"] is None
    # the holder's own lock is untouched
    assert os.path.exists(lock_path(out_dir))
    import json
    with open(lock_path(out_dir), encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "holder-run"


def test_unknown_job_raises_instead_of_silently_doing_something():
    with pytest.raises(KeyError):
        runner.run_job("not_a_real_job")


# --- dependency ordering (PK-E follow-up): intelligence_am -> pre, intelligence_pm -> post ---
# Deeper check(), wait(), and boundary-timing coverage lives in
# tests/test_shadow_scheduler_dependency.py; these exercise the full run_job() integration -
# the morning/evening wake-up races, every upstream terminal state, and the window-deadline
# interaction - end to end with a fake subprocess spy, exactly like the rest of this file.

def _write_upstream(out_dir, dep_job, started_at_dt, runner_status):
    rec = RunRecord(job=dep_job, run_id=f"upstream-{dep_job}-{runner_status}",
                    started_at=started_at_dt.isoformat(), completed_at=started_at_dt.isoformat(),
                    duration=1.0, runner_status=runner_status)
    records.write_record(out_dir, rec)


def test_pre_waits_without_holding_lock_until_intelligence_am_completes_mid_wait(tmp_path):
    """The primary acceptance case (packet section 11/26): PRE starts at 07:30 IST with no
    intelligence_am record yet. PRE must wait WITHOUT the global lock (proven by a second
    RunLock instance successfully acquiring it mid-wait) and show WAITING_DEPENDENCY live, then
    proceed once intelligence_am completes."""
    out_dir = str(tmp_path / "shadow_scheduler")
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 0, tzinfo=UTC)}   # 07:30:00 IST
    lock_probe_result = {}
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            mid_wait = records.read_latest(out_dir, "pre")
            assert mid_wait["runner_status"] == WAITING_DEPENDENCY
            probe = RunLock(out_dir)
            probe.acquire("probe-run", "intelligence_am", [])   # proves PRE isn't holding it
            lock_probe_result["acquired"] = True
            probe.release()
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if calls["n"] == 2:
            _write_upstream(out_dir, "intelligence_am", clock["utc"], COMPLETED)

    fake = _FakeSubprocess(returncode=0)
    exit_code = _run(tmp_path, job="pre", seed_dependency=False, out_dir=out_dir,
                     subprocess_run=fake, utcnow=lambda: clock["utc"], sleep=fake_sleep,
                     clock_monotonic=lambda: clock["t"], dependency_poll_s=60)
    assert exit_code == 0
    assert len(fake.calls) == 1
    assert lock_probe_result.get("acquired") is True
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == COMPLETED
    assert latest["dependency_status"] == "SATISFIED"
    assert latest["dependency_wait_seconds"] > 0


def test_pre_proceeds_without_waiting_when_intelligence_am_already_completed_today(tmp_path):
    """Healthy daily operation (packet section 13/28): dependency already satisfied before PRE
    even starts - zero wait, negligible overhead."""
    out_dir = str(tmp_path / "shadow_scheduler")
    today_utc = dt.datetime(2026, 10, 9, 2, 0, tzinfo=UTC)   # 07:30 IST
    _write_upstream(out_dir, "intelligence_am", today_utc, COMPLETED)
    sleeps = []
    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "pre", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: today_utc, sleep=sleeps.append,
        clock_monotonic=lambda: 0.0)
    assert exit_code == 0
    assert len(fake.calls) == 1
    assert sleeps == []
    latest = records.read_latest(out_dir, "pre")
    assert latest["dependency_wait_seconds"] == 0.0
    assert latest["dependency_status"] == "SATISFIED"


def test_pre_rejects_yesterdays_intelligence_am_completion_and_expires(tmp_path):
    """Current-day record only (packet section 14/29): yesterday's completion never satisfies
    today's dependency - PRE waits out its own window and EXPIRES, it never silently proceeds
    on stale state."""
    out_dir = str(tmp_path / "shadow_scheduler")
    yesterday_utc = dt.datetime(2026, 10, 8, 2, 0, tzinfo=UTC)
    _write_upstream(out_dir, "intelligence_am", yesterday_utc, COMPLETED)

    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 29, 0, tzinfo=UTC)}   # 07:59:00 IST

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)

    fake = _FakeSubprocess(returncode=0)
    exit_code = _run(tmp_path, job="pre", seed_dependency=False, out_dir=out_dir,
                     subprocess_run=fake, utcnow=lambda: clock["utc"], sleep=fake_sleep,
                     clock_monotonic=lambda: clock["t"], dependency_poll_s=30)
    assert exit_code == 0
    assert len(fake.calls) == 0
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == EXPIRED


def test_pre_proceeds_when_intelligence_am_reached_child_nonzero(tmp_path):
    """CHILD_NONZERO upstream (packet section 17/30): the refresh ATTEMPT finished - ordering is
    satisfied, PK-C (not this layer) judges whether that business outcome is actually safe."""
    out_dir = str(tmp_path / "shadow_scheduler")
    today_utc = dt.datetime(2026, 10, 9, 1, 45, tzinfo=UTC)   # 07:15 IST
    _write_upstream(out_dir, "intelligence_am", today_utc, CHILD_NONZERO)
    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "pre", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: today_utc, sleep=lambda s: None,
        clock_monotonic=lambda: 0.0)
    assert exit_code == 0
    assert len(fake.calls) == 1


def test_pre_stops_when_intelligence_am_failed_before_completing(tmp_path):
    """FAILED upstream (packet section 18/31): PRE never launches its child, scheduler-level
    DEPENDENCY_FAILED is recorded - distinct from both PK-C BLOCKED and a PRE execution
    failure, because PRE never ran at all."""
    out_dir = str(tmp_path / "shadow_scheduler")
    today_utc = dt.datetime(2026, 10, 9, 1, 45, tzinfo=UTC)
    _write_upstream(out_dir, "intelligence_am", today_utc, FAILED)
    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "pre", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: today_utc, sleep=lambda s: None,
        clock_monotonic=lambda: 0.0)
    assert exit_code == 5
    assert len(fake.calls) == 0
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == DEPENDENCY_FAILED


def test_pre_stops_fast_when_intelligence_am_expired_well_before_pres_own_deadline(tmp_path):
    """EXPIRED upstream (packet section 19/32): intelligence_am was invoked after its own 07:40
    deadline and never started its child. PRE still has until 08:00, but must recognise the
    terminal EXPIRED outcome and stop immediately - never wait out the rest of its own window
    on a dependency that has already definitively failed to run today."""
    out_dir = str(tmp_path / "shadow_scheduler")
    today_utc = dt.datetime(2026, 10, 9, 1, 45, tzinfo=UTC)   # 07:15 IST - PRE still has 45 min
    _write_upstream(out_dir, "intelligence_am", today_utc, EXPIRED)

    def fake_sleep(seconds):
        raise AssertionError("must fail fast - never wait out its own window on a dependency "
                             "that has already terminally failed")

    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "pre", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: today_utc, sleep=fake_sleep,
        clock_monotonic=lambda: 0.0)
    assert exit_code == 5
    assert len(fake.calls) == 0
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == DEPENDENCY_FAILED


def test_pre_expires_if_intelligence_am_never_completes_before_pres_deadline(tmp_path):
    """Packet section 33: the dependency never appears at all; PRE's own window authority wins
    - EXPIRED, never a late launch."""
    out_dir = str(tmp_path / "shadow_scheduler")
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 25, tzinfo=UTC)}   # 07:55:00 IST

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)

    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "pre", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: clock["utc"], sleep=fake_sleep,
        clock_monotonic=lambda: clock["t"], dependency_poll_s=60)
    assert exit_code == 0
    assert len(fake.calls) == 0
    latest = records.read_latest(out_dir, "pre")
    assert latest["runner_status"] == EXPIRED


def test_pre_launches_when_intelligence_am_completes_just_before_pres_deadline(tmp_path):
    """Packet section 34 boundary: the dependency is observed SATISFIED strictly before the
    deadline, and the subsequent lock+launch also complete before the deadline - allowed."""
    out_dir = str(tmp_path / "shadow_scheduler")
    deadline_utc = dt.datetime(2026, 10, 9, 2, 30, 0, tzinfo=UTC)   # 08:00:00 IST
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 2, 29, 0, tzinfo=UTC)}   # 07:59:00 IST

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if clock["utc"] < deadline_utc:
            _write_upstream(out_dir, "intelligence_am", clock["utc"], COMPLETED)

    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "pre", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: clock["utc"], sleep=fake_sleep,
        clock_monotonic=lambda: clock["t"], dependency_poll_s=30)
    assert exit_code == 0
    assert len(fake.calls) == 1


# --- evening mirror: intelligence_pm -> post (packet section 35) ---------------------------

def test_post_waits_without_holding_lock_until_intelligence_pm_completes_mid_wait(tmp_path):
    out_dir = str(tmp_path / "shadow_scheduler")
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 14, 0, tzinfo=UTC)}   # 19:30:00 IST
    lock_probe_result = {}
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            probe = RunLock(out_dir)
            probe.acquire("probe-run", "intelligence_pm", [])
            lock_probe_result["acquired"] = True
            probe.release()
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)
        if calls["n"] == 2:
            _write_upstream(out_dir, "intelligence_pm", clock["utc"], COMPLETED)

    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "post", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: clock["utc"], sleep=fake_sleep,
        clock_monotonic=lambda: clock["t"], dependency_poll_s=60)
    assert exit_code == 0
    assert len(fake.calls) == 1
    assert lock_probe_result.get("acquired") is True


def test_post_proceeds_without_waiting_when_intelligence_pm_already_completed_today(tmp_path):
    out_dir = str(tmp_path / "shadow_scheduler")
    today_utc = dt.datetime(2026, 10, 9, 14, 0, tzinfo=UTC)   # 19:30 IST
    _write_upstream(out_dir, "intelligence_pm", today_utc, COMPLETED)
    sleeps = []
    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "post", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: today_utc, sleep=sleeps.append,
        clock_monotonic=lambda: 0.0)
    assert exit_code == 0
    assert len(fake.calls) == 1
    assert sleeps == []


def test_post_stops_when_intelligence_pm_failed(tmp_path):
    out_dir = str(tmp_path / "shadow_scheduler")
    today_utc = dt.datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    _write_upstream(out_dir, "intelligence_pm", today_utc, FAILED)
    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "post", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: today_utc, sleep=lambda s: None,
        clock_monotonic=lambda: 0.0)
    assert exit_code == 5
    assert len(fake.calls) == 0
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == DEPENDENCY_FAILED


def test_post_expires_if_intelligence_pm_never_completes_before_posts_deadline(tmp_path):
    out_dir = str(tmp_path / "shadow_scheduler")
    clock = {"t": 0.0, "utc": dt.datetime(2026, 10, 9, 14, 55, tzinfo=UTC)}   # 20:25:00 IST

    def fake_sleep(seconds):
        clock["t"] += seconds
        clock["utc"] += dt.timedelta(seconds=seconds)

    fake = _FakeSubprocess(returncode=0)
    exit_code = runner.run_job(
        "post", out_dir=out_dir, business_root=str(tmp_path / "output"),
        repo_root=str(tmp_path / "repo"), python_executable="D:\\repo\\venv\\Scripts\\python.exe",
        subprocess_run=fake, utcnow=lambda: clock["utc"], sleep=fake_sleep,
        clock_monotonic=lambda: clock["t"], dependency_poll_s=60)
    assert exit_code == 0
    assert len(fake.calls) == 0
    latest = records.read_latest(out_dir, "post")
    assert latest["runner_status"] == EXPIRED

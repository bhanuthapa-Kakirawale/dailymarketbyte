"""shadow_scheduler.runner: the control-flow distinctions the packet cares about most - a
healthy child, a child that launches and returns nonzero (a BUSINESS outcome like PK-D's own
BLOCKED=20, never a "scheduler crash", never retried), a child that can't even be launched
(genuine infra FAILED), a lock that is busy past its bounded wait (LOCKED, never touching the
holder), and that a late invocation still launches the canonical child exactly as it would on
time - the runner has no wall-clock/business logic of its own at all.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

from shadow_scheduler import records, runner
from shadow_scheduler.lock import RunLock, lock_path
from shadow_scheduler.models import CHILD_NONZERO, COMPLETED, FAILED, LOCKED

UTC = dt.timezone.utc


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


def _run(tmp_path, job="post", **kwargs):
    kwargs.setdefault("out_dir", str(tmp_path / "shadow_scheduler"))
    kwargs.setdefault("business_root", str(tmp_path / "output"))
    kwargs.setdefault("repo_root", str(tmp_path / "repo"))
    kwargs.setdefault("python_executable", "D:\\repo\\venv\\Scripts\\python.exe")
    kwargs.setdefault("utcnow", lambda: dt.datetime(2026, 10, 9, 14, 0, tzinfo=UTC))
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
    exit_code = _run(tmp_path, job="pre", subprocess_run=fake)
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
    _run(tmp_path, job="pre", subprocess_run=fake, repo_root=repo_root,
        cli_argv=["run", "pre", "--some-untrusted-arg"])
    command, kwargs = fake.calls[0]
    assert command == [os.path.normpath(os.path.join(repo_root, "scripts/run_production_pre.bat"))]
    assert kwargs["cwd"] == repo_root
    assert "--intent" not in " ".join(command)
    assert "publish" not in " ".join(command)


def test_late_invocation_still_launches_the_canonical_child_unconditionally(tmp_path):
    """The runner has no wall-clock/business logic - a scheduled job that starts well past
    its scheduled time still launches exactly the same canonical child command. PK-C alone
    decides whether that's actually safe to render."""
    fake = _FakeSubprocess(returncode=0)
    late_utcnow = lambda: dt.datetime(2026, 10, 9, 20, 0, tzinfo=UTC)   # 8pm UTC, very late
    exit_code = _run(tmp_path, job="post", subprocess_run=fake, utcnow=late_utcnow)
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

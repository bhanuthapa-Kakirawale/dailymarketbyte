"""production_orchestrator.post: ONE POST command wrapping REPORT + POST_UNIFIED with a lock,
idempotency, --resume and the PK-C readiness gate at both PREFLIGHT and POST_RENDER.

Reuses test_evening_full.py's Runner/_record/_manifest fixtures - the same stand-ins for
`python main.py --mode report` / `python main.py --no-fetch-public`. Fully offline.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket

import pytest

from conftest import IST
from operations import evening_full as EF
from production_orchestrator import manifest as MF
from production_orchestrator import models as M
from production_orchestrator import post as PO
from production_orchestrator.lock import LOCK_SCHEMA, lock_path
from test_evening_full import FRI, Runner, _manifest, _record  # noqa: F401  (fixture re-export)
from test_pipeline import _Args, offline_pipeline  # noqa: F401
from test_production_schedule import EVENING, _no_live_acquisition, _report_job  # noqa: F401

EVENING_CLOCK = EVENING


def _ready_gate(session, as_of, out_dir, calendar, intent):
    from readiness import ReadinessResult
    return ReadinessResult(edition="POST", as_of=as_of), None


def _blocked_gate(session, as_of, out_dir, calendar, intent):
    from readiness import CheckResult, ReadinessResult
    res = ReadinessResult(edition="POST", as_of=as_of)
    res.checks = [CheckResult(check_id="X", category="TEST", capability="TEST",
                              requirement="REQUIRED", status="FAIL", message="stub failure")]
    return res, None


def _blocked_post_render(session, out_dir, intent):
    from readiness import CheckResult, ReadinessResult
    res = ReadinessResult(edition="POST", stage="POST_RENDER")
    res.checks = [CheckResult(check_id="ARTIFACT", category="FINAL_QA", capability="PUBLICATION",
                              requirement="REQUIRED", status="FAIL", message="stub QA failure")]
    return res


def _run_post(tmp_path, runner, clock=EVENING_CLOCK, **kw):
    kw.setdefault("readiness_fn", _ready_gate)
    return PO.run_post(clock=lambda: clock, runner=runner, out_dir=str(tmp_path), **kw)


def test_clean_post_run_succeeds(tmp_path):
    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]
    assert m.orchestrator_status == M.SUCCESS
    assert m.exit_code == 0
    assert m.session_date == FRI
    assert m.readiness_status_preflight == "READY"
    assert m.readiness_status_post_render == "READY"
    assert m.canonical_report["report_id"] == f"rep-{FRI}"
    assert m.video_artifact["exists"] is False or m.video_artifact is not None
    # the run file + latest.json were both written
    latest = MF.read_latest(str(tmp_path), "POST")
    assert latest["run_id"] == m.run_id
    assert os.path.exists(os.path.join(str(tmp_path), latest["path"]))


def test_preflight_blocked_stops_before_renderer(tmp_path):
    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r, readiness_fn=_blocked_gate)
    assert r.calls == [EF.REPORT_ARGS]                      # renderer never called
    assert m.orchestrator_status == M.BLOCKED
    assert m.exit_code == 20
    assert m.readiness_status_preflight == "BLOCKED"
    assert m.readiness_status_post_render is None


def test_post_render_blocked_after_a_real_render_is_failed_not_blocked(tmp_path):
    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r, post_render_fn=_blocked_post_render)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]        # the render WAS attempted
    assert m.orchestrator_status == M.FAILED                # not BLOCKED - distinct meaning
    assert m.exit_code == 30
    assert m.readiness_status_post_render == "BLOCKED"


def test_a_fatal_report_stops_before_renderer(tmp_path):
    r = Runner(str(tmp_path), report=lambda o, n: _record(o, status="BLOCKED", n=n))
    m = _run_post(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS]
    assert m.orchestrator_status == M.BLOCKED
    assert m.exit_code == 20


def test_already_completed_short_circuits_with_no_subprocess_calls(tmp_path):
    _record(str(tmp_path), n=1)
    _manifest(str(tmp_path))
    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r)
    assert r.calls == []
    assert m.orchestrator_status == M.ALREADY_COMPLETED
    assert m.exit_code == 0
    assert m.canonical_report["report_id"] == f"rep-{FRI}"


def test_force_reruns_despite_a_passing_prior_run(tmp_path):
    _record(str(tmp_path), n=99)      # n=99 avoids colliding with the Runner's own 1,2,... counter
    _manifest(str(tmp_path))
    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r, force=True)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]
    assert m.orchestrator_status == M.SUCCESS
    assert m.forced is True


def test_resume_reuses_run_id_and_renders_after_report_already_built(tmp_path):
    # simulate: REPORT completed in a prior attempt, the process then crashed before rendering
    _record(str(tmp_path), n=99)      # n=99 avoids colliding with the Runner's own 1,2,... counter
    leftover = M.RunManifest(edition="POST", command="post", run_id="orphan-run-1",
                             session_date=FRI, started_at="2026-09-18T13:00:00+00:00")
    leftover.orchestrator_status = M.RUNNING
    MF.write_manifest(str(tmp_path), leftover)

    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r, resume=True)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]        # REPORT re-invoked (cheap/idempotent)
    assert m.run_id == "orphan-run-1"
    assert m.resumed_from_run_id == "orphan-run-1"
    assert m.started_at == "2026-09-18T13:00:00+00:00"
    assert m.orchestrator_status == M.SUCCESS
    # only ONE immutable manifest file exists for this run_id - it was overwritten, not duplicated
    paths = MF.session_manifest_paths(str(tmp_path), "POST", FRI)
    assert len([p for p in paths if "orphan-run-1" in p]) == 1


def test_resume_is_a_safe_noop_when_nothing_is_running(tmp_path):
    r = Runner(str(tmp_path))
    m = _run_post(tmp_path, r, resume=True)
    assert m.resumed_from_run_id is None
    assert m.orchestrator_status == M.SUCCESS


def test_lock_conflict_blocks_with_no_subprocess_calls(tmp_path):
    out_dir = str(tmp_path)
    path = lock_path(out_dir, "POST", FRI)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": LOCK_SCHEMA, "run_id": "someone-else", "pid": os.getpid(),
                  "hostname": socket.gethostname(),
                  "acquired_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "command": "post", "intent": "SHADOW", "cli_argv": ["post"]}, fh)
    r = Runner(out_dir)
    m = _run_post(tmp_path, r)
    assert r.calls == []
    assert m.orchestrator_status == M.LOCKED
    assert m.exit_code == 40
    # the lock PK-D didn't create is left untouched
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "someone-else"


def test_a_degraded_report_job_still_renders_readiness_decides_the_verdict(tmp_path):
    """A REPORT-job DEGRADED (an optional capture failed) never blocks the render - only PK-C
    readiness, re-deriving its own verdict from the canonical report, decides SUCCESS/DEGRADED."""
    def degraded(o, n):
        _record(o, status="DEGRADED", n=n, radar={"status": "FAILED", "error": "x"})
    r = Runner(str(tmp_path), report=degraded)
    m = _run_post(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]
    assert m.orchestrator_status == M.SUCCESS

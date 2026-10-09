"""production_orchestrator.pre: ONE PRE command wrapping `main.py --mode premarket --shadow`
with a run lock, idempotency (new - run_morning_pre.bat always re-renders today), --resume and
the PK-C readiness gate at both PREFLIGHT and POST_RENDER. Fully offline."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from production_orchestrator import manifest as MF
from production_orchestrator import models as M
from production_orchestrator import pre as PR

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PRE_DATE = dt.date(2026, 9, 18)          # a real NSE Friday session
NON_SESSION_DATE = dt.date(2026, 9, 20)  # a Sunday


def _write_shadow(out_dir, pre_date, status="SUCCESS", content_ok=True, run_id=1):
    run_dir = os.path.join(out_dir, "pre_shadow", pre_date.isoformat())
    os.makedirs(run_dir, exist_ok=True)
    mp4_path = os.path.join(run_dir, f"full_pre_{pre_date}.mp4")
    with open(mp4_path, "w", encoding="utf-8") as fh:
        fh.write("fake-mp4")
    manifest = {"shadow": True, "pre_date": pre_date.isoformat(), "run_id": run_id,
               "run_status": status, "stage": "DONE",
               "artifacts": {"mp4": mp4_path},
               "qa": {"ok": True, "blocked": None,
                      "content_safety": "SAFE" if content_ok else "BLOCKED",
                      "language_issues": [], "freeze_frame_qa": True,
                      "probed_duration": 50.0, "total_duration": 50.0}}
    with open(os.path.join(run_dir, "shadow_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    result = {"publication_audit": {"final": "PASS" if content_ok else "BLOCK",
                                    "failed_checks": [] if content_ok else ["x"]}}
    with open(os.path.join(run_dir, f"pre_result_{pre_date}.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh)


def _pre_date_from_args(args):
    i = args.index("--session-date")
    return dt.date.fromisoformat(args[i + 1])


class PreRunner:
    """Stands in for `python main.py --mode premarket --shadow ...`."""

    def __init__(self, out_dir, status="SUCCESS", rc=0, content_ok=True, write=True):
        self.out_dir, self.calls = out_dir, []
        self.status, self.rc, self.content_ok, self.write = status, rc, content_ok, write

    def __call__(self, args):
        self.calls.append(list(args))
        if self.write:
            _write_shadow(self.out_dir, _pre_date_from_args(args), status=self.status,
                         content_ok=self.content_ok, run_id=len(self.calls))
        return self.rc


def _ready_gate(pre_date, as_of, out_dir, calendar, intent, mode="LIVE"):
    from readiness import ReadinessResult
    return ReadinessResult(edition="PRE", as_of=as_of, mode=mode), None


def _blocked_gate(pre_date, as_of, out_dir, calendar, intent, mode="LIVE"):
    from readiness import CheckResult, ReadinessResult
    res = ReadinessResult(edition="PRE", as_of=as_of, mode=mode)
    res.checks = [CheckResult(check_id="X", category="TEST", capability="TEST",
                              requirement="REQUIRED", status="FAIL", message="stub failure")]
    return res, None


def _run_pre(tmp_path, runner, as_of=None, **kw):
    kw.setdefault("readiness_fn", _ready_gate)
    as_of = as_of or dt.datetime(2026, 9, 18, 7, 45, tzinfo=IST)
    return PR.run_pre(session_date=PRE_DATE, as_of=as_of, runner=runner, out_dir=str(tmp_path),
                      clock=lambda: as_of, **kw)


def test_clean_pre_run_succeeds(tmp_path):
    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r)
    assert len(r.calls) == 1
    assert "--session-date" in r.calls[0] and "--shadow" in r.calls[0]
    assert m.orchestrator_status == M.SUCCESS
    assert m.exit_code == 0
    assert m.readiness_status_preflight == "READY"
    assert m.readiness_status_post_render == "READY"
    assert m.video_artifact["exists"] is True


def test_non_session_date_is_skipped_with_no_subprocess_calls(tmp_path):
    r = PreRunner(str(tmp_path))
    as_of = dt.datetime(2026, 9, 20, 7, 45, tzinfo=IST)
    m = PR.run_pre(session_date=NON_SESSION_DATE, as_of=as_of, runner=r, out_dir=str(tmp_path),
                  clock=lambda: as_of, readiness_fn=_ready_gate)
    assert r.calls == []
    assert m.orchestrator_status == M.SKIPPED
    assert m.exit_code == 0


def test_preflight_blocked_stops_before_renderer(tmp_path):
    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r, readiness_fn=_blocked_gate)
    assert r.calls == []
    assert m.orchestrator_status == M.BLOCKED
    assert m.exit_code == 20


def test_post_render_blocked_when_render_fails(tmp_path):
    r = PreRunner(str(tmp_path), rc=1, write=False)
    m = _run_pre(tmp_path, r)
    assert len(r.calls) == 1
    assert m.orchestrator_status == M.FAILED
    assert m.exit_code == 30
    assert m.readiness_status_post_render == "BLOCKED"


def test_already_completed_short_circuits_with_no_subprocess_calls(tmp_path):
    _write_shadow(str(tmp_path), PRE_DATE)
    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r)
    assert r.calls == []
    assert m.orchestrator_status == M.ALREADY_COMPLETED
    assert m.exit_code == 0


def test_force_reruns_despite_a_passing_prior_run(tmp_path):
    _write_shadow(str(tmp_path), PRE_DATE)
    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r, force=True)
    assert len(r.calls) == 1
    assert m.orchestrator_status == M.SUCCESS
    assert m.forced is True


def test_resume_reuses_run_id_and_skips_redundant_readiness_network_call(tmp_path):
    as_of = dt.datetime(2026, 9, 18, 7, 45, tzinfo=IST)
    # simulate: PREFLIGHT_READINESS already passed in a prior attempt, the process then crashed
    leftover = M.RunManifest(edition="PRE", command="pre", run_id="orphan-pre-1",
                             session_date=PRE_DATE.isoformat(),
                             started_at="2026-09-18T02:10:00+00:00",
                             cli={"as_of": as_of.isoformat()})
    leftover.readiness_status_preflight = "READY"
    leftover.orchestrator_status = M.RUNNING
    MF.write_manifest(str(tmp_path), leftover)

    calls = {"n": 0}

    def counting_readiness_fn(pre_date, a, out_dir, calendar, intent, mode="LIVE"):
        calls["n"] += 1
        from readiness import ReadinessResult
        return ReadinessResult(edition="PRE", as_of=a), None

    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r, as_of=as_of, resume=True, readiness_fn=counting_readiness_fn)
    assert calls["n"] == 0                                  # readiness NOT re-touched
    assert m.run_id == "orphan-pre-1"
    assert m.resumed_from_run_id == "orphan-pre-1"
    assert m.readiness_status_preflight == "READY"
    assert len(r.calls) == 1                                # renderer still ran
    assert m.orchestrator_status == M.SUCCESS


def test_resume_with_different_as_of_does_not_reuse_readiness(tmp_path):
    as_of_old = dt.datetime(2026, 9, 18, 7, 0, tzinfo=IST)
    as_of_new = dt.datetime(2026, 9, 18, 7, 45, tzinfo=IST)
    leftover = M.RunManifest(edition="PRE", command="pre", run_id="orphan-pre-2",
                             session_date=PRE_DATE.isoformat(),
                             cli={"as_of": as_of_old.isoformat()})
    leftover.readiness_status_preflight = "READY"
    leftover.orchestrator_status = M.RUNNING
    MF.write_manifest(str(tmp_path), leftover)

    calls = {"n": 0}

    def counting_readiness_fn(pre_date, a, out_dir, calendar, intent, mode="LIVE"):
        calls["n"] += 1
        from readiness import ReadinessResult
        return ReadinessResult(edition="PRE", as_of=a), None

    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r, as_of=as_of_new, resume=True, readiness_fn=counting_readiness_fn)
    assert calls["n"] == 1                                  # different as_of - re-evaluated


def test_explicit_session_date_or_as_of_evaluates_readiness_in_replay_mode(tmp_path):
    """A regression guard: PRE's LIVE readiness mode is the only readiness call in the whole
    gate that touches the network (the overnight-cue probe). Giving PK-D an explicit
    --session-date/--as-of must map to REPLAY, never LIVE, or a REPLAY run would silently start
    making real Yahoo calls."""
    seen_modes = []

    def mode_capturing_gate(pre_date, as_of, out_dir, calendar, intent, mode="LIVE"):
        seen_modes.append(mode)
        from readiness import ReadinessResult
        return ReadinessResult(edition="PRE", as_of=as_of, mode=mode), None

    r = PreRunner(str(tmp_path))
    _run_pre(tmp_path, r, readiness_fn=mode_capturing_gate)
    assert seen_modes == ["REPLAY"]      # session_date + as_of were both given explicitly


def test_live_run_with_no_explicit_session_or_as_of_uses_live_mode(tmp_path):
    seen_modes = []

    def mode_capturing_gate(pre_date, as_of, out_dir, calendar, intent, mode="LIVE"):
        seen_modes.append(mode)
        from readiness import ReadinessResult
        return ReadinessResult(edition="PRE", as_of=as_of, mode=mode), None

    now = dt.datetime(2026, 9, 18, 7, 45, tzinfo=IST)
    r = PreRunner(str(tmp_path))
    PR.run_pre(runner=r, out_dir=str(tmp_path), clock=lambda: now,
              readiness_fn=mode_capturing_gate)
    assert seen_modes == ["LIVE"]        # neither session_date nor as_of given - the real daily case


def test_degraded_readiness_completes_as_degraded(tmp_path):
    def degraded_gate(pre_date, as_of, out_dir, calendar, intent, mode="LIVE"):
        from readiness import CheckResult, ReadinessResult
        res = ReadinessResult(edition="PRE", as_of=as_of)
        res.checks = [CheckResult(check_id="Y", category="TEST", capability="TEST",
                                  requirement="OPTIONAL", status="WARN", message="stub warn")]
        return res, None
    r = PreRunner(str(tmp_path))
    m = _run_pre(tmp_path, r, readiness_fn=degraded_gate)
    assert m.orchestrator_status == M.DEGRADED
    assert m.exit_code == 0

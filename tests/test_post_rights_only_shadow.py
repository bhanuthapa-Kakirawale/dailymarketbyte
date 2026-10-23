"""2026-10-22 POST shadow incident: the run FAILED with "POST QA / content failure (audit
RIGHTS_BLOCK_ONLY)". The rights-only block was NOT the cause - freeze-frame QA had failed
(01_pulse: overlapping labels 'OPEN' / 'CLOSE', the open and close only a few px apart on the
day-range strip) and the message named only the audit verdict.

Pins: a rights-only block never fails a SHADOW run on its own (PRE and POST alike); it never hides
a genuine content / video / freeze-frame failure, and the failure message names the gate that
actually failed; PUBLISH intent still blocks on rights; nothing uploads; the pulse strip's two
labels never overlap however close the open and close are.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os

import pytest

from daily_video import Composer
from production_orchestrator import models as M
from production_orchestrator import post as PO
from readiness.post_render import evaluate_post_render
from test_evening_full import FRI, Runner, _manifest  # noqa: F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CLOCK = lambda: dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST)          # noqa: E731


def _ready_gate(session, as_of, out_dir, calendar, intent):
    from readiness import ReadinessResult
    return ReadinessResult(edition="POST", as_of=as_of), None


def _post_manifest(qa=None, failed=("publication_rights",), content="SAFE"):
    """A POST manifest writer for Runner(post=...) with the given QA block overridden."""
    def write(out):
        _manifest(out, failed=failed, content=content)
        if qa is None:
            return
        p = os.path.join(out, "post", FRI, "production_manifest.json")
        with open(p, encoding="utf-8") as fh:
            m = json.load(fh)
        m["qa"].update(qa)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
    return write


def _run(tmp_path, post, intent="shadow"):
    r = Runner(str(tmp_path), post=post)
    m = PO.run_post(clock=CLOCK, runner=r, out_dir=str(tmp_path), readiness_fn=_ready_gate,
                    intent=intent)
    return m, r


# --------------------------------------------------------------------------- POST orchestrator
def test_B_post_rights_only_shadow_succeeds_and_keeps_the_rights_block(tmp_path):
    m, r = _run(tmp_path, _post_manifest())
    assert m.orchestrator_status == M.SUCCESS
    assert m.readiness_status_post_render in ("READY", "DEGRADED")
    # G: the rights block stays on record - never rewritten to PASS
    assert m.audit_artifact["final"] == "BLOCK"
    assert m.audit_artifact["failed_checks"] == ["publication_rights"]
    assert not m.blocking_reasons
    # H: no upload anywhere in what was run
    assert all("--upload" not in a for call in r.calls for a in call)


def test_C_rights_plus_real_content_failure_fails_and_names_the_content(tmp_path):
    m, _ = _run(tmp_path, _post_manifest(failed=("publication_rights", "recommendation_language")))
    assert m.orchestrator_status == M.FAILED
    reason = m.failure["reason"]
    assert "recommendation_language" in reason and "CONTENT_FAILURE" in reason


def test_C2_rights_plus_unsafe_final_content_scan_fails(tmp_path):
    m, _ = _run(tmp_path, _post_manifest(content="BLOCKED"))
    assert m.orchestrator_status == M.FAILED
    assert "final content scan BLOCKED" in m.failure["reason"]


def test_D_rights_plus_freeze_frame_failure_fails_and_names_the_frame(tmp_path):
    """The exact 2026-10-22 shape: rights-only audit, every scan PASS, one frame QA issue."""
    issue = {"01_pulse": ["overlapping labels: 'OPEN' / 'CLOSE'"]}
    m, _ = _run(tmp_path, _post_manifest(qa={"frames_qa": {"passed": False, "issues": issue}}))
    assert m.orchestrator_status == M.FAILED
    reason = m.failure["reason"]
    assert "freeze-frame QA failed" in reason and "01_pulse" in reason and "OPEN" in reason
    assert "(audit RIGHTS_BLOCK_ONLY)" in reason        # the rights state is still reported
    assert m.audit_artifact["final"] == "BLOCK"


def test_D2_rights_plus_video_qa_failure_fails_and_names_video_qa(tmp_path):
    m, _ = _run(tmp_path, _post_manifest(qa={"video_qa": "FAIL",
                                             "video_qa_blocking": ["duration 12.0s"]}))
    assert m.orchestrator_status == M.FAILED
    assert "video QA FAIL" in m.failure["reason"] and "duration" in m.failure["reason"]


def test_E_publish_intent_with_rights_pending_is_never_success(tmp_path):
    m, r = _run(tmp_path, _post_manifest(), intent="publish")
    assert m.orchestrator_status in (M.FAILED, M.BLOCKED)
    assert m.readiness_status_post_render == "BLOCKED"
    assert m.audit_artifact["final"] == "BLOCK"
    assert all("--upload" not in a for call in r.calls for a in call)


# --------------------------------------------------------------------------- PRE (same rule)
def _pre_artifacts(out, session, failed=("publication_rights",), freeze=True):
    d = os.path.join(out, "pre_shadow", session)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "shadow_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_status": "DEGRADED", "stage": "COMPLETE",
                   "qa": {"ok": True, "freeze_frame_qa": freeze, "content_safety": "SAFE",
                          "language_issues": [], "probed_duration": 48.0}}, fh)
    with open(os.path.join(d, f"pre_result_{session}.json"), "w", encoding="utf-8") as fh:
        json.dump({"publication_audit": {"final": "BLOCK", "failed_checks": list(failed)}}, fh)


def test_A_pre_rights_only_shadow_is_not_blocked(tmp_path):
    _pre_artifacts(str(tmp_path), "2026-10-22")
    res = evaluate_post_render("PRE", dt.date(2026, 10, 22), out_dir=str(tmp_path), intent="SHADOW")
    assert res.overall_status == "DEGRADED"                       # the run's own degradations
    assert res.check("PUBLICATION_AUDIT").status == "PASS"
    assert res.check("PUBLICATION_AUDIT").observed["verdict"] == "RIGHTS_BLOCK_ONLY"


def test_A2_pre_and_post_treat_a_freeze_frame_failure_the_same(tmp_path):
    _pre_artifacts(str(tmp_path), "2026-10-22", freeze=False)
    res = evaluate_post_render("PRE", dt.date(2026, 10, 22), out_dir=str(tmp_path), intent="SHADOW")
    assert res.overall_status == "BLOCKED" and res.check("FRAMES_QA").status == "FAIL"


# --------------------------------------------------------------------------- the pulse strip
def _pulse_frame(open_delta, high_pad=20.0):
    import daily_video.storyboard as sbm
    from test_post_phase3 import _plan, _pres
    pres = _pres(pct=-0.5)
    c = pres.m["close"]
    pres.m["open"] = c + open_delta
    pres.m["high"] = max(c, c + open_delta) + high_pad
    pres.m["low"] = min(c, c + open_delta) - 20.0
    sb = sbm.build_storyboard(_plan(), pres, dynamic_hook=False, profile="PRIVATE_ANALYTICS")
    i = next(k for k, s in enumerate(sb.scenes) if s.kind == "PULSE")
    assert (sb.scenes[i].data.get("support") or {}).get("kind") == "SESSION_RANGE"
    _, rec, qa = Composer(sb).freeze(i)
    return rec, qa


@pytest.mark.parametrize("open_delta,high_pad", [
    (0.4, 20.0),        # close a hair below the open (the 2026-10-22 shape)
    (-0.4, 20.0),       # close a hair above the open
    (0.0, 20.0),        # closed exactly where it opened
    (0.2, 0.3),         # both pinned against the DAY HIGH end of the strip
    (12.0, 20.0),       # well apart - the ordinary layout
])
def test_pulse_open_and_close_labels_never_overlap(open_delta, high_pad):
    rec, qa = _pulse_frame(open_delta, high_pad)
    assert qa["passed"], qa["issues"]
    labels = [tb for tb in rec.texts if tb.text in ("OPEN", "CLOSE")]
    assert sorted(tb.text for tb in labels) == ["CLOSE", "OPEN"]
    for a, b in itertools.combinations(labels, 2):
        assert a.box[2] <= b.box[0] or b.box[2] <= a.box[0], (a.box, b.box)

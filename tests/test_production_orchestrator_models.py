"""production_orchestrator.models: the run manifest shape, exit-code mapping, stage order."""
from __future__ import annotations

from production_orchestrator import models as M


def test_exit_codes_cover_every_terminal_status():
    for status in M.TERMINAL_STATUSES:
        assert status in M.EXIT_CODES, f"{status} has no exit code"
    assert M.EXIT_CODES[M.SUCCESS] == 0
    assert M.EXIT_CODES[M.DEGRADED] == 0
    assert M.EXIT_CODES[M.ALREADY_COMPLETED] == 0
    assert M.EXIT_CODES[M.SKIPPED] == 0
    assert M.EXIT_CODES[M.BLOCKED] == 20
    assert M.EXIT_CODES[M.FAILED] == 30
    assert M.EXIT_CODES[M.LOCKED] == 40


def test_ok_statuses_are_exactly_the_zero_exit_ones():
    zero_exit = {s for s, c in M.EXIT_CODES.items() if c == 0}
    assert zero_exit == M.OK_STATUSES


def test_stage_order_has_renderer_and_post_render_check_for_both_editions():
    for edition in ("PRE", "POST"):
        order = M.STAGE_ORDER[edition]
        assert "RUN_LOCK" in order
        assert "RENDERER" in order
        assert order.index("RENDERER") < order.index("POST_RENDER_CHECK")
        assert order[-1] == "MANIFEST"
    # POST has an extra ACQUISITION_OR_REPORT stage PRE does not
    assert "ACQUISITION_OR_REPORT" in M.STAGE_ORDER["POST"]
    assert "ACQUISITION_OR_REPORT" not in M.STAGE_ORDER["PRE"]


def test_run_manifest_begin_and_finish_stage_round_trip():
    m = M.RunManifest(edition="POST", command="post", run_id="r1")
    m.begin_stage("RUN_LOCK")
    rec = m.finish_stage("RUN_LOCK", M.STAGE_OK, "acquired")
    assert rec.status == M.STAGE_OK
    assert rec.detail == "acquired"
    assert rec.started_at is not None
    assert rec.completed_at is not None
    assert len(m.stages) == 1
    # finishing again (same stage name) updates in place, never duplicates
    m.finish_stage("RUN_LOCK", M.STAGE_FAILED, "lost it")
    assert len(m.stages) == 1
    assert m.stages[0].status == M.STAGE_FAILED


def test_finalize_sets_status_and_completed_at():
    m = M.RunManifest(edition="PRE", command="pre", run_id="r2")
    assert m.completed_at is None
    m.finalize(M.SUCCESS)
    assert m.orchestrator_status == M.SUCCESS
    assert m.completed_at is not None
    assert m.exit_code == 0


def test_to_dict_includes_exit_code_and_serializes_stages():
    m = M.RunManifest(edition="POST", command="post", run_id="r3")
    m.begin_stage("RUN_LOCK")
    m.finish_stage("RUN_LOCK", M.STAGE_OK)
    m.finalize(M.BLOCKED)
    d = m.to_dict()
    assert d["schema"] == M.SCHEMA
    assert d["exit_code"] == 20
    assert isinstance(d["stages"], list)
    assert d["stages"][0]["stage"] == "RUN_LOCK"
    assert d["stages"][0]["status"] == M.STAGE_OK


def test_to_latest_dict_carries_both_readiness_fields_separately():
    m = M.RunManifest(edition="POST", command="post", run_id="r4", session_date="2026-10-08")
    m.readiness_status_preflight = M.DEGRADED
    m.readiness_status_post_render = "READY"
    m.finalize(M.DEGRADED)
    d = m.to_latest_dict("production_runs/POST/2026-10-08/r4.json")
    assert d["schema"] == M.LATEST_SCHEMA
    assert d["readiness_status_preflight"] == M.DEGRADED
    assert d["readiness_status_post_render"] == "READY"
    assert d["orchestrator_status"] == M.DEGRADED
    assert d["path"] == "production_runs/POST/2026-10-08/r4.json"

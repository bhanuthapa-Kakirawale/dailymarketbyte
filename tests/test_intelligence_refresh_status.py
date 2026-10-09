"""intelligence_refresh.status.run_status: pure read of latest.json, never calls a repair/fetch
function, never acquires the lock."""
from __future__ import annotations

from intelligence_refresh import manifest, models
from intelligence_refresh.status import run_status


def test_no_run_yet(tmp_path):
    exit_code, text = run_status(out_dir=str(tmp_path))
    assert exit_code == 30
    assert "No intelligence_refresh run recorded yet" in text


def test_reports_last_successful_run(tmp_path):
    man = models.RunManifest(run_id="r1", orchestrator_status=models.SUCCESS,
                             summary={"sessions_audited": 5})
    manifest.write_manifest(str(tmp_path), man)
    exit_code, text = run_status(out_dir=str(tmp_path))
    assert exit_code == 0
    assert "VERDICT: OK" in text


def test_reports_blocked_with_exit_20(tmp_path):
    man = models.RunManifest(run_id="r1", orchestrator_status=models.BLOCKED)
    manifest.write_manifest(str(tmp_path), man)
    exit_code, _ = run_status(out_dir=str(tmp_path))
    assert exit_code == 20


def test_json_output_returns_the_raw_latest_dict(tmp_path):
    man = models.RunManifest(run_id="r1", orchestrator_status=models.SUCCESS)
    manifest.write_manifest(str(tmp_path), man)
    exit_code, payload = run_status(out_dir=str(tmp_path), json_output=True)
    assert exit_code == 0
    assert payload["run_id"] == "r1"


def test_status_never_calls_any_repair_or_fetch_function(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("status must never call a repair/fetch function")
    monkeypatch.setattr("intelligence_refresh.price_chain_backfill.fetch_benchmark_context", boom)
    monkeypatch.setattr("intelligence_refresh.price_chain_backfill.backfill_market_structure", boom)
    monkeypatch.setattr("intelligence_refresh.latest_session.ensure_latest", boom)
    run_status(out_dir=str(tmp_path))


def test_status_never_acquires_the_lock(tmp_path, monkeypatch):
    import os
    run_status(out_dir=str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), "intelligence_refresh", "run.lock"))

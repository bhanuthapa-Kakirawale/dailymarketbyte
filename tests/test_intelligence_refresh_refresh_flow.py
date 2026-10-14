"""intelligence_refresh.refresh.run_refresh: the orchestration flow. Every engine call is
monkeypatched at the module boundary (coverage.scan_session, price_chain_backfill.*,
scan.sessions_to_audit, refresh.ensure_latest) so these tests prove ORCHESTRATION behaviour -
healthy-system zero-backfill, 3-missed-days chronological backfill, mixed recoverability,
idempotency, live-artifact preservation, resume - without depending on real market data or
network.
"""
from __future__ import annotations

import datetime as dt

import pytest

from intelligence_refresh import coverage, manifest, models, price_chain_backfill, refresh, registry, scan

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 10, 9, 19, 30, tzinfo=IST)
S1, S2, S3, S4 = (dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3),
                 dt.date(2026, 9, 4))


def _patch_common(monkeypatch, sessions, *, latest=None):
    monkeypatch.setattr(scan, "sessions_to_audit",
                        lambda *a, **k: (sessions, latest))
    monkeypatch.setattr(refresh, "ensure_latest",
                        lambda *a, **k: {"action": "SKIPPED", "reason": "ALREADY_RUN",
                                        "session": str(latest),
                                        "canonical_report": {"status": "COMPLETE",
                                                             "expected_session": str(latest)}})


def _all_present(out_dir, session, **kw):
    return {"ohlcv_benchmark": registry.PRESENT, "market_structure": registry.PRESENT,
            "radar_candidate_history": registry.PRESENT,
            **{k: registry.COMPONENTS[k].backfill_mode for k in registry.LATEST_ONLY_KEYS
               + registry.STATIC_KEYS}}


def test_healthy_system_zero_backfill(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [S1, S2, S3])
    monkeypatch.setattr(coverage, "scan_session", _all_present)

    def boom(*a, **k):
        raise AssertionError("no repair function should be called when everything is present")
    monkeypatch.setattr(price_chain_backfill, "fetch_benchmark_context", boom)
    monkeypatch.setattr(price_chain_backfill, "backfill_market_structure", boom)
    monkeypatch.setattr(price_chain_backfill, "backfill_radar_candidates", boom)

    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.SUCCESS
    assert man.summary["sessions_fully_recoverable"] == 3
    assert man.summary["component_counts"].get(registry.BACKFILLED, 0) == 0


def _missing_all(out_dir, session, **kw):
    return {"ohlcv_benchmark": registry.MISSING, "market_structure": registry.MISSING,
            "radar_candidate_history": registry.MISSING,
            **{k: registry.COMPONENTS[k].backfill_mode for k in registry.LATEST_ONLY_KEYS
               + registry.STATIC_KEYS}}


def test_3_missed_days_backfilled_chronologically(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [S1, S2, S3])
    monkeypatch.setattr(coverage, "scan_session", _missing_all)
    # the real engines write benchmark OHLCV through as a side effect of actually running;
    # simulate that here so the post-backfill re-check sees it present.
    monkeypatch.setattr(coverage, "_ohlcv_benchmark_present", lambda out_dir, session: True)
    monkeypatch.setattr(price_chain_backfill, "fetch_benchmark_context",
                        lambda out_dir, universe_name: {"status": "OK", "bench": ["b"],
                                                        "spine": [S1, S2, S3], "universe": {}})

    structure_calls = []
    monkeypatch.setattr(price_chain_backfill, "backfill_market_structure",
                        lambda session, universe_name, out_dir: (
                            structure_calls.append(session) or
                            {"status": registry.BACKFILLED, "artifact": "x.json"}))

    candidate_call_sessions = []

    def fake_candidates(sessions, *, spine, universe, benchmark_series, out_dir, store=None):
        candidate_call_sessions.append(list(sessions))
        class _R:
            processed_sessions = list(sessions)
            already_complete_sessions = []
            failed_sessions = []
            warnings = []
        return _R()
    monkeypatch.setattr(price_chain_backfill, "backfill_radar_candidates", fake_candidates)

    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.SUCCESS
    assert structure_calls == [S1, S2, S3]              # oldest-first
    assert len(candidate_call_sessions) == 1             # ONE batched call, never per-session
    assert candidate_call_sessions[0] == [S1, S2, S3]
    assert man.summary["sessions_fully_recoverable"] == 3
    # 3 structure + 3 candidates + 3 ohlcv_benchmark (re-checked present after the backfill)
    assert man.summary["component_counts"][registry.BACKFILLED] == 9


def test_mixed_recoverability_one_failure_is_partial_not_fatal(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [S1])
    monkeypatch.setattr(coverage, "scan_session", _missing_all)
    monkeypatch.setattr(price_chain_backfill, "fetch_benchmark_context",
                        lambda out_dir, universe_name: {"status": "OK", "bench": ["b"],
                                                        "spine": [S1], "universe": {}})
    monkeypatch.setattr(price_chain_backfill, "backfill_market_structure",
                        lambda session, universe_name, out_dir: {"status": registry.FAILED,
                                                                 "detail": "boom"})

    def fake_candidates(sessions, *, spine, universe, benchmark_series, out_dir, store=None):
        class _R:
            processed_sessions = list(sessions)
            already_complete_sessions = []
            failed_sessions = []
            warnings = []
        return _R()
    monkeypatch.setattr(price_chain_backfill, "backfill_radar_candidates", fake_candidates)

    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.DEGRADED     # never BLOCKED/FAILED for an
                                                            # optional historical gap
    assert man.session_results[0]["session_status"] == "PARTIAL"
    assert man.session_results[0]["components"]["market_structure"] == registry.FAILED


def test_latest_session_blocked_propagates_to_run_status(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [])
    monkeypatch.setattr(refresh, "ensure_latest",
                        lambda *a, **k: {"action": "RAN", "session": "2026-10-09",
                                        "report_job_run_status": "BLOCKED"})
    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.BLOCKED


def test_report_job_run_status_degraded_maps_to_orchestrator_degraded(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [])
    monkeypatch.setattr(refresh, "ensure_latest",
                        lambda *a, **k: {"action": "RAN", "session": "2026-10-09",
                                        "report_job_run_status": "DEGRADED",
                                        "canonical_report": {"status": "COMPLETE"}})
    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.DEGRADED


def test_report_job_run_status_failed_maps_to_orchestrator_degraded(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [])
    monkeypatch.setattr(refresh, "ensure_latest",
                        lambda *a, **k: {"action": "RAN", "session": "2026-10-09",
                                        "report_job_run_status": "FAILED",
                                        "exception": "RuntimeError: boom",
                                        "canonical_report": {"status": "MISSING"}})
    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.DEGRADED


def test_canonical_report_non_complete_forces_degraded_even_with_odd_report_job_run_status(
        tmp_path, monkeypatch):
    _patch_common(monkeypatch, [])
    monkeypatch.setattr(refresh, "ensure_latest",
                        lambda *a, **k: {"action": "RAN", "session": "2026-10-09",
                                        "report_job_run_status": "WEIRD_UNRECOGNIZED",
                                        "canonical_report": {"status": "ARTIFACT_MISSING"}})
    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man.orchestrator_status == models.DEGRADED


def test_run_refresh_recovers_from_report_job_exception_runs_price_chain_anyway(
        tmp_path, monkeypatch):
    # The real ensure_latest (not stubbed) with a report_job_fn that raises - closes Bug A: one
    # transient data failure must not crash the whole refresh or skip the unrelated 30-day
    # reconciliation.
    monkeypatch.setattr(scan, "sessions_to_audit", lambda *a, **k: ([S1], dt.date(2026, 10, 9)))
    monkeypatch.setattr(coverage, "scan_session", _all_present)

    def boom(*, now, calendar):
        raise RuntimeError("Only 0 stocks had clean data")

    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW, report_job_fn=boom)
    assert man.orchestrator_status == models.DEGRADED
    assert man.session_results, "the independent 30-day reconciliation must still run"
    assert man.latest_session_result["report_job_run_status"] == "FAILED"
    assert "RuntimeError" in man.latest_session_result["exception"]


def test_30_day_reconciler_never_builds_canonical_reports_for_historical_sessions(
        tmp_path, monkeypatch):
    _patch_common(monkeypatch, [S1, S2, S3])
    monkeypatch.setattr(coverage, "scan_session", _missing_all)
    monkeypatch.setattr(price_chain_backfill, "fetch_benchmark_context",
                        lambda out_dir, universe_name: {"status": "OK", "bench": ["b"],
                                                        "spine": [S1, S2, S3], "universe": {}})
    monkeypatch.setattr(price_chain_backfill, "backfill_market_structure",
                        lambda session, universe_name, out_dir: {"status": registry.BACKFILLED,
                                                                 "artifact": "x.json"})

    def fake_candidates(sessions, *, spine, universe, benchmark_series, out_dir, store=None):
        class _R:
            processed_sessions = list(sessions)
            already_complete_sessions = []
            failed_sessions = []
            warnings = []
        return _R()
    monkeypatch.setattr(price_chain_backfill, "backfill_radar_candidates", fake_candidates)

    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    expected = registry.COMPONENTS["canonical_report"].backfill_mode
    assert expected == "LATEST_SESSION_ONLY"
    for row in man.session_results:
        # the 30-day historical reconciler only ever touches PRICE_CHAIN_KEYS - canonical_report
        # for any non-latest session stays its static classification, never an attempted build.
        assert row["components"]["canonical_report"] == expected


def test_idempotent_second_run_makes_no_new_backfill_calls(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [S1, S2])
    monkeypatch.setattr(coverage, "scan_session", _all_present)
    monkeypatch.setattr(price_chain_backfill, "fetch_benchmark_context",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))

    man1 = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    man2 = refresh.run_refresh(out_dir=str(tmp_path), now=NOW)
    assert man1.summary == man2.summary
    assert man1.orchestrator_status == man2.orchestrator_status == models.SUCCESS
    assert man1.latest_session_result["canonical_report"]["status"] == \
        man2.latest_session_result["canonical_report"]["status"] == "COMPLETE"


def test_dry_run_never_calls_any_repair_function(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [S1])
    monkeypatch.setattr(coverage, "scan_session", _missing_all)

    def boom(*a, **k):
        raise AssertionError("dry-run must never call a repair function")
    monkeypatch.setattr(price_chain_backfill, "fetch_benchmark_context", boom)
    monkeypatch.setattr(price_chain_backfill, "backfill_market_structure", boom)
    monkeypatch.setattr(price_chain_backfill, "backfill_radar_candidates", boom)

    man = refresh.run_refresh(out_dir=str(tmp_path), now=NOW, dry_run=True)
    assert man.session_results[0]["components"]["market_structure"] == registry.WOULD_BACKFILL


def test_resume_records_resumed_from_run_id(tmp_path, monkeypatch):
    _patch_common(monkeypatch, [])
    out_dir = str(tmp_path)
    leftover = models.RunManifest(run_id="leftover-1", orchestrator_status="RUNNING")
    manifest.write_manifest(out_dir, leftover)

    man = refresh.run_refresh(out_dir=out_dir, now=NOW, resume=True)
    assert man.resumed_from_run_id == "leftover-1"

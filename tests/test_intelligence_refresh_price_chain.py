"""intelligence_refresh.price_chain_backfill: thin calls to the existing, unmodified engines -
market_structure.build.build_for_session and radar.candidate_history_backfill.
backfill_candidate_history. Proves: each is a true pass-through (no reimplemented logic), a
failure in one never touches the other, and the benchmark context is fetched once and reused
across a whole batch of sessions rather than once per session."""
from __future__ import annotations

import datetime as dt

from intelligence_refresh import price_chain_backfill as pcb
from intelligence_refresh import registry

S1, S2, S3 = dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)


def test_backfill_market_structure_delegates_to_build_for_session(tmp_path, monkeypatch):
    calls = []

    def fake_build_for_session(session, universe_name, out_dir=None):
        calls.append((session, universe_name, out_dir))
        return {"status": "OK", "artifact": "path.json"}

    monkeypatch.setattr("market_structure.build.build_for_session", fake_build_for_session)
    result = pcb.backfill_market_structure(S1, "NIFTY200", str(tmp_path))
    assert result["status"] == registry.BACKFILLED
    assert calls == [(S1, "NIFTY200", str(tmp_path))]


def test_backfill_market_structure_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def boom(session, universe_name, out_dir=None):
        raise RuntimeError("network down")
    monkeypatch.setattr("market_structure.build.build_for_session", boom)
    result = pcb.backfill_market_structure(S1, "NIFTY200", str(tmp_path))
    assert result["status"] == registry.FAILED


def test_backfill_radar_candidates_delegates_with_a_single_batched_call(tmp_path, monkeypatch):
    calls = []

    def fake_backfill_candidate_history(*, spine, universe, benchmark_series, sessions,
                                        out_dir, store=None):
        calls.append(sessions)
        class _R:
            processed_sessions = list(sessions)
            already_complete_sessions = []
            failed_sessions = []
            warnings = []
        return _R()

    monkeypatch.setattr("radar.candidate_history_backfill.backfill_candidate_history",
                        fake_backfill_candidate_history)
    result = pcb.backfill_radar_candidates([S1, S2, S3], spine=[S1, S2, S3], universe={},
                                           benchmark_series=[], out_dir=str(tmp_path))
    assert len(calls) == 1          # ONE call for all three sessions, never one per session
    assert calls[0] == [S1, S2, S3]
    assert result.processed_sessions == [S1, S2, S3]


def test_fetch_benchmark_context_reports_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr("radar.relative_acquisition.build_market_benchmark_series",
                        lambda period, out_dir=None: [])
    ctx = pcb.fetch_benchmark_context(str(tmp_path))
    assert ctx["status"] == "FAILED"


def test_structure_failure_never_blocks_candidate_backfill(tmp_path, monkeypatch):
    """Independence: the two engines are mutually independent - a failure in one must not
    prevent the other from running."""
    monkeypatch.setattr("market_structure.build.build_for_session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    structure_result = pcb.backfill_market_structure(S1, "NIFTY200", str(tmp_path))
    assert structure_result["status"] == registry.FAILED

    def fake_backfill_candidate_history(*, spine, universe, benchmark_series, sessions,
                                        out_dir, store=None):
        class _R:
            processed_sessions = list(sessions)
            already_complete_sessions = []
            failed_sessions = []
            warnings = []
        return _R()
    monkeypatch.setattr("radar.candidate_history_backfill.backfill_candidate_history",
                        fake_backfill_candidate_history)
    candidate_result = pcb.backfill_radar_candidates([S1], spine=[S1], universe={},
                                                     benchmark_series=[], out_dir=str(tmp_path))
    assert candidate_result.processed_sessions == [S1]

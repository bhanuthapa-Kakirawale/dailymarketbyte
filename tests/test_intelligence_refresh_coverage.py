"""intelligence_refresh.coverage.scan_session: read-only, never fetches. A healthy session
with every price-chain artifact present reports ALREADY_PRESENT for all three; a session
missing them reports MISSING - and HISTORICAL_UNAVAILABLE/UNSUPPORTED_HISTORICALLY/
NOT_APPLICABLE components are never mistaken for MISSING (they are never even checked against
disk - they come straight from the registry)."""
from __future__ import annotations

import datetime as dt
import json
import os

from intelligence_refresh import coverage, registry

SESSION = dt.date(2026, 9, 15)


def _write_market_structure_revision(out_dir, session, retrieved_at):
    folder = os.path.join(out_dir, "market_structure")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"market_structure_{session.isoformat()}.rev1.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"snapshot": {"universe_source": {"retrieved_at": retrieved_at}}}, fh)


def _write_benchmark_bar(out_dir, session):
    from storage.ohlcv_models import OHLCVBar, QualityStatus
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    store = OHLCVStore(default_db_path(out_dir))
    try:
        store.upsert_bars([OHLCVBar(symbol="^NSEI", session_date=session, open=100, high=101,
                                   low=99, close=100.5, volume=1000, source="yahoo",
                                   retrieved_at=dt.datetime.now(dt.timezone.utc),
                                   quality_status=QualityStatus.OK)])
    finally:
        store.close()


def _mark_candidate_session_complete(out_dir, session, version):
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.candidate_history_repository import default_db_path as cdb
    store = CandidateHistoryStore(cdb(out_dir))
    try:
        store.mark_run(session, version, "COMPLETE", 0)
    finally:
        store.close()


def test_healthy_session_reports_present_for_every_price_chain_component(tmp_path):
    out_dir = str(tmp_path)
    _write_benchmark_bar(out_dir, SESSION)
    _write_market_structure_revision(out_dir, SESSION, "2026-09-15T10:00:00+00:00")
    from radar.models import CALCULATION_VERSION
    _mark_candidate_session_complete(out_dir, SESSION, CALCULATION_VERSION)

    out = coverage.scan_session(out_dir, SESSION)
    assert out["ohlcv_benchmark"] == registry.PRESENT
    assert out["market_structure"] == registry.PRESENT
    assert out["radar_candidate_history"] == registry.PRESENT


def test_missing_session_reports_missing_for_price_chain_only(tmp_path):
    out = coverage.scan_session(str(tmp_path), SESSION)
    for key in registry.PRICE_CHAIN_KEYS:
        assert out[key] == registry.MISSING


def test_non_price_components_are_never_misreported_as_missing(tmp_path):
    out = coverage.scan_session(str(tmp_path), SESSION)
    for key in registry.LATEST_ONLY_KEYS:
        assert out[key] == registry.LATEST_SESSION_ONLY
        assert out[key] != registry.MISSING
    assert out["radar_editorial_selections"] == registry.UNSUPPORTED_HISTORICALLY
    assert out["market_regime"] == registry.NOT_APPLICABLE


def test_market_structure_cutoff_is_compared_in_ist_not_utc(tmp_path):
    """Regression (verified live against production data): `load_revision_as_of` compares
    `universe_source.retrieved_at` LEXICALLY, and every stored `retrieved_at` is an IST-offset
    ISO string - converting the cutoff to UTC instead of IST before that lexical comparison
    gives the WRONG answer whenever the two offsets disagree on which hour digit sorts first,
    even though the absolute instants compare correctly. Chosen so a UTC-cutoff bug and an
    IST-cutoff fix give OPPOSITE answers for the same inputs:
      built_at  = 2026-10-09T20:00:00+05:30  (== 2026-10-09T14:30:00 UTC)
      now       = 2026-10-09T15:00:00 UTC    (== 2026-10-09T20:30:00+05:30)
    now is 30 minutes AFTER built_at in absolute time, so the revision must be PRESENT. A
    UTC-formatted cutoff ("...T15:00:00+00:00") sorts BEFORE the IST-formatted built_at
    ("...T20:00:00+05:30") as plain strings ("15" < "20"), wrongly reporting MISSING."""
    out_dir = str(tmp_path)
    built_at = "2026-10-09T20:00:00.000000+05:30"
    _write_market_structure_revision(out_dir, SESSION, built_at)
    now = dt.datetime(2026, 10, 9, 15, 0, tzinfo=dt.timezone.utc)
    out = coverage.scan_session(out_dir, SESSION, now=now)
    assert out["market_structure"] == registry.PRESENT


def test_scan_session_never_opens_a_network_seam(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("coverage.scan_session must never fetch")
    monkeypatch.setattr("market.history", _boom, raising=False)
    monkeypatch.setattr("radar.relative_acquisition.build_market_benchmark_series", _boom)
    coverage.scan_session(str(tmp_path), SESSION)

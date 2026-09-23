"""Focused, offline tests for the standalone Radar runner (Phase 4.2 Packet 5): artifact
assembly, coverage accounting, session alignment and JSON serialization. No network, no real
acquisition - mirrors `tests/test_composite_radar.py`'s hand-built-snapshot style.
"""
import datetime as dt
import json

from radar.models import (AnomalyLevel, DirectionCompatibility, TechnicalEvent, TechnicalEventType,
                          TechnicalRadarSnapshot, TechnicalStructure, RelativePerformance,
                          RelativePerformanceSnapshot, RelativePersistenceState, VolumeAnomaly,
                          VolumeRadarSnapshot)
from radar.composite import build_candidates
from radar.run import (assemble_radar_report, price_changes_from_series,
                       rvol_by_symbol_from_scan_report, save_radar_report)

SESSION = dt.date(2026, 9, 22)
AS_OF = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)


def make_volume(symbol, level, rvol=4.0, price_change_pct=0.8):
    return VolumeAnomaly(
        instrument=symbol, market_date=SESSION, current_volume=None, average_volume_20=None,
        relative_volume=rvol, prior_sessions_available=25, rvol_percentile=None,
        rvol_historical_rank=None, highest_rvol_in_n_sessions=None,
        price_change_pct=price_change_pct, level=level, why_flagged="test")


def make_technical(symbol, events=()):
    return TechnicalStructure(
        instrument=symbol, market_date=SESSION, close=100.0, prior_20_high=None, prior_20_low=None,
        prior_50_high=None, prior_50_low=None, sma20=None, sma50=None, distance_from_sma20_pct=None,
        distance_from_sma50_pct=None, recent_5d_range_pct=None, median_5d_range_pct=None,
        sessions_available=25, events=list(events), supporting_session_dates=[SESSION])


def make_relative(symbol, persistence_state=None, five=None, twenty=None):
    return RelativePerformance(
        instrument=symbol, session_date=SESSION, stock_return_1d=None, stock_return_5d=five,
        stock_return_20d=twenty, market_return_1d=None, market_return_5d=None, market_return_20d=None,
        market_relative_1d_pp=None, market_relative_5d_pp=five, market_relative_20d_pp=twenty,
        sector=None, sector_return_1d=None, sector_return_5d=None, sector_return_20d=None,
        sector_relative_1d_pp=None, sector_relative_5d_pp=None, sector_relative_20d_pp=None,
        persistence_state=persistence_state, relative_shift_pp=None,
        supporting_session_dates=[SESSION])


class FakeFact:
    def __init__(self, instrument, value):
        self.instrument = instrument
        self.value = value


class FakeScanReport:
    """Stands in for the RADAR_SCAN `MarketReport` - only `facts_for` is read by the runner."""
    def __init__(self, rvol_by_symbol):
        self._rvol = rvol_by_symbol

    def facts_for(self, metric):
        return [FakeFact(sym, val) for sym, val in self._rvol.items()]


def _build_universe():
    """3-family (ABC), 2-family (DEF), a rejected large mover (XYZ, +7.8% but only volume
    clears the bar) and a quiet non-mover (QUIET, no evidence at all)."""
    universe = {"ABC": "ABC Ltd", "DEF": "DEF Ltd", "XYZ": "XYZ Ltd", "QUIET": "Quiet Ltd"}

    volumes = [make_volume("ABC", AnomalyLevel.EXTREME, rvol=4.3, price_change_pct=0.8),
              make_volume("DEF", AnomalyLevel.UNUSUAL, rvol=3.5, price_change_pct=1.2),
              make_volume("XYZ", AnomalyLevel.ELEVATED, rvol=2.1, price_change_pct=7.8)]
    vsnap = VolumeRadarSnapshot(session_date=SESSION, generated_at=AS_OF, universe_size=4,
                                universe_source="test", anomalies=volumes,
                                scanned=["ABC", "DEF", "XYZ", "QUIET"])

    tech_events = [make_technical("ABC", [TechnicalEvent(
        event_type=TechnicalEventType.BREAK_ABOVE_20D_RANGE,
        evidence={"break_distance_pct": 1.4, "window_sessions": 20}, why="test")])]
    tsnap = TechnicalRadarSnapshot(session_date=SESSION, generated_at=AS_OF, universe_size=4,
                                   universe_source="test", flagged=tech_events,
                                   scanned=["ABC", "DEF", "XYZ", "QUIET"])

    relatives = [make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE, five=3.7, twenty=7.4),
                make_relative("DEF", RelativePersistenceState.PERSISTENT_POSITIVE, five=2.0, twenty=2.5)]
    rsnap = RelativePerformanceSnapshot(session_date=SESSION, generated_at=AS_OF, universe_size=4,
                                        universe_source="test", benchmark_source="NIFTY 50 (^NSEI)",
                                        results=relatives,
                                        scanned=["ABC", "DEF", "XYZ", "QUIET"])

    series = {
        "ABC": [{"date": SESSION - dt.timedelta(days=1), "high": 500, "low": 495, "close": 503.0},
               {"date": SESSION, "high": 510, "low": 500, "close": 507.0}],
        "DEF": [{"date": SESSION - dt.timedelta(days=1), "high": 200, "low": 198, "close": 200.0},
               {"date": SESSION, "high": 204, "low": 199, "close": 202.4}],
        "XYZ": [{"date": SESSION - dt.timedelta(days=1), "high": 100, "low": 98, "close": 100.0},
               {"date": SESSION, "high": 110, "low": 99, "close": 107.8}],
        "QUIET": [{"date": SESSION - dt.timedelta(days=1), "high": 50, "low": 49, "close": 50.0},
                 {"date": SESSION, "high": 50.2, "low": 49.8, "close": 50.05}],
    }
    scan_report = FakeScanReport({"ABC": 4.3, "DEF": 3.5, "XYZ": 2.1})

    return universe, vsnap, tsnap, rsnap, series, scan_report


def test_assemble_radar_report_coverage_and_candidates():
    universe, vsnap, tsnap, rsnap, series, scan_report = _build_universe()
    csnap = build_candidates(vsnap, tsnap, rsnap, SESSION, as_of=AS_OF)

    report = assemble_radar_report(
        universe_name="NIFTY200", universe=universe, session_date=SESSION,
        benchmark_session=SESSION, volume_snapshot=vsnap, technical_snapshot=tsnap,
        relative_snapshot=rsnap, composite_snapshot=csnap, series_by_symbol=series,
        scan_report=scan_report, data_problems={}, timings={"total_s": 1.0}, generated_at=AS_OF)

    assert report["universe"]["requested"] == 4
    assert report["detector_coverage"]["candidate_count"] == 2          # ABC (3-family), DEF (2-family)
    assert report["detector_coverage"]["three_family_candidates"] == 1
    assert report["detector_coverage"]["two_family_candidates"] == 1
    assert {c["instrument"] for c in report["candidates"]} == {"ABC", "DEF"}


def test_rejected_large_movers_excludes_candidates_and_flags_xyz():
    universe, vsnap, tsnap, rsnap, series, scan_report = _build_universe()
    csnap = build_candidates(vsnap, tsnap, rsnap, SESSION, as_of=AS_OF)

    report = assemble_radar_report(
        universe_name="NIFTY200", universe=universe, session_date=SESSION,
        benchmark_session=SESSION, volume_snapshot=vsnap, technical_snapshot=tsnap,
        relative_snapshot=rsnap, composite_snapshot=csnap, series_by_symbol=series,
        scan_report=scan_report, data_problems={}, timings={}, generated_at=AS_OF)

    rejected_symbols = {r["symbol"] for r in report["rejected_large_movers"]}
    assert "XYZ" in rejected_symbols                     # +7.8% move, only ELEVATED volume clears
    assert "ABC" not in rejected_symbols and "DEF" not in rejected_symbols   # both are candidates
    xyz = next(r for r in report["rejected_large_movers"] if r["symbol"] == "XYZ")
    assert xyz["meaningful_families"] == 0                # ELEVATED alone never activates VOLUME


def test_manual_review_orders_three_family_before_two_family():
    universe, vsnap, tsnap, rsnap, series, scan_report = _build_universe()
    csnap = build_candidates(vsnap, tsnap, rsnap, SESSION, as_of=AS_OF)
    report = assemble_radar_report(
        universe_name="NIFTY200", universe=universe, session_date=SESSION,
        benchmark_session=SESSION, volume_snapshot=vsnap, technical_snapshot=tsnap,
        relative_snapshot=rsnap, composite_snapshot=csnap, series_by_symbol=series,
        scan_report=scan_report, data_problems={}, timings={}, generated_at=AS_OF)

    symbols_in_order = [row["symbol"] for row in report["manual_review"]]
    assert symbols_in_order == ["ABC", "DEF"]             # 3-family before 2-family


def test_session_mismatch_between_snapshots_is_excluded_not_partially_trusted():
    """A composite snapshot built against a mismatched relative-performance session should not
    let that detector's evidence leak into candidates for the requested session - this mirrors
    `radar.composite`'s own session-alignment guarantee and is exercised here through the
    runner's real call path rather than re-asserted against `radar.composite` directly."""
    universe, vsnap, tsnap, rsnap, series, scan_report = _build_universe()
    other_session = SESSION - dt.timedelta(days=1)
    stale_rsnap = RelativePerformanceSnapshot(
        session_date=other_session, generated_at=AS_OF, universe_size=4, universe_source="test",
        results=rsnap.results, scanned=rsnap.scanned)

    csnap = build_candidates(vsnap, tsnap, stale_rsnap, SESSION, as_of=AS_OF)
    assert any("relative" in w and str(other_session) in w for w in csnap.warnings)
    # ABC only clears 2 families now (volume + structure) since relative was dropped entirely.
    abc = next(c for c in csnap.candidates if c.instrument == "ABC")
    assert abc.independent_signal_count == 2


def test_price_changes_from_series():
    _, _, _, _, series, _ = _build_universe()
    changes = price_changes_from_series(series)
    assert changes["ABC"] == (507.0 / 503.0 - 1) * 100
    assert "QUIET" in changes and abs(changes["QUIET"]) < 1.0


def test_rvol_by_symbol_from_scan_report_reads_every_scanned_reading_not_just_anomalies():
    scan_report = FakeScanReport({"ABC": 4.3, "QUIET": 1.1})
    rvol = rvol_by_symbol_from_scan_report(scan_report)
    assert rvol == {"ABC": 4.3, "QUIET": 1.1}             # QUIET never appears in any anomalies list


def test_save_radar_report_round_trips_through_json(tmp_path):
    universe, vsnap, tsnap, rsnap, series, scan_report = _build_universe()
    csnap = build_candidates(vsnap, tsnap, rsnap, SESSION, as_of=AS_OF)
    report = assemble_radar_report(
        universe_name="NIFTY200", universe=universe, session_date=SESSION,
        benchmark_session=SESSION, volume_snapshot=vsnap, technical_snapshot=tsnap,
        relative_snapshot=rsnap, composite_snapshot=csnap, series_by_symbol=series,
        scan_report=scan_report, data_problems={}, timings={}, generated_at=AS_OF)

    path = save_radar_report(report, out_dir=str(tmp_path))
    assert path.endswith(f"radar_{SESSION.isoformat()}.json")
    with open(path, encoding="utf-8") as fh:
        reloaded = json.load(fh)
    assert reloaded["session_date"] == SESSION.isoformat()
    assert {c["instrument"] for c in reloaded["candidates"]} == {"ABC", "DEF"}

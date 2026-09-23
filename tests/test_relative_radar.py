"""Relative-performance radar: return math, market/sector-relative differentials, session
alignment, persistence classification, and the boundaries it must not cross - no acquisition,
no fabrication, no dependency on the volume or technical detectors. Offline only.
"""
import copy
import datetime as dt

import pytest

from radar import (RelativePerformanceThresholds, RelativePersistenceState,
                   scan_relative_performance_universe)
from radar.relative import _classify_persistence, _return_pct

BASE = dt.date(2026, 1, 1)


def build_series(closes: list, start: dt.date = BASE) -> list:
    """Oldest-first session list, one entry per close, dates simply incrementing business-day
    style (the detector only cares about the calendar date each close is tagged with)."""
    return [{"date": start + dt.timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def flat(n: int, value: float = 100.0, start: dt.date = BASE) -> list:
    return build_series([value] * n, start)


# =============================================================== return mathematics
def test_1d_return():
    series = build_series([100.0, 104.0])
    ret, prior_date, current_date = _return_pct(series, 1)
    assert ret == pytest.approx(4.0)
    assert prior_date == BASE
    assert current_date == BASE + dt.timedelta(days=1)


def test_5d_return_needs_six_closes():
    series = build_series([100.0, 101, 102, 103, 104, 104.8])
    ret, *_ = _return_pct(series, 5)
    assert ret == pytest.approx(4.8)


def test_5d_return_unavailable_with_only_five_closes():
    series = build_series([100.0, 101, 102, 103, 104])
    ret, prior_date, current_date = _return_pct(series, 5)
    assert ret is None and prior_date is None and current_date is None


def test_20d_return():
    series = build_series([100.0] * 20 + [108.1])
    ret, *_ = _return_pct(series, 20)
    assert ret == pytest.approx(8.1)


def test_return_none_on_non_positive_close():
    series = build_series([0.0, 104.0])
    assert _return_pct(series, 1)[0] is None
    series2 = build_series([100.0, -5.0])
    assert _return_pct(series2, 1)[0] is None


# =============================================================== relative mathematics
def test_stock_vs_nifty_relative_performance():
    """Packet spec example: stock +4.8% over 5D, Nifty +1.6% -> +3.2pp relative."""
    stock = build_series([100.0, 100, 100, 100, 100, 104.8])
    nifty = build_series([100.0, 100, 100, 100, 100, 101.6])
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, nifty, stock[-1]["date"], as_of=dt.datetime(2026, 1, 1))
    result = snap.results[0]
    assert result.stock_return_5d == pytest.approx(4.8)
    assert result.market_return_5d == pytest.approx(1.6)
    assert result.market_relative_5d_pp == pytest.approx(3.2, abs=1e-9)


def test_stock_vs_sector_relative_performance():
    """Packet spec Case B: stock +3.0%, sector +5.2% -> -2.2pp, must NOT read as strength."""
    stock = build_series([100.0, 100, 100, 100, 100, 103.0])
    nifty = build_series([100.0] * 6)
    sector = build_series([100.0, 100, 100, 100, 100, 105.2])
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, nifty, stock[-1]["date"],
        sector_map={"ABC": "Auto"}, sector_series_by_name={"Auto": sector},
        sector_source="market.SECTORS")
    result = snap.results[0]
    assert result.sector_return_5d == pytest.approx(5.2)
    assert result.sector_relative_5d_pp == pytest.approx(-2.2, abs=1e-9)
    assert result.sector == "Auto"


# =============================================================== alignment
def test_matched_sessions_all_windows_available():
    stock = flat(21, 100.0)
    stock[-1]["close"] = 110.0
    nifty = flat(21, 100.0)
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"])
    result = snap.results[0]
    assert result.stock_return_1d is not None
    assert result.stock_return_5d is not None
    assert result.stock_return_20d is not None


def test_missing_stock_session_skips_symbol():
    """Fewer than 2 sessions, or a series not ending at session_date, is a skip - never a
    guess."""
    stock = build_series([100.0])
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, flat(5), dt.date(2026, 6, 1))
    assert "ABC" not in snap.scanned
    assert "ABC" in snap.skipped


def test_stale_current_session_is_skipped_not_substituted():
    stock = build_series([100.0, 101.0])                 # last session BASE+1
    session_date = BASE + dt.timedelta(days=5)            # requested session is later
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, flat(5), session_date)
    assert "ABC" in snap.skipped
    assert "ABC" not in snap.scanned


def test_missing_benchmark_session_leaves_market_relative_unavailable():
    """The stock has a full 5D window but the benchmark is missing a session inside it -
    market-relative for that window must be None, never silently substituted."""
    stock = build_series([100.0, 101, 102, 103, 104, 104.8])
    nifty = build_series([100.0, 100, 100, 100, 100, 101.6])
    del nifty[0]                                          # drop the "5 sessions ago" date
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"])
    result = snap.results[0]
    assert result.stock_return_5d is not None
    assert result.market_return_5d is None
    assert result.market_relative_5d_pp is None
    assert any("5D market-relative unavailable" in note for note in result.data_quality)


def test_partial_window_availability_5d_available_20d_unavailable():
    stock = build_series([100.0] * 6)
    stock[-1]["close"] = 104.8
    nifty = build_series([100.0] * 6)
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"])
    result = snap.results[0]
    assert result.stock_return_5d is not None
    assert result.stock_return_20d is None
    assert any("20D unavailable" in note for note in result.data_quality)


# =============================================================== persistence
def test_persistence_positive():
    assert _classify_persistence(2.2, 8.1, RelativePerformanceThresholds()) is \
        RelativePersistenceState.PERSISTENT_POSITIVE


def test_persistence_negative():
    assert _classify_persistence(-3.0, -6.0, RelativePerformanceThresholds()) is \
        RelativePersistenceState.PERSISTENT_NEGATIVE


def test_persistence_neutral():
    assert _classify_persistence(0.2, -0.4, RelativePerformanceThresholds()) is \
        RelativePersistenceState.NEUTRAL


def test_persistence_mixed_on_recent_reversal():
    """Packet spec Case D: 5D +3.5, 20D -5.0 must NOT read as persistent."""
    state = _classify_persistence(3.5, -5.0, RelativePerformanceThresholds())
    assert state is RelativePersistenceState.MIXED
    assert state is not RelativePersistenceState.PERSISTENT_POSITIVE


def test_persistence_none_when_a_window_is_missing():
    assert _classify_persistence(3.5, None, RelativePerformanceThresholds()) is None


def test_relative_shift_preserved_on_reversal_when_persistence_not_positive():
    """Case D: even though persistence is not PERSISTENT_POSITIVE, the raw 5D/20D evidence and
    the shift between them must still be preserved for a later packet to reason about."""
    stock = build_series([100.0] * 21)
    for i in range(1, 6):
        stock[-i]["close"] = 100.0 * (1 + 0.035 * (6 - i) / 5)   # ramps toward +3.5% at day 0
    stock[-1]["close"] = 103.5
    nifty = flat(21, 100.0)
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"])
    result = snap.results[0]
    assert result.market_relative_5d_pp is not None
    assert result.market_relative_20d_pp is not None
    assert result.relative_shift_pp == pytest.approx(
        result.market_relative_5d_pp - result.market_relative_20d_pp)


# =============================================================== independence
def test_case_a_quiet_non_mover_relative_strength_preserved():
    """Packet spec Case A: a stock that is not a top mover must still carry its relative
    performance - this detector never filters by "was it a mover"."""
    stock = build_series([100.0, 100.8])
    nifty = build_series([100.0, 98.8])
    snap = scan_relative_performance_universe(["QUIET"], {"QUIET": stock}, nifty, stock[-1]["date"])
    result = snap.results[0]
    assert result.market_relative_1d_pp == pytest.approx(2.0, abs=1e-9)


def test_module_never_imports_volume_or_technical_detectors():
    import radar.relative as relative_module
    import radar.relative_acquisition as acquisition_module
    for module in (relative_module, acquisition_module):
        assert not hasattr(module, "volume") and not hasattr(module, "technical")
        assert "VolumeAnomaly" not in dir(module) and "TechnicalStructure" not in dir(module)


def test_no_volume_or_technical_parameter_required():
    """The public API takes only price series - no VolumeAnomaly/TechnicalStructure input is
    even expressible, let alone required."""
    stock = build_series([100.0, 101.0])
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, flat(5), stock[-1]["date"])
    assert snap.results


# =============================================================== sector
def test_sector_absent_mapping_leaves_sector_relative_unavailable():
    stock = build_series([100.0, 101.0])
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, flat(5), stock[-1]["date"])
    result = snap.results[0]
    assert result.sector is None
    assert result.sector_relative_1d_pp is None
    assert any("no sector mapping supplied" in note for note in result.data_quality)


def test_sector_mapped_but_benchmark_series_absent():
    stock = build_series([100.0, 101.0])
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, flat(5), stock[-1]["date"], sector_map={"ABC": "Auto"})
    result = snap.results[0]
    assert result.sector == "Auto"
    assert result.sector_relative_1d_pp is None
    assert any("no benchmark series supplied for sector 'Auto'" in note
              for note in result.data_quality)


def test_sector_valid_mapping_and_benchmark():
    stock = build_series([100.0, 102.0])
    sector = build_series([100.0, 101.0])
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, flat(5), stock[-1]["date"], sector_map={"ABC": "Auto"},
        sector_series_by_name={"Auto": sector})
    result = snap.results[0]
    assert result.sector_relative_1d_pp == pytest.approx(1.0, abs=1e-9)


# =============================================================== data quality
def test_duplicate_session_dates_flagged():
    stock = build_series([100.0, 101.0])
    stock.append(dict(stock[-1]))                          # duplicate the last date
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, flat(5), stock[-1]["date"])
    result = snap.results[0]
    assert any("duplicate session dates" in note for note in result.data_quality)


def test_invalid_close_in_series_yields_no_return_for_that_window():
    stock = build_series([100.0, -5.0])
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, flat(5), stock[-1]["date"])
    result = snap.results[0]
    assert result.stock_return_1d is None


def test_suspended_stock_no_current_session_is_skipped():
    stock = build_series([100.0, 101.0])                  # last session is BASE+1
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, flat(5), BASE + dt.timedelta(days=30))
    assert "ABC" in snap.skipped
    assert not snap.results


def test_empty_benchmark_series_still_yields_stock_returns():
    stock = build_series([100.0, 101.0])
    snap = scan_relative_performance_universe(["ABC"], {"ABC": stock}, [], stock[-1]["date"])
    result = snap.results[0]
    assert result.stock_return_1d is not None
    assert result.market_return_1d is None
    assert any("benchmark series empty or unavailable" in w for w in snap.warnings)


# =============================================================== determinism / immutability
def test_deterministic_output_given_same_input():
    stock = build_series([100.0, 101, 102, 103, 104, 104.8])
    nifty = build_series([100.0, 100, 100, 100, 100, 101.6])
    as_of = dt.datetime(2026, 1, 1, 7, 40)
    snap1 = scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"],
                                                as_of=as_of)
    snap2 = scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"],
                                                as_of=as_of)
    assert snap1.to_dict() == snap2.to_dict()


def test_inputs_are_never_mutated():
    stock = build_series([100.0, 101, 102, 103, 104, 104.8])
    nifty = build_series([100.0, 100, 100, 100, 100, 101.6])
    stock_before, nifty_before = copy.deepcopy(stock), copy.deepcopy(nifty)
    scan_relative_performance_universe(["ABC"], {"ABC": stock}, nifty, stock[-1]["date"],
                                       sector_map={"ABC": "Auto"}, sector_series_by_name={})
    assert stock == stock_before
    assert nifty == nifty_before


def test_snapshot_round_trips_through_json():
    from radar import RelativePerformanceSnapshot
    stock = build_series([100.0, 101, 102, 103, 104, 104.8])
    nifty = build_series([100.0, 100, 100, 100, 100, 101.6])
    snap = scan_relative_performance_universe(
        ["ABC"], {"ABC": stock}, nifty, stock[-1]["date"],
        sector_map={"ABC": "Auto"}, sector_series_by_name={"Auto": nifty},
        as_of=dt.datetime(2026, 1, 1))
    restored = RelativePerformanceSnapshot.from_json(snap.to_json())
    assert restored.to_dict() == snap.to_dict()

"""radar.technical.scan_technical_universe / _build_structure: technical-structure detection
(Phase 4.2 Packet 2). Fully offline and pure - no network, no database, no canonical history;
input is a plain per-symbol OHLC session list, exactly what
`radar.technical_acquisition.build_universe_technical_series` returns.
"""
import datetime as dt

import pytest

from radar import technical
from radar.models import TechnicalEventType
from radar.thresholds import TechnicalThresholds

BASE = dt.date(2026, 1, 1)


def build_series(closes, highs=None, lows=None):
    """Oldest-first session list, one entry per `closes[i]`, dates simply incrementing -
    the detector only cares about order and count, never calendar semantics."""
    highs = highs or closes
    lows = lows or closes
    return [{"date": BASE + dt.timedelta(days=i), "high": h, "low": l, "close": c}
           for i, (c, h, l) in enumerate(zip(closes, highs, lows))]


NO_COMPRESSION = TechnicalThresholds(compression_lookback_windows=10_000)  # never satisfiable


def event_types(structure):
    return {e.event_type for e in structure.events}


# --------------------------------------------------------------------------- 20-session range
def test_close_above_prior_20_session_high_breaks_above():
    closes = [100.0] * 20 + [107.0]          # 20 prior sessions at 100, then a breakout
    series = build_series(closes)
    structure = technical._build_structure("SYM", series, NO_COMPRESSION)
    assert TechnicalEventType.BREAK_ABOVE_20D_RANGE in event_types(structure)
    event = next(e for e in structure.events if e.event_type == TechnicalEventType.BREAK_ABOVE_20D_RANGE)
    assert event.evidence["prior_high"] == pytest.approx(100.0)
    assert event.evidence["break_distance_pct"] == pytest.approx(7.0)


def test_close_exactly_equal_to_prior_high_is_not_a_break():
    closes = [100.0] * 20 + [100.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.BREAK_ABOVE_20D_RANGE not in event_types(structure)
    assert TechnicalEventType.BREAK_BELOW_20D_RANGE not in event_types(structure)


def test_close_below_prior_20_session_low_breaks_below():
    closes = [100.0] * 20 + [93.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.BREAK_BELOW_20D_RANGE in event_types(structure)
    event = next(e for e in structure.events if e.event_type == TechnicalEventType.BREAK_BELOW_20D_RANGE)
    assert event.evidence["prior_low"] == pytest.approx(100.0)
    assert event.evidence["break_distance_pct"] == pytest.approx(-7.0)


def test_current_session_excluded_from_its_own_range():
    """The current session's own extreme high must never count toward the prior-20 range that
    is supposed to be judging it."""
    closes = [100.0] * 19 + [50.0, 200.0]   # the dip at index 19 must still bound the range
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert structure.prior_20_low == pytest.approx(50.0)
    assert TechnicalEventType.BREAK_ABOVE_20D_RANGE in event_types(structure)


def test_insufficient_20_session_history_produces_no_break_event():
    closes = [100.0] * 10 + [200.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert structure.prior_20_high is None
    assert not any(e.event_type in (TechnicalEventType.BREAK_ABOVE_20D_RANGE,
                                    TechnicalEventType.BREAK_BELOW_20D_RANGE)
                  for e in structure.events)
    assert any("20-session" in note for note in structure.data_quality)


# --------------------------------------------------------------------------- 50-session range
def test_valid_50_session_upper_break():
    closes = [100.0] * 50 + [110.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.BREAK_ABOVE_50D_RANGE in event_types(structure)


def test_valid_50_session_lower_break():
    closes = [100.0] * 50 + [88.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.BREAK_BELOW_50D_RANGE in event_types(structure)


def test_insufficient_50_session_history_does_not_invalidate_20_session_result():
    closes = [100.0] * 30 + [107.0]   # 30 prior: enough for 20D, not enough for 50D
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert structure.prior_50_high is None
    assert TechnicalEventType.BREAK_ABOVE_20D_RANGE in event_types(structure)
    assert TechnicalEventType.BREAK_ABOVE_50D_RANGE not in event_types(structure)


# --------------------------------------------------------------------------- SMA / crosses
def test_sma20_is_the_inclusive_mean_of_the_last_20_closes():
    closes = list(range(1, 21))   # 1..20, mean = 10.5
    structure = technical._build_structure("SYM", build_series([float(c) for c in closes]), NO_COMPRESSION)
    assert structure.sma20 == pytest.approx(10.5)


def test_sma50_is_the_inclusive_mean_of_the_last_50_closes():
    closes = [float(c) for c in range(1, 51)]   # mean = 25.5
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert structure.sma50 == pytest.approx(25.5)


def test_cross_above_sma20():
    # 20 sessions ending just below their own SMA20 (98.9 vs 100 mean), then a jump above.
    closes = [100.0] * 19 + [95.0, 120.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.CROSS_ABOVE_SMA20 in event_types(structure)


def test_cross_below_sma20():
    closes = [100.0] * 19 + [105.0, 80.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.CROSS_BELOW_SMA20 in event_types(structure)


def test_cross_above_sma50():
    closes = [100.0] * 49 + [95.0, 130.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.CROSS_ABOVE_SMA50 in event_types(structure)


def test_cross_below_sma50():
    closes = [100.0] * 49 + [105.0, 70.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.CROSS_BELOW_SMA50 in event_types(structure)


def test_remaining_above_sma_does_not_emit_a_repeated_cross():
    closes = [100.0] * 18 + [110.0, 111.0, 112.0]   # already above SMA20, then stays above
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.CROSS_ABOVE_SMA20 not in event_types(structure)


# --------------------------------------------------------------------------- data quality
def test_newly_listed_stock_with_very_short_history_does_not_crash():
    closes = [100.0, 101.0, 99.0]
    structure = technical._build_structure("SYM", build_series(closes), NO_COMPRESSION)
    assert structure.events == []
    assert structure.sma20 is None and structure.sma50 is None


# --------------------------------------------------------------------------- universe scan
def test_non_mover_technical_event_is_detected_regardless_of_price_change():
    """Universe independence: scan_technical_universe never filters by price move, RVOL or
    editorial mover selection - only by whether a usable series exists."""
    closes = [100.0] * 20 + [107.0]
    series_by_symbol = {"NONMOVER": build_series(closes)}
    snapshot = technical.scan_technical_universe(
        ["NONMOVER"], series_by_symbol, BASE, thresholds=NO_COMPRESSION)
    assert [s.instrument for s in snapshot.flagged] == ["NONMOVER"]


def test_top_mover_inside_range_is_not_falsely_flagged():
    """A large price move that never leaves its own 20-session range must not earn a range
    event merely because the move itself was big."""
    closes = [100.0] * 20 + [95.0]      # -5% but still inside [100 low.. well need real range]
    # Build a range wide enough that an 8% move stays inside it.
    wide = [90.0, 110.0] * 10           # prior 20 sessions oscillate 90..110
    closes = wide + [97.2]              # +8% vs previous close (90.0) but inside [90, 110]
    structure = technical._build_structure("STOCK-B", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.BREAK_ABOVE_20D_RANGE not in event_types(structure)
    assert TechnicalEventType.BREAK_BELOW_20D_RANGE not in event_types(structure)


def test_skipped_symbols_are_recorded_with_reason():
    snapshot = technical.scan_technical_universe(
        ["A", "B"], {"A": build_series([100.0] * 25)}, BASE,
        skip_reasons={"B": "no_recap_row"})
    assert snapshot.skipped == {"B": "no_recap_row"}
    assert snapshot.scanned == ["A"]


# --------------------------------------------------------------------------- independence
def test_technical_detector_does_not_import_volume_module():
    """`radar.technical` must not depend on `radar.volume` or its types - the two detectors
    are independent by construction, never combined until a later composite packet."""
    import ast
    import radar.technical as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(n.name for n in node.names)
    assert not any("volume" in name for name in imported)


# --------------------------------------------------------------------------- combined example
def test_combined_example_stock_a_breaks_above_range_independent_of_volume_classification():
    """Section 11 of the packet spec: a modest +0.9% move with extreme RVOL and a genuine
    20-session breakout. The technical detector must reach BREAK_ABOVE_20D_RANGE knowing
    nothing about the volume side - there is no RVOL input to this function at all."""
    closes = [100.0] * 20 + [500.0] + [502.0, 502.5, 503.0]  # prior-20 high settles at 500
    # Simplify: prior 20 sessions all at 500 high, then close 507 (> 500).
    closes = [500.0] * 20 + [507.0]
    structure = technical._build_structure("STOCK-A", build_series(closes), NO_COMPRESSION)
    assert TechnicalEventType.BREAK_ABOVE_20D_RANGE in event_types(structure)
    event = next(e for e in structure.events if e.event_type == TechnicalEventType.BREAK_ABOVE_20D_RANGE)
    assert event.evidence["prior_high"] == pytest.approx(500.0)


# --------------------------------------------------------------------------- compression
def test_range_compression_is_flagged_when_recent_range_is_tight_versus_history():
    thresholds = TechnicalThresholds(compression_window=5, compression_lookback_windows=3,
                                     compression_ratio=0.6)
    wide_window = [90.0, 110.0, 90.0, 110.0, 90.0]     # ~22% range each, repeated 3x
    tight_window = [99.0, 101.0, 99.0, 101.0, 100.0]    # ~2% range
    closes = wide_window * 3 + tight_window
    structure = technical._build_structure("SYM", build_series(closes), thresholds)
    assert TechnicalEventType.RANGE_COMPRESSION in event_types(structure)


def test_range_compression_not_flagged_without_full_prior_window_sample():
    thresholds = TechnicalThresholds(compression_window=5, compression_lookback_windows=10)
    closes = [90.0, 110.0, 90.0, 110.0, 90.0] * 2 + [99.0, 101.0, 99.0, 101.0, 100.0]
    structure = technical._build_structure("SYM", build_series(closes), thresholds)
    assert TechnicalEventType.RANGE_COMPRESSION not in event_types(structure)
    assert structure.median_5d_range_pct is None

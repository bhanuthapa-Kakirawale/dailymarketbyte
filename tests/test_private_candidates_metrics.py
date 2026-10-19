"""Pure-function tests for the new, small deterministic calculations this package introduces:
ATR14, weekly resample/alignment, structural reference/extension/pullback/risk-width,
volatility/liquidity bands, regime streak, trading-session event distance, return correlation.
"""
import datetime as dt
from types import SimpleNamespace

import pytest

from core.trading_calendar import SessionCalendar
from private_candidates import metrics
from private_candidates.policy import DEFAULT_SCORING_POLICY as POLICY


def _daily_rows(n, start=dt.date(2026, 1, 5)):
    """`n` consecutive trading-calendar sessions, close rising by 1/day, high=close+1, low=close-1."""
    cal = SessionCalendar()
    days = []
    d = start
    while len(days) < n:
        if cal.status(d) == "SESSION":
            days.append(d)
        d += dt.timedelta(days=1)
    rows = []
    for i, day in enumerate(days):
        close = 100.0 + i
        rows.append({"date": day, "open": close - 0.5, "high": close + 1, "low": close - 1,
                    "close": close, "volume": 1_000_000})
    return rows


def test_atr14_requires_window_plus_one_rows():
    assert metrics.atr14(_daily_rows(14), window=14) is None
    assert metrics.atr14(_daily_rows(15), window=14) == pytest.approx(2.0)


def test_weekly_resample_excludes_the_current_partial_week():
    rows = _daily_rows(5 * 4)   # 4 full weeks of trading-calendar sessions
    # append 2 more days into a 5th, still-forming week
    extra_start = rows[-1]["date"] + dt.timedelta(days=1)
    rows += _daily_rows(2, start=extra_start)
    weeks = metrics.weekly_resample(rows)
    # the last 2 rows' week must never appear as completed
    last_week_start = rows[-1]["date"] - dt.timedelta(days=rows[-1]["date"].weekday())
    assert all(w["week_start"] != last_week_start for w in weeks)


def test_weekly_alignment_insufficient_below_minimum_weeks():
    rows = _daily_rows(5 * 3)   # only 3 completed-ish weeks worth
    out = metrics.weekly_alignment(rows, direction_sign=1, policy=POLICY)
    assert out["alignment"] == "WEEKLY_INSUFFICIENT_DATA"


def test_weekly_alignment_detects_aligned_and_divergent():
    rows = _daily_rows(5 * 12)   # an uptrend across 12 weeks
    aligned = metrics.weekly_alignment(rows, direction_sign=1, policy=POLICY)
    assert aligned["alignment"] == "ALIGNED"
    divergent = metrics.weekly_alignment(rows, direction_sign=-1, policy=POLICY)
    assert divergent["alignment"] == "DIVERGENT"


TECH = {"prior_20_low": 90, "prior_20_high": 110, "prior_50_low": 80, "prior_50_high": 115}


def _with(*events) -> dict:
    return dict(TECH, events=[{"event_type": e} for e in events])


def test_structural_reference_without_a_range_break_is_the_far_end_of_the_20_session_range():
    assert metrics.structural_reference(TECH, 1) == {"price": 90, "kind": "PRIOR_20_LOW"}
    assert metrics.structural_reference(TECH, -1) == {"price": 110, "kind": "PRIOR_20_HIGH"}
    assert metrics.structural_reference(TECH, 0) == {"price": None, "kind": None}


def test_structural_reference_for_a_range_break_is_the_level_it_cleared():
    """2026-10-16: with the far prior-20 low as reference, 22 of 24 breakouts read
    EXTENDED_FROM_STRUCTURE / WIDE. A break in the setup direction now references the broken
    level (the 50-session one first - the nearer of the two)."""
    assert metrics.structural_reference(_with("BREAK_ABOVE_20D_RANGE"), 1) == {
        "price": 110, "kind": "BROKEN_PRIOR_20_HIGH"}
    assert metrics.structural_reference(
        _with("BREAK_ABOVE_20D_RANGE", "BREAK_ABOVE_50D_RANGE"), 1) == {
        "price": 115, "kind": "BROKEN_PRIOR_50_HIGH"}
    assert metrics.structural_reference(_with("BREAK_BELOW_20D_RANGE"), -1) == {
        "price": 90, "kind": "BROKEN_PRIOR_20_LOW"}
    # a break against the setup direction is not this setup's reference
    assert metrics.structural_reference(_with("BREAK_BELOW_20D_RANGE"), 1) == {
        "price": 90, "kind": "PRIOR_20_LOW"}


@pytest.mark.parametrize("session, window", [
    (dt.date(2026, 10, 16), (dt.date(2026, 10, 1), dt.date(2026, 11, 14))),
    (dt.date(2026, 7, 1), (dt.date(2026, 7, 1), dt.date(2026, 8, 14))),
    (dt.date(2026, 1, 10), (dt.date(2026, 1, 1), dt.date(2026, 2, 14))),
    (dt.date(2026, 5, 29), (dt.date(2026, 4, 1), dt.date(2026, 5, 30))),   # annual: 60 days
    (dt.date(2026, 11, 20), None),
    (dt.date(2026, 6, 15), None),
])
def test_results_window_follows_the_lodr_filing_deadlines(session, window):
    assert metrics.results_window(session, POLICY) == window


def test_range_position_pct_clamped():
    assert metrics.range_position_pct(100, 90, 110) == pytest.approx(50.0)
    assert metrics.range_position_pct(90, 90, 110) == pytest.approx(0.0)
    assert metrics.range_position_pct(200, 90, 110) == pytest.approx(100.0)
    assert metrics.range_position_pct(100, 90, 90) is None


def test_extension_state_bands():
    # close=100, distance_from_sma20_pct=1% -> distance_price=1, atr=1 -> 1 ATR unit -> NORMAL
    assert metrics.extension_state(1.0, 100, 1.0, POLICY)["state"] == "NORMAL"
    # 2.5% distance, atr=1 -> 2.5 ATR units -> STRETCHED
    assert metrics.extension_state(2.5, 100, 1.0, POLICY)["state"] == "STRETCHED"
    # 5% distance, atr=1 -> 5 ATR units -> DEEPLY_STRETCHED
    assert metrics.extension_state(5.0, 100, 1.0, POLICY)["state"] == "DEEPLY_STRETCHED"
    assert metrics.extension_state(None, 100, 1.0, POLICY)["state"] == "EXTENSION_UNKNOWN"


def test_pullback_and_risk_width_bands():
    assert metrics.pullback_state(100, 99.5, 1.0, POLICY)["state"] == "AT_STRUCTURE"
    assert metrics.pullback_state(100, 98, 1.0, POLICY)["state"] == "NEAR_STRUCTURE"
    assert metrics.pullback_state(100, 90, 1.0, POLICY)["state"] == "EXTENDED_FROM_STRUCTURE"
    assert metrics.risk_width_label(100, 99, 1.0, POLICY)["label"] == "NARROW"
    assert metrics.risk_width_label(100, 90, 1.0, POLICY)["label"] == "WIDE"


def test_average_volume_fallback_for_liquidity():
    rows = _daily_rows(25)
    assert metrics.average_volume(rows, window=20) == pytest.approx(1_000_000.0)
    assert metrics.average_volume(rows[:3], window=20) is None


def test_volatility_and_liquidity_bands():
    assert metrics.volatility_band(1.0, POLICY) == "TIGHT"
    assert metrics.volatility_band(2.0, POLICY) == "NORMAL"
    assert metrics.volatility_band(4.0, POLICY) == "ELEVATED"
    assert metrics.volatility_band(10.0, POLICY) == "EXTREME"
    assert metrics.volatility_band(None, POLICY) == "VOLATILITY_UNKNOWN"
    assert metrics.liquidity_band(1_000, 100, POLICY)["band"] == "THIN"
    assert metrics.liquidity_band(10_000_000, 100, POLICY)["band"] == "DEEP"
    assert metrics.liquidity_band(None, 100, POLICY)["band"] == "LIQUIDITY_UNKNOWN"


def _snap(session_date, regime):
    return SimpleNamespace(session_date=session_date, regime=regime)


def test_regime_streak_breaks_on_mismatch_not_skips_over_it():
    snaps = [_snap("2026-09-14", "BULLISH"), _snap("2026-09-15", "BULLISH"),
            _snap("2026-09-16", "NEUTRAL"), _snap("2026-09-17", "BULLISH"),
            _snap("2026-09-18", "BULLISH")]
    assert metrics.regime_streak(snaps, "2026-09-18") == 2
    assert metrics.regime_streak(snaps, "2026-09-15") == 2


def test_regime_streak_zero_when_session_not_last():
    snaps = [_snap("2026-09-14", "BULLISH")]
    assert metrics.regime_streak(snaps, "2026-09-18") == 0


def test_trading_session_distance_counts_sessions_not_calendar_days():
    cal = SessionCalendar()
    # 2026-09-18 (Fri) -> 2026-09-21 (Mon) is 1 trading session apart, 3 calendar days
    d = metrics.trading_session_distance(cal, dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    assert d == 1


def test_nearest_event_distance_picks_the_closest():
    cal = SessionCalendar()
    events = [{"data_as_of": "2026-10-01"}, {"data_as_of": "2026-09-21"}]
    best = metrics.nearest_event_distance(events, dt.date(2026, 9, 18), cal)
    assert best == 1


def test_pairwise_return_correlation_insufficient_sample_is_none():
    rows_a = _daily_rows(5)
    rows_b = _daily_rows(5)
    assert metrics.pairwise_return_correlation(rows_a, rows_b, min_sample=20) is None


def test_pairwise_return_correlation_perfectly_correlated_series():
    rows_a = _daily_rows(25)
    rows_b = [dict(r, close=r["close"] * 2) for r in rows_a]   # identical returns, scaled
    corr = metrics.pairwise_return_correlation(rows_a, rows_b, min_sample=20)
    assert corr == pytest.approx(1.0, abs=1e-6)

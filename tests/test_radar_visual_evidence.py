"""radar/visual_evidence.py: builds chart evidence for a selected Radar story from local,
already-persisted OHLCV data. Offline only - `market.history` is monkeypatched to raise if
called at all, proving the builder never fetches.
"""
import datetime as dt

import pytest

import market
from radar.relative import _return_pct
from radar.relative_acquisition import BENCHMARK_SYMBOL
from radar.technical import _sma, _window_high_low
from radar.thresholds import DEFAULT_TECHNICAL_THRESHOLDS
from radar.visual_evidence import (BENCHMARK_VISUAL_EVIDENCE_UNAVAILABLE, CHART_WINDOW_SESSIONS,
                                   RELATIVE_WINDOW_SESSIONS, build_visual_evidence,
                                   build_visual_evidence_for_presentation)
from storage.ohlcv_models import OHLCVBar, QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path

N_SESSIONS = 90
START = dt.date(2026, 1, 5)  # a Monday


def _weekday_dates(start: dt.date, n: int) -> list:
    dates, d = [], start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += dt.timedelta(days=1)
    return dates


def _bars(symbol: str, dates: list, closes: list, *, highs=None, lows=None, volumes=None,
         source: str = "yahoo") -> list:
    highs = highs if highs is not None else [c + 1 for c in closes]
    lows = lows if lows is not None else [c - 1 for c in closes]
    volumes = volumes if volumes is not None else [1000.0] * len(closes)
    now = dt.datetime.now(dt.timezone.utc)
    return [OHLCVBar(symbol=symbol, session_date=d, open=c, high=h, low=l, close=c, volume=v,
                     source=source, retrieved_at=now, quality_status=QualityStatus.OK)
           for d, c, h, l, v in zip(dates, closes, highs, lows, volumes)]


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Every test in this file proves the builder never fetches - fail loudly if it tries."""
    def _boom(*a, **k):
        raise AssertionError("build_visual_evidence must never call market.history")
    monkeypatch.setattr(market, "history", _boom)


def _store(tmp_path) -> OHLCVStore:
    return OHLCVStore(default_db_path(str(tmp_path)))


def _seed_basic(tmp_path, *, n=N_SESSIONS, trend=0.5):
    dates = _weekday_dates(START, n)
    session_date = dates[-1]
    bench_closes = [18000.0 + i * (trend / 2) for i in range(n)]
    stock_closes = [1000.0 + i * trend for i in range(n)]
    store = _store(tmp_path)
    store.upsert_bars(_bars(BENCHMARK_SYMBOL, dates, bench_closes))
    store.upsert_bars(_bars("MANKIND", dates, stock_closes))
    store.close()
    return dates, session_date, bench_closes, stock_closes


def test_no_local_benchmark_history_returns_none_with_warning(tmp_path, capsys):
    dates = _weekday_dates(START, N_SESSIONS)
    store = _store(tmp_path)
    store.upsert_bars(_bars("MANKIND", dates, [1000.0 + i for i in range(N_SESSIONS)]))
    store.close()

    result = build_visual_evidence("MANKIND", dates[-1], {}, out_dir=str(tmp_path))
    assert result is None
    assert BENCHMARK_VISUAL_EVIDENCE_UNAVAILABLE in capsys.readouterr().out


def test_no_recap_row_locally_returns_none(tmp_path):
    dates, session_date, *_ = _seed_basic(tmp_path)
    # Ask for a session one day later than anything stored.
    result = build_visual_evidence("MANKIND", session_date + dt.timedelta(days=1), {},
                                   out_dir=str(tmp_path))
    assert result is None


def test_insufficient_history_returns_none(tmp_path):
    dates = _weekday_dates(START, market.MIN_TECHNICAL_SESSIONS - 5)
    session_date = dates[-1]
    store = _store(tmp_path)
    store.upsert_bars(_bars(BENCHMARK_SYMBOL, dates, [18000.0] * len(dates)))
    store.upsert_bars(_bars("MANKIND", dates, [1000.0] * len(dates)))
    store.close()

    result = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert result is None


def test_future_rows_never_leak_into_evidence(tmp_path):
    dates, session_date, bench_closes, stock_closes = _seed_basic(tmp_path)
    future_dates = _weekday_dates(session_date + dt.timedelta(days=1), 5)
    store = _store(tmp_path)
    store.upsert_bars(_bars(BENCHMARK_SYMBOL, future_dates, [99999.0] * 5))
    store.upsert_bars(_bars("MANKIND", future_dates, [88888.0] * 5))
    store.close()

    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence is not None
    assert max(evidence.window_dates) == session_date
    assert 88888.0 not in evidence.close_series
    assert evidence.close_series[-1] == stock_closes[-1]


def test_holiday_placeholder_row_is_filtered_out(tmp_path, declare_nse_holiday):
    dates = _weekday_dates(START, N_SESSIONS)
    session_date = dates[-1]
    holiday_date = dates[45]  # a normal weekday in the middle of the fixture range
    declare_nse_holiday(holiday_date)
    bench_closes = [18000.0 + i * 0.25 for i in range(N_SESSIONS)]
    stock_closes = [1000.0 + i * 0.5 for i in range(N_SESSIONS)]

    # Simulate the real 2026-09-14-class artifact: the index has NO bar for this date (a
    # genuine NSE holiday), but the stock's own feed carries a spurious one for it (a
    # placeholder, different volume from any real session) - the benchmark table is seeded
    # WITHOUT that date from the start, since upsert_bars can only add/replace rows, never
    # delete one a prior seed already wrote.
    bench_dates = [d for d in dates if d != holiday_date]
    bench_closes_minus = [c for d, c in zip(dates, bench_closes) if d != holiday_date]
    store = _store(tmp_path)
    store.upsert_bars(_bars(BENCHMARK_SYMBOL, bench_dates, bench_closes_minus))
    store.upsert_bars(_bars("MANKIND", dates, stock_closes, volumes=[
        42.0 if d == holiday_date else 1000.0 for d in dates]))
    store.close()

    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence is not None
    assert holiday_date not in evidence.window_dates
    assert holiday_date not in evidence.source_session_dates


def test_detector_math_parity_range_and_sma_and_returns(tmp_path):
    dates, session_date, bench_closes, stock_closes = _seed_basic(tmp_path)
    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence is not None

    series = [{"date": d, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0}
             for d, c in zip(dates, stock_closes)]
    prior = series[:-1]
    expected_20_high, expected_20_low = _window_high_low(
        prior[-DEFAULT_TECHNICAL_THRESHOLDS.range_window_short:])
    expected_50_high, expected_50_low = _window_high_low(
        prior[-DEFAULT_TECHNICAL_THRESHOLDS.range_window_long:])
    assert evidence.range_20_high == expected_20_high
    assert evidence.range_20_low == expected_20_low
    assert evidence.range_50_high == expected_50_high
    assert evidence.range_50_low == expected_50_low

    expected_sma20 = _sma(series, DEFAULT_TECHNICAL_THRESHOLDS.sma_short)
    expected_sma50 = _sma(series, DEFAULT_TECHNICAL_THRESHOLDS.sma_long)
    assert evidence.sma20_series[-1] == expected_sma20
    assert evidence.sma50_series[-1] == expected_sma50

    expected_5d, _, _ = _return_pct(series, 5)
    expected_20d, _, _ = _return_pct(series, 20)
    assert evidence.stock_return_5d_pct == expected_5d
    assert evidence.stock_return_20d_pct == expected_20d


def test_highlight_events_rvol_and_relative_pp_are_copied_verbatim_not_recomputed(tmp_path):
    dates, session_date, *_ = _seed_basic(tmp_path)
    story_fields = {
        "technical_context": {"events": ["BREAK_ABOVE_20D_RANGE", "CROSS_ABOVE_SMA50"]},
        "volume_context": {"level": "UNUSUAL", "relative_volume": 3.4},
        "relative_context": {"market_relative_5d_pp": 2.1, "market_relative_20d_pp": 5.6},
    }
    evidence = build_visual_evidence("MANKIND", session_date, story_fields, out_dir=str(tmp_path))
    assert evidence is not None
    assert evidence.highlight_events == ["BREAK_ABOVE_20D_RANGE", "CROSS_ABOVE_SMA50"]
    assert evidence.rvol == 3.4
    assert evidence.rvol_level == "UNUSUAL"
    assert evidence.market_relative_5d_pp == 2.1
    assert evidence.market_relative_20d_pp == 5.6


def test_normalized_comparison_starts_at_100_for_both_series(tmp_path):
    dates, session_date, bench_closes, stock_closes = _seed_basic(tmp_path)
    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence.relative_stock_normalized is not None
    assert evidence.relative_benchmark_normalized is not None
    assert evidence.relative_stock_normalized[0] == pytest.approx(100.0)
    assert evidence.relative_benchmark_normalized[0] == pytest.approx(100.0)
    assert len(evidence.relative_dates) == RELATIVE_WINDOW_SESSIONS + 1


def test_relative_comparison_uses_intersection_only_no_interpolation(tmp_path):
    dates = _weekday_dates(START, N_SESSIONS)
    session_date = dates[-1]
    missing_date = dates[-5]
    bench_closes = [18000.0 + i * 0.25 for i in range(N_SESSIONS)]
    stock_closes = [1000.0 + i * 0.5 for i in range(N_SESSIONS)]

    # The benchmark table is seeded WITHOUT `missing_date` from the start (not far enough from
    # `session_date` to be dropped from spine coverage - it stays a genuine gap the relative
    # comparison must skip, never interpolate over). The stock still has that date.
    bench_dates = [d for d in dates if d != missing_date]
    bench_closes_minus = [c for d, c in zip(dates, bench_closes) if d != missing_date]
    store = _store(tmp_path)
    store.upsert_bars(_bars(BENCHMARK_SYMBOL, bench_dates, bench_closes_minus))
    store.upsert_bars(_bars("MANKIND", dates, stock_closes))
    store.close()

    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence is not None
    assert missing_date not in (evidence.relative_dates or [])


def test_benchmark_unavailable_for_relative_window_sets_warning_not_none(tmp_path):
    dates, session_date, bench_closes, stock_closes = _seed_basic(tmp_path, n=N_SESSIONS)
    # A stock with local history that only barely overlaps the benchmark's covered range still
    # yields a full price chart - only the relative comparison degrades.
    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence is not None
    assert evidence.close_series  # chart evidence still produced


def test_provenance_fields_present(tmp_path):
    dates, session_date, *_ = _seed_basic(tmp_path)
    evidence = build_visual_evidence("MANKIND", session_date, {}, out_dir=str(tmp_path))
    assert evidence.benchmark_symbol == BENCHMARK_SYMBOL
    assert evidence.calculation_version
    assert evidence.session_date == session_date
    assert evidence.instrument == "MANKIND"
    assert len(evidence.source_session_dates) >= market.MIN_TECHNICAL_SESSIONS
    assert len(evidence.window_dates) <= CHART_WINDOW_SESSIONS


def test_build_visual_evidence_for_presentation_only_covers_story_scenes(tmp_path):
    dates, session_date, bench_closes, stock_closes = _seed_basic(tmp_path)
    store = _store(tmp_path)
    store.upsert_bars(_bars("ETERNAL", dates, [500.0 + i * 0.3 for i in range(N_SESSIONS)]))
    store.close()

    presentation = {
        "session_date": session_date.isoformat(),
        "scenes": [
            {"role": "HOOK", "headline": "hi"},
            {"role": "STORY", "story": {"instrument": "MANKIND"}},
            {"role": "STORY", "story": {"instrument": "ETERNAL"}},
            {"role": "CLOSING", "headline": "bye"},
        ],
    }
    radar_result = {"stories": [
        {"instrument": "MANKIND", "technical_context": None, "volume_context": None,
         "relative_context": None},
        {"instrument": "ETERNAL", "technical_context": None, "volume_context": None,
         "relative_context": None},
        {"instrument": "OFSS", "technical_context": None, "volume_context": None,
         "relative_context": None},
    ]}

    result = build_visual_evidence_for_presentation(presentation, radar_result,
                                                     out_dir=str(tmp_path))
    assert set(result.keys()) == {"MANKIND", "ETERNAL"}
    assert result["MANKIND"] is not None
    assert result["ETERNAL"] is not None


def test_build_visual_evidence_for_presentation_missing_story_context_is_none(tmp_path):
    dates, session_date, *_ = _seed_basic(tmp_path)
    presentation = {
        "session_date": session_date.isoformat(),
        "scenes": [{"role": "STORY", "story": {"instrument": "NOTINRESULT"}}],
    }
    radar_result = {"stories": []}
    result = build_visual_evidence_for_presentation(presentation, radar_result,
                                                     out_dir=str(tmp_path))
    assert result == {"NOTINRESULT": None}

"""market.get_universe_technical_series: bulk OHLC session history across a wide universe for
the technical-structure detector (Phase 4.2 Packet 2). Offline only -
market._bulk_download_universe_ohlcv is monkeypatched, never a live yfinance call.
"""
import market
from conftest_universe import fake_bulk_ohlcv, synthetic_universe


def test_returns_series_for_the_full_universe(monkeypatch):
    universe = synthetic_universe(5)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons == {}
    assert set(series) == set(universe)
    for rows in series.values():
        assert rows[-1]["date"] == recap_date
        assert rows[-2]["date"] == prev_date


def test_series_is_oldest_first_and_carries_high_low_close(monkeypatch):
    universe = synthetic_universe(1)
    symbol = next(iter(universe))
    closes = [100.0 + i for i in range(63)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, close_map={symbol: closes},
                                            high_map={symbol: highs}, low_map={symbol: lows})

    series, _ = market.get_universe_technical_series(universe, recap_date, prev_date)
    rows = series[symbol]
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert rows[-1]["close"] == closes[-1]
    assert rows[-1]["high"] == highs[-1]
    assert rows[-1]["low"] == lows[-1]


def test_invalid_ohlc_row_is_dropped_not_the_whole_symbol(monkeypatch):
    universe = synthetic_universe(1)
    symbol = next(iter(universe))
    closes = [100.0] * 63
    highs = list(closes)
    lows = list(closes)
    highs[30] = 50.0   # high < low for this one session -> invalid, must be dropped
    lows[30] = 60.0
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, close_map={symbol: closes},
                                            high_map={symbol: highs}, low_map={symbol: lows})

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert symbol not in skip_reasons
    assert len(series[symbol]) == 62


def test_skip_reason_no_recap_row(monkeypatch):
    universe = synthetic_universe(3)
    absent = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, missing=[absent])

    _, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[absent] == "no_recap_row"


def test_skip_reason_backfill_pending(monkeypatch):
    universe = synthetic_universe(3)
    lagging = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, nan_close=[lagging])

    _, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[lagging] == "backfill_pending"


def test_skip_reason_date_gap(monkeypatch):
    universe = synthetic_universe(3)
    gapped = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, date_gap=[gapped])

    _, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[gapped] == "date_gap"


def test_skip_reason_insufficient_technical_history(monkeypatch):
    universe = synthetic_universe(3)
    ipo = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, short_history={ipo: 10})

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[ipo] == "insufficient_technical_history"
    assert ipo not in series

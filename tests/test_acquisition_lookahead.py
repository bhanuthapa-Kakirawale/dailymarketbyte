"""Regression tests for Phase 4.2 Packet 5.3B: recap-date acceptance must be "does a valid row
exist for recap_date", never "is recap_date the LAST row" - and any session dated AFTER
recap_date must never influence a calculation FOR recap_date (look-ahead prevention).

Offline only - `market._bulk_download_universe_ohlcv` is monkeypatched via
`conftest_universe.fake_bulk_ohlcv`, never a live yfinance call.
"""
import market
import radar.ohlcv_service as ohlcv_service
import radar.relative as relative
import radar.technical as technical
from conftest_universe import fake_bulk_ohlcv, synthetic_universe


# --------------------------------------------------------------------------- Case A/B/C/D
def test_case_a_recap_is_last_row_accepted(monkeypatch):
    universe = synthetic_universe(2)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons == {}
    assert set(series) == set(universe)
    for rows in series.values():
        assert rows[-1]["date"] == recap_date


def test_case_b_later_rows_exist_recap_still_accepted(monkeypatch):
    universe = synthetic_universe(2)
    leader = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            future_sessions={leader: 2})

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert leader not in skip_reasons, "a later session must not invalidate a valid recap row"
    assert skip_reasons == {}
    assert series[leader][-1]["date"] == recap_date  # never the future row


def test_case_b_rvol_also_accepts_recap_with_later_rows(monkeypatch):
    universe = synthetic_universe(2)
    leader = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            future_sessions={leader: 2})

    rows, skip_reasons = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert leader not in skip_reasons
    assert {r["symbol"] for r in rows} == set(universe)


def test_case_c_recap_missing_rejected_no_recap_row(monkeypatch):
    universe = synthetic_universe(3)
    absent = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, missing=[absent])

    _, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[absent] == "no_recap_row"


def test_case_d_recap_close_nan_rejected_backfill_pending(monkeypatch):
    universe = synthetic_universe(3)
    lagging = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, nan_close=[lagging])

    _, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[lagging] == "backfill_pending"


def test_case_d_recap_close_nan_with_later_rows_still_backfill_pending(monkeypatch):
    """The false-BACKFILL_PENDING bug's mirror image: a symbol whose recap-date Close is
    GENUINELY NaN must still be correctly rejected as backfill_pending even when later
    (post-recap) sessions also exist in the same fetch - the fix must not overcorrect into
    accepting a truly-incomplete recap row just because future data happens to be present."""
    universe = synthetic_universe(2)
    lagging = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, nan_close=[lagging],
                                            future_sessions={lagging: 2})

    _, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons[lagging] == "backfill_pending"


# --------------------------------------------------------------------------- Case E: look-ahead
def test_case_e_rvol_identical_with_and_without_future_rows(monkeypatch):
    universe = synthetic_universe(1)
    symbol = next(iter(universe))
    volume_map = {symbol: [1_000_000.0 + i * 137 for i in range(63)]}

    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            volume_map=volume_map)
    rows_clean, _ = market.get_universe_relative_volume(universe, recap_date, prev_date)

    recap_date2, prev_date2 = fake_bulk_ohlcv(
        monkeypatch, universe, sessions=63, volume_map=volume_map,
        future_sessions={symbol: 3},
        future_volume_map={symbol: [999_999_999.0, 1.0, 500_000_000.0]})
    assert (recap_date2, prev_date2) == (recap_date, prev_date)
    rows_contaminated, _ = market.get_universe_relative_volume(universe, recap_date, prev_date)

    assert rows_clean == rows_contaminated


def test_case_e_technical_identical_with_and_without_future_rows(monkeypatch):
    universe = synthetic_universe(1)
    symbol = next(iter(universe))
    closes = {symbol: [100.0 + i * 0.5 for i in range(63)]}

    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63, close_map=closes)
    series_clean, skip_clean = market.get_universe_technical_series(universe, recap_date, prev_date)
    snapshot_clean = technical.scan_technical_universe(
        list(universe), series_clean, recap_date, skip_reasons=skip_clean)

    # Future rows deliberately engineered to trip a BREAK_ABOVE_20D_RANGE / SMA cross / range
    # compression if they were to leak into recap-date's own calculation.
    recap_date2, prev_date2 = fake_bulk_ohlcv(
        monkeypatch, universe, sessions=63, close_map=closes,
        future_sessions={symbol: 5},
        future_close_map={symbol: [500.0, 600.0, 50.0, 700.0, 10.0]})
    assert (recap_date2, prev_date2) == (recap_date, prev_date)
    series_future, skip_future = market.get_universe_technical_series(universe, recap_date, prev_date)
    snapshot_future = technical.scan_technical_universe(
        list(universe), series_future, recap_date, skip_reasons=skip_future)

    assert series_clean == series_future
    struct_clean = snapshot_clean.flagged[0] if snapshot_clean.flagged else None
    struct_future = snapshot_future.flagged[0] if snapshot_future.flagged else None
    assert (struct_clean is None) == (struct_future is None)
    if struct_clean is not None:
        assert [e.event_type for e in struct_clean.events] == [e.event_type for e in struct_future.events]
        assert struct_clean.sma20 == struct_future.sma20
        assert struct_clean.sma50 == struct_future.sma50
        assert struct_clean.prior_20_high == struct_future.prior_20_high


def test_case_e_relative_identical_with_and_without_future_rows(monkeypatch):
    universe = synthetic_universe(1)
    symbol = next(iter(universe))
    closes = {symbol: [100.0 + i * 0.3 for i in range(63)]}

    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63, close_map=closes)
    series_clean, skip_clean = market.get_universe_technical_series(universe, recap_date, prev_date)
    benchmark = [{"date": row["date"], "close": 20000.0 + i}
                for i, row in enumerate(series_clean[symbol])]
    snapshot_clean = relative.scan_relative_performance_universe(
        list(universe), series_clean, benchmark, recap_date, skip_reasons=skip_clean)

    recap_date2, prev_date2 = fake_bulk_ohlcv(
        monkeypatch, universe, sessions=63, close_map=closes,
        future_sessions={symbol: 4}, future_close_map={symbol: [5000.0, 1.0, 9000.0, 2.0]})
    assert (recap_date2, prev_date2) == (recap_date, prev_date)
    series_future, skip_future = market.get_universe_technical_series(universe, recap_date, prev_date)
    snapshot_future = relative.scan_relative_performance_universe(
        list(universe), series_future, benchmark, recap_date, skip_reasons=skip_future)

    r_clean = snapshot_clean.results[0] if snapshot_clean.results else None
    r_future = snapshot_future.results[0] if snapshot_future.results else None
    assert (r_clean is None) == (r_future is None)
    if r_clean is not None:
        assert r_clean.stock_return_1d == r_future.stock_return_1d
        assert r_clean.stock_return_5d == r_future.stock_return_5d
        assert r_clean.stock_return_20d == r_future.stock_return_20d
        assert r_clean.market_relative_1d_pp == r_future.market_relative_1d_pp
        assert r_clean.market_relative_20d_pp == r_future.market_relative_20d_pp


def test_case_e_get_movers_identical_with_and_without_future_rows(monkeypatch):
    """`get_movers` (the actual publication top-movers path) shares the exact same
    last-row-acceptance pattern and is fixed the same way - a look-ahead bug here would
    corrupt the video's own gainers/losers, not just the Radar.

    `get_movers` calls `yf.download` directly rather than through the mockable
    `market._bulk_download_universe_ohlcv` seam `fake_bulk_ohlcv` patches, so this test
    captures the frame `fake_bulk_ohlcv` builds and feeds it to `get_movers` via a direct
    `yfinance.download` monkeypatch instead - no live network call either way.
    """
    import yfinance as yf

    universe = synthetic_universe(12)
    leader = next(iter(universe))
    volume_map = {s: [1_000_000.0] * 63 for s in universe}
    closes = {leader: [100.0 + i * 0.2 for i in range(63)]}

    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            volume_map=volume_map, close_map=closes)
    raw_clean = market._bulk_download_universe_ohlcv(list(universe))
    monkeypatch.setattr(yf, "download", lambda *a, **k: raw_clean)
    gainers_clean, losers_clean = market.get_movers(universe, recap_date, prev_date, n=5)

    recap_date2, prev_date2 = fake_bulk_ohlcv(
        monkeypatch, universe, sessions=63, volume_map=volume_map, close_map=closes,
        future_sessions={leader: 2}, future_close_map={leader: [9999.0, 1.0]})
    assert (recap_date2, prev_date2) == (recap_date, prev_date)
    raw_future = market._bulk_download_universe_ohlcv(list(universe))
    monkeypatch.setattr(yf, "download", lambda *a, **k: raw_future)
    gainers_future, losers_future = market.get_movers(universe, recap_date, prev_date, n=5)

    assert gainers_clean == gainers_future
    assert losers_clean == losers_future


# --------------------------------------------------------------------------- historical-run invariant
def test_all_series_dates_bounded_by_recap_date(monkeypatch):
    universe = synthetic_universe(3)
    leader = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            future_sessions={leader: 3})

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    for symbol, rows in series.items():
        assert all(r["date"] <= recap_date for r in rows), \
            f"{symbol}: a session dated after recap_date leaked into the series"

    rows, _ = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert {r["symbol"] for r in rows}.issubset(set(universe))  # sanity: still runs cleanly


def test_shared_ohlcv_service_dataset_never_leaks_future_sessions(monkeypatch):
    universe = synthetic_universe(3)
    leader = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            future_sessions={leader: 3})

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    for symbol, rows in dataset.series_by_symbol.items():
        assert rows[-1]["date"] == recap_date
        assert all(r["date"] <= recap_date for r in rows)

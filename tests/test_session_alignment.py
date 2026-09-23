"""Canonical trading-session alignment (Phase 4.2 Packet 5.3C). Offline only - no network, no
provider. Builds synthetic series/benchmarks directly (no live yfinance call anywhere).
"""
import datetime as dt

import market
import radar.ohlcv_service as ohlcv_service
import radar.relative as relative
import radar.session_alignment as session_alignment
import radar.technical as technical

RECAP = dt.date(2026, 9, 21)


def _row(d, close, high=None, low=None, volume=1_000_000.0, open_=None):
    return {"date": d, "open": open_ if open_ is not None else close,
           "high": high if high is not None else close, "low": low if low is not None else close,
           "close": close, "volume": volume}


def _weekday_session_dates(n: int, end: dt.date = RECAP) -> list:
    """`n` business-day dates ending at `end`, oldest first - a simple, dependency-free stand-in
    for a real NSE trading calendar (no weekends; the 09-14 holiday is inserted/removed
    explicitly per test)."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def _weekday_range_with_holiday(n_total: int, holiday_index: int, end: dt.date = RECAP):
    """`n_total` consecutive business-day dates ending at `end` (`full_dates`), with the one at
    `holiday_index` singled out as `holiday_date` - a mid-window weekday that a real stock's
    Yahoo series carries a row for but the canonical spine (`spine_dates`, `full_dates` minus
    `holiday_date`) correctly excludes. Mirrors the real 2026-09-14 case exactly: a weekday,
    strictly inside the covered date range, that simply never traded.
    """
    full_dates = _weekday_session_dates(n_total, end=end)
    holiday_date = full_dates[holiday_index]
    spine_dates = [d for d in full_dates if d != holiday_date]
    return full_dates, holiday_date, spine_dates


def _benchmark_series(dates: list, base: float = 20000.0) -> list:
    return [{"date": d, "close": base + i} for i, d in enumerate(dates)]


# --------------------------------------------------------------------------- pure alignment
def test_holiday_placeholder_excluded():
    dates = [dt.date(2026, 9, 10), dt.date(2026, 9, 11), dt.date(2026, 9, 14), dt.date(2026, 9, 15)]
    stock_series = [_row(d, 100.0 + i) for i, d in enumerate(dates)]
    spine_dates = [d for d in dates if d != dt.date(2026, 9, 14)]
    spine = session_alignment.canonical_session_spine(_benchmark_series(spine_dates))

    aligned, dropped = session_alignment.align_series_to_spine(stock_series, spine)
    assert dropped == 1
    assert [r["date"] for r in aligned] == spine_dates


def test_genuine_trading_session_retained():
    dates = _weekday_session_dates(10)
    stock_series = [_row(d, 100.0 + i) for i, d in enumerate(dates)]
    spine = session_alignment.canonical_session_spine(_benchmark_series(dates))

    aligned, dropped = session_alignment.align_series_to_spine(stock_series, spine)
    assert dropped == 0
    assert [r["date"] for r in aligned] == dates


def test_missing_genuine_session_remains_missing_not_interpolated():
    """A date the spine says SHOULD exist but the stock genuinely lacks must stay missing - no
    row is ever fabricated to fill the gap."""
    dates = _weekday_session_dates(10)
    missing_date = dates[5]
    stock_series = [_row(d, 100.0 + i) for i, d in enumerate(dates) if d != missing_date]
    spine = session_alignment.canonical_session_spine(_benchmark_series(dates))

    aligned, dropped = session_alignment.align_series_to_spine(stock_series, spine)
    assert dropped == 0  # nothing to drop - the stock simply never had this row
    assert missing_date not in {r["date"] for r in aligned}
    assert len(aligned) == len(stock_series)  # unchanged - no row invented


def test_row_outside_spine_coverage_is_kept_not_dropped():
    """A date the benchmark's own window never covered is UNKNOWN, not proven invalid."""
    spine_dates = _weekday_session_dates(5, end=RECAP)
    spine = session_alignment.canonical_session_spine(_benchmark_series(spine_dates))

    earlier_unknown_date = spine_dates[0] - dt.timedelta(days=30)
    stock_series = [_row(earlier_unknown_date, 50.0)] + [_row(d, 100.0 + i)
                                                         for i, d in enumerate(spine_dates)]
    aligned, dropped = session_alignment.align_series_to_spine(stock_series, spine)
    assert dropped == 0
    assert earlier_unknown_date in {r["date"] for r in aligned}


def test_empty_spine_filters_nothing():
    dates = _weekday_session_dates(5)
    stock_series = [_row(d, 100.0) for d in dates]
    aligned, dropped = session_alignment.align_series_to_spine(stock_series, frozenset())
    assert dropped == 0
    assert aligned == stock_series


# --------------------------------------------------------------------------- dataset-level
class _FakeDataset:
    def __init__(self, session_date, series_by_symbol):
        self.session_date = session_date
        self.series_by_symbol = series_by_symbol
        self.skipped_symbols = {}
        self.available_symbols = []
        self.warnings = []


def test_align_dataset_removes_holiday_row_and_updates_series():
    full_dates, holiday_date, spine_dates = _weekday_range_with_holiday(
        market.MIN_TECHNICAL_SESSIONS + 5, holiday_index=15)
    stock_series = [_row(d, 100.0 + i) for i, d in enumerate(full_dates)]
    dataset = _FakeDataset(full_dates[-1], {"AAA": stock_series})

    report = session_alignment.align_dataset_to_spine(dataset, _benchmark_series(spine_dates))
    assert report.symbols_filtered == 1
    assert report.rows_dropped == 1
    assert holiday_date not in {r["date"] for r in dataset.series_by_symbol["AAA"]}
    assert [r["date"] for r in dataset.series_by_symbol["AAA"]] == spine_dates
    assert dataset.skipped_symbols == {}


def test_align_dataset_skips_symbol_that_becomes_too_short():
    dates = _weekday_session_dates(market.MIN_TECHNICAL_SESSIONS)
    holiday = dates[10]
    contaminated = dates + [holiday]  # duplicate-style contamination pushes nothing extra in,
    # so instead simulate a symbol whose only "extra" sessions ARE holiday placeholders:
    series = [_row(d, 100.0) for d in dates[:market.MIN_TECHNICAL_SESSIONS - 3]]
    series.append(_row(holiday, 100.0))  # a holiday placeholder padding out an otherwise-thin series
    series = sorted(series, key=lambda r: r["date"])
    spine_dates = [d for d in dates if d != holiday]
    dataset = _FakeDataset(dates[-1], {"THIN": series})

    report = session_alignment.align_dataset_to_spine(dataset, _benchmark_series(spine_dates))
    assert "THIN" in dataset.skipped_symbols
    assert "THIN" not in dataset.series_by_symbol
    assert report.newly_skipped["THIN"] in ("insufficient_technical_history", "no_recap_row")


def test_align_dataset_no_benchmark_leaves_series_untouched_with_warning():
    dates = _weekday_session_dates(5)
    series = {"AAA": [_row(d, 100.0) for d in dates]}
    dataset = _FakeDataset(dates[-1], dict(series))

    report = session_alignment.align_dataset_to_spine(dataset, [])
    assert report.spine_available is False
    assert dataset.series_by_symbol == series
    assert dataset.warnings


# --------------------------------------------------------------------------- RVOL / technical exclude holiday
def test_rvol_prior_20_excludes_holiday():
    import pandas as pd

    full_dates, holiday_date, spine_dates = _weekday_range_with_holiday(22, holiday_index=10)
    volumes_by_date = {d: 1_000_000.0 + i * 1000 for i, d in enumerate(spine_dates)}
    volumes_by_date[holiday_date] = 999_999_999.0  # deliberately extreme placeholder volume

    contaminated_rows = [_row(d, 100.0, volume=volumes_by_date[d]) for d in full_dates]
    clean_rows = [_row(d, 100.0, volume=volumes_by_date[d]) for d in spine_dates]

    spine = session_alignment.canonical_session_spine(_benchmark_series(spine_dates))
    aligned, dropped = session_alignment.align_series_to_spine(contaminated_rows, spine)
    assert dropped == 1
    assert aligned == clean_rows

    rvol_contaminated = market.relative_volume(
        pd.Series([r["volume"] for r in contaminated_rows]), lookback=20)
    rvol_aligned = market.relative_volume(pd.Series([r["volume"] for r in aligned]), lookback=20)
    rvol_clean = market.relative_volume(pd.Series([r["volume"] for r in clean_rows]), lookback=20)
    assert rvol_contaminated != rvol_clean  # sanity: contamination DOES change the raw result
    assert rvol_aligned == rvol_clean


def test_sma20_and_range_exclude_holiday():
    full_dates, holiday_date, spine_dates = _weekday_range_with_holiday(61, holiday_index=30)
    closes_by_date = {d: 100.0 + i * 0.1 for i, d in enumerate(spine_dates)}
    closes_by_date[holiday_date] = 9999.0  # deliberately extreme placeholder close

    contaminated_series = [_row(d, closes_by_date[d]) for d in full_dates]
    clean_series = [_row(d, closes_by_date[d]) for d in spine_dates]

    spine = session_alignment.canonical_session_spine(_benchmark_series(spine_dates))
    aligned, dropped = session_alignment.align_series_to_spine(contaminated_series, spine)
    assert dropped == 1
    assert aligned == clean_series

    struct_clean = technical.scan_technical_universe(
        ["AAA"], {"AAA": clean_series}, spine_dates[-1]).flagged
    struct_aligned = technical.scan_technical_universe(
        ["AAA"], {"AAA": aligned}, spine_dates[-1]).flagged
    assert [(s.instrument, [e.event_type for e in s.events]) for s in struct_clean] == \
        [(s.instrument, [e.event_type for e in s.events]) for s in struct_aligned]
    # direct SMA20 comparison, unaffected by whether anything crossed
    sma_clean = sum(r["close"] for r in clean_series[-20:]) / 20
    assert sma_clean == sum(r["close"] for r in aligned[-20:]) / 20


def test_compression_window_excludes_holiday():
    full_dates, holiday_date, spine_dates = _weekday_range_with_holiday(61, holiday_index=45)
    closes_by_date = {d: 100.0 + (i % 3) * 0.2 for i, d in enumerate(spine_dates)}
    closes_by_date[holiday_date] = 100.0  # placeholder close, but with a deliberately wide range

    def make_row(d):
        c = closes_by_date[d]
        if d == holiday_date:
            return _row(d, c, high=500.0, low=1.0)
        return _row(d, c, high=c + 0.1, low=c - 0.1)

    contaminated_series = [make_row(d) for d in full_dates]
    clean_series = [make_row(d) for d in spine_dates]

    spine = session_alignment.canonical_session_spine(_benchmark_series(spine_dates))
    aligned, dropped = session_alignment.align_series_to_spine(contaminated_series, spine)
    assert dropped == 1
    assert aligned == clean_series


# --------------------------------------------------------------------------- relative alignment
def test_relative_1d_5d_20d_alignment_restores_benchmark_matches():
    full_dates, holiday_date, spine_dates = _weekday_range_with_holiday(26, holiday_index=19)
    closes_by_date = {d: 100.0 + i * 0.4 for i, d in enumerate(spine_dates)}
    closes_by_date[holiday_date] = closes_by_date[spine_dates[18]]  # same close as the prior real session

    contaminated_series = [_row(d, closes_by_date[d]) for d in full_dates]
    clean_series = [_row(d, closes_by_date[d]) for d in spine_dates]
    session_date = spine_dates[-1]

    benchmark = _benchmark_series(spine_dates)  # the benchmark NEVER has the holiday date
    spine = session_alignment.canonical_session_spine(benchmark)
    aligned, dropped = session_alignment.align_series_to_spine(contaminated_series, spine)
    assert dropped == 1
    assert aligned == clean_series

    snap_contaminated = relative.scan_relative_performance_universe(
        ["AAA"], {"AAA": contaminated_series}, benchmark, session_date)
    snap_aligned = relative.scan_relative_performance_universe(
        ["AAA"], {"AAA": aligned}, benchmark, session_date)

    r_contaminated = snap_contaminated.results[0]
    r_aligned = snap_aligned.results[0]

    # Before alignment, the holiday row shifts the 20D window's "20 sessions ago" date to one
    # the benchmark never has - after alignment it lands back on a real, matched date.
    assert r_aligned.market_relative_1d_pp is not None
    assert r_aligned.market_relative_5d_pp is not None
    assert r_aligned.market_relative_20d_pp is not None
    assert r_contaminated.market_relative_20d_pp != r_aligned.market_relative_20d_pp \
        or r_contaminated.market_relative_20d_pp is None


# --------------------------------------------------------------------------- Packet 5.3B still holds
def test_future_row_protection_still_holds_after_alignment():
    dates = _weekday_session_dates(30, end=RECAP)
    series = [_row(d, 100.0) for d in dates]
    future = [_row(RECAP + dt.timedelta(days=1), 99999.0),
             _row(RECAP + dt.timedelta(days=2), 1.0)]
    contaminated = series + future  # future rows, not holiday rows

    spine = session_alignment.canonical_session_spine(_benchmark_series(dates))
    aligned, dropped = session_alignment.align_series_to_spine(contaminated, spine)
    # future dates fall OUTSIDE the spine's covered range -> kept by session_alignment alone;
    # look-ahead protection is `market.py`'s own recap-date truncation (Packet 5.3B), which
    # ohlcv_service always applies before session_alignment ever sees a series (see the
    # companion test below, which exercises the real pipeline path).
    assert dropped == 0
    assert any(r["date"] > RECAP for r in aligned)


def test_ohlcv_service_dataset_has_no_future_rows_for_alignment_to_see(monkeypatch):
    from conftest_universe import fake_bulk_ohlcv, synthetic_universe

    universe = synthetic_universe(2)
    leader = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            future_sessions={leader: 3})

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    for symbol, rows in dataset.series_by_symbol.items():
        assert all(r["date"] <= recap_date for r in rows)

    benchmark = _benchmark_series([r["date"] for r in dataset.series_by_symbol[leader]])
    report = session_alignment.align_dataset_to_spine(dataset, benchmark)
    for rows in dataset.series_by_symbol.values():
        assert all(r["date"] <= recap_date for r in rows)

"""Shared read-through OHLCV acquisition (Phase 4.2 Packet 5.3). Offline only -
`market._bulk_download_universe_ohlcv` is monkeypatched (via `conftest_universe.fake_bulk_ohlcv`
or a local raising stub), never a live yfinance call. `config.OUT_DIR` is isolated per test by
the autouse `_isolate_config_out_dir` fixture in `tests/conftest.py`.
"""
import datetime as dt
import time

import pandas as pd
import pytest

import market
import radar.ohlcv_service as ohlcv_service
import radar.relative as relative
import radar.technical as technical
from conftest_universe import RECAP_DATE as RECAP
from conftest_universe import fake_bulk_ohlcv, synthetic_universe, universe_sessions
from storage.ohlcv_models import OHLCVBar, QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path


def _seed_ok(store: OHLCVStore, symbols, sessions_index, *, source="yahoo",
            quality=QualityStatus.OK, retrieved_at=None):
    retrieved_at = retrieved_at or dt.datetime(2026, 8, 3, 9, 0, tzinfo=dt.timezone.utc)
    bars = []
    for i, symbol in enumerate(symbols):
        base = 100.0 + i
        for j, ts in enumerate(sessions_index):
            close = base + j * 0.1
            bars.append(OHLCVBar(symbol=symbol, session_date=ts.date(), open=close, high=close + 1,
                                 low=close - 1, close=close, volume=1_000_000.0 + j, source=source,
                                 retrieved_at=retrieved_at, quality_status=quality))
    store.upsert_bars(bars)


def _store(tmp_path):
    return OHLCVStore(default_db_path(str(tmp_path)))


def _raise_if_called(*_a, **_k):
    raise AssertionError("Yahoo must not be called for a warm-cache request")


# --------------------------------------------------------------------------- warm cache
def test_warm_cache_zero_yahoo_calls(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(5)
    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 5)

    store = _store(tmp_path)
    _seed_ok(store, universe, idx)
    store.close()

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)

    assert dataset.coverage.network_call_count == 0
    assert dataset.coverage.cache_complete == len(universe)
    assert set(dataset.series_by_symbol) == set(universe)
    assert dataset.skipped_symbols == {}
    for rows in dataset.series_by_symbol.values():
        assert rows[-1]["date"] == recap_date
        assert rows[-2]["date"] == prev_date


def test_warm_cache_500_symbols_runtime(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(500)
    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 2)

    store = _store(tmp_path)
    _seed_ok(store, universe, idx)
    store.close()

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)

    t0 = time.time()
    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    elapsed = time.time() - t0

    assert dataset.coverage.network_call_count == 0
    assert len(dataset.series_by_symbol) == 500
    print(f"\n[perf] 500-symbol warm-cache load_universe: {elapsed:.3f}s")
    assert elapsed < 10.0  # generous ceiling - this is a correctness harness, not a benchmark


# --------------------------------------------------------------------------- cold cache
def test_cold_cache_fetches_then_second_call_is_warm(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(4)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    calls = []
    original = market._bulk_download_universe_ohlcv

    def counting(syms, period="1y"):
        calls.append(list(syms))
        return original(syms, period=period)

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", counting)

    dataset1 = ohlcv_service.load_universe(universe, recap_date, prev_date)
    assert len(calls) == 1
    assert dataset1.coverage.network_call_count == 1
    assert dataset1.coverage.cache_miss == len(universe)
    assert set(dataset1.series_by_symbol) == set(universe)

    calls.clear()
    dataset2 = ohlcv_service.load_universe(universe, recap_date, prev_date)
    assert len(calls) == 0  # second, identical request is warm
    assert dataset2.coverage.network_call_count == 0
    assert dataset2.coverage.cache_complete == len(universe)
    assert set(dataset2.series_by_symbol) == set(universe)


# --------------------------------------------------------------------------- partial cache
def test_partial_cache_fetches_only_incomplete_symbols(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    complete_universe = synthetic_universe(6, prefix="OK")
    incomplete_universe = synthetic_universe(2, prefix="GAP")
    universe = {**complete_universe, **incomplete_universe}

    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 5)
    store = _store(tmp_path)
    _seed_ok(store, complete_universe, idx)
    _seed_ok(store, incomplete_universe, idx[:-1])  # missing the latest session -> "tail"
    store.close()

    recap_date2, prev_date2 = fake_bulk_ohlcv(monkeypatch, incomplete_universe, sessions=63)
    assert recap_date2 == recap_date and prev_date2 == prev_date

    calls = []
    original = market._bulk_download_universe_ohlcv

    def counting(syms, period="1y"):
        calls.append(list(syms))
        return original(syms, period=period)

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", counting)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)

    assert len(calls) == 1
    assert set(calls[0]) == set(incomplete_universe)  # Yahoo saw ONLY the incomplete symbols
    assert dataset.coverage.network_call_count == 1
    assert dataset.coverage.cache_complete == len(complete_universe)
    assert dataset.coverage.cache_partial == len(incomplete_universe)
    assert set(dataset.series_by_symbol) == set(universe)


# --------------------------------------------------------------------------- quality rules
def test_backfill_pending_row_does_not_satisfy_cache(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(1)
    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 5)
    store = _store(tmp_path)
    _seed_ok(store, universe, idx[:-1])
    store.upsert_bars([OHLCVBar(symbol=next(iter(universe)), session_date=recap_date, open=None,
                                high=None, low=None, close=None, volume=None, source="yahoo",
                                retrieved_at=dt.datetime.now(dt.timezone.utc),
                                quality_status=QualityStatus.BACKFILL_PENDING)])
    store.close()

    recap_date2, prev_date2 = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)
    assert recap_date2 == recap_date and prev_date2 == prev_date

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    assert dataset.coverage.network_call_count == 1
    assert dataset.coverage.cache_complete == 0


# --------------------------------------------------------------------------- failure isolation
def test_store_unavailable_falls_back_to_yahoo(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    class BrokenStore:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated: database unavailable")

    monkeypatch.setattr(ohlcv_service, "OHLCVStore", BrokenStore)

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    assert set(dataset.series_by_symbol) == set(universe)
    assert dataset.coverage.network_call_count == 1
    assert dataset.warnings


def test_store_read_failure_falls_back_to_yahoo(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    def broken_get_range(self, *a, **k):
        raise RuntimeError("simulated: disk read error")

    monkeypatch.setattr(OHLCVStore, "get_range", broken_get_range)

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    assert set(dataset.series_by_symbol) == set(universe)
    assert dataset.coverage.network_call_count == 1
    assert dataset.warnings


# --------------------------------------------------------------------------- coverage telemetry
def test_coverage_diagnostics_counts(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    complete_universe = synthetic_universe(3, prefix="OK")
    cold_universe = synthetic_universe(2, prefix="COLD")
    universe = {**complete_universe, **cold_universe}

    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 5)
    store = _store(tmp_path)
    _seed_ok(store, complete_universe, idx)
    store.close()

    recap_date2, prev_date2 = fake_bulk_ohlcv(monkeypatch, cold_universe, sessions=63)
    assert recap_date2 == recap_date and prev_date2 == prev_date

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    cov = dataset.coverage
    assert cov.requested_symbols == 5
    assert cov.cache_complete == 3
    assert cov.cache_miss == 2
    assert cov.usable_symbols == 5
    assert cov.freshly_fetched == 2
    assert cov.network_call_count == 1
    assert cov.skipped_symbols == 0


# --------------------------------------------------------------------------- shared acquisition
def test_detectors_share_one_acquisition_no_duplicate_yahoo_calls(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(4)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    calls = []
    original = market._bulk_download_universe_ohlcv

    def counting(syms, period="1y"):
        calls.append(list(syms))
        return original(syms, period=period)

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", counting)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    assert len(calls) == 1

    # Every detector family now consumes the SAME dataset with zero further acquisition.
    rvol_rows, rvol_skip = ohlcv_service.relative_volume_rows_from_dataset(dataset, universe)
    tech_snapshot = technical.scan_technical_universe(
        list(universe), dataset.series_by_symbol, recap_date, skip_reasons=dataset.skipped_symbols)
    rel_snapshot = relative.scan_relative_performance_universe(
        list(universe), dataset.series_by_symbol, [], recap_date,
        skip_reasons=dataset.skipped_symbols)

    assert len(calls) == 1  # no detector triggered a second Yahoo call
    assert len(rvol_rows) == len(universe)
    assert len(tech_snapshot.scanned) == len(universe)
    assert len(rel_snapshot.scanned) == len(universe)


# --------------------------------------------------------------------------- invariance
def test_rvol_from_dataset_matches_direct_acquisition(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    volume_map = {s: [1_000_000.0 + i * 137 for i in range(63)] for s in universe}
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63,
                                            volume_map=volume_map)

    expected_rows, expected_skip = market.get_universe_relative_volume(universe, recap_date, prev_date)
    expected_by_symbol = {r["symbol"]: r["volx"] for r in expected_rows}

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    rows, skip_reasons = ohlcv_service.relative_volume_rows_from_dataset(dataset, universe)
    by_symbol = {r["symbol"]: r["volx"] for r in rows}

    assert set(by_symbol) == set(expected_by_symbol)
    for symbol, expected_volx in expected_by_symbol.items():
        assert by_symbol[symbol] == pytest.approx(expected_volx)


def test_technical_from_dataset_matches_direct_acquisition(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(2)
    symbols = list(universe)
    closes = {symbols[0]: [100.0 + i * 0.3 for i in range(63)],
             symbols[1]: [200.0 - i * 0.2 for i in range(63)]}
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63, close_map=closes)

    expected_series, expected_skip = market.get_universe_technical_series(universe, recap_date, prev_date)
    expected_snapshot = technical.scan_technical_universe(
        symbols, expected_series, recap_date, skip_reasons=expected_skip)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    snapshot = technical.scan_technical_universe(
        symbols, dataset.series_by_symbol, recap_date, skip_reasons=dataset.skipped_symbols)

    assert {s.instrument: [e.event_type for e in s.events] for s in snapshot.flagged} == \
        {s.instrument: [e.event_type for e in s.events] for s in expected_snapshot.flagged}
    assert snapshot.scanned == expected_snapshot.scanned


def test_relative_from_dataset_matches_direct_acquisition(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(2)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    expected_series, expected_skip = market.get_universe_technical_series(universe, recap_date, prev_date)
    benchmark = [{"date": d.date(), "close": 20000.0 + i}
                for i, d in enumerate(universe_sessions(63, recap_date)[0])]
    expected_snapshot = relative.scan_relative_performance_universe(
        list(universe), expected_series, benchmark, recap_date, skip_reasons=expected_skip)

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date)
    snapshot = relative.scan_relative_performance_universe(
        list(universe), dataset.series_by_symbol, benchmark, recap_date,
        skip_reasons=dataset.skipped_symbols)

    for symbol in universe:
        expected_r = next((r for r in expected_snapshot.results if r.instrument == symbol), None)
        actual_r = next((r for r in snapshot.results if r.instrument == symbol), None)
        assert (expected_r is None) == (actual_r is None)
        if expected_r is not None:
            assert actual_r.market_relative_5d_pp == pytest.approx(expected_r.market_relative_5d_pp) \
                if expected_r.market_relative_5d_pp is not None else actual_r.market_relative_5d_pp is None


# --------------------------------------------------------------------------- canonical isolation
def test_market_history_db_untouched(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, sessions=63)

    ohlcv_service.load_universe(universe, recap_date, prev_date)

    assert not (tmp_path / "data" / "market_history.db").exists()


# --------------------------------------------------------------------------- Packet 5.3E: spine-
# aware date-gap fix. `_finalize_symbol`'s date-gap check runs on rows already filtered by
# `apply_session_spine` (production `load_universe(..., spine=...)`), so a provider
# holiday-placeholder row can no longer masquerade as a missing real session.
def _session_around_holiday(n_total: int = 65, holiday_offset: int = 1):
    """`n_total` consecutive business-day dates ending at RECAP, with the one `holiday_offset`
    positions before the end singled out as a holiday: present in every stock's own stored
    series (a provider placeholder) but absent from the canonical spine. Returns
    `(full_dates, spine_dates, holiday, session_date, spine_prev_date)`."""
    full_dates = [ts.date() for ts in pd.bdate_range(end=RECAP, periods=n_total)]
    holiday = full_dates[-1 - holiday_offset]
    spine_dates = [d for d in full_dates if d != holiday]
    session_date = full_dates[-1]
    spine_prev_date = spine_dates[spine_dates.index(session_date) - 1]
    return full_dates, spine_dates, holiday, session_date, spine_prev_date


def _benchmark(dates: list, base=20000.0) -> list:
    return [{"date": d, "close": base + i} for i, d in enumerate(dates)]


# A - holiday placeholder: usable once spine-filtered
def test_spine_case_a_holiday_placeholder_becomes_usable(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = synthetic_universe(5)
    full_dates, spine_dates, holiday, session_date, prev_date = _session_around_holiday()
    store = _store(tmp_path)
    _seed_ok(store, universe, pd.DatetimeIndex(full_dates))  # store carries the holiday row
    store.close()

    from radar import session_alignment
    spine = session_alignment.canonical_session_spine(_benchmark(spine_dates))

    dataset = ohlcv_service.load_universe(universe, session_date, prev_date, spine=spine)

    assert dataset.coverage.network_call_count == 0
    assert set(dataset.series_by_symbol) == set(universe)
    for rows in dataset.series_by_symbol.values():
        assert holiday not in {r["date"] for r in rows}
        assert rows[-1]["date"] == session_date
        assert rows[-2]["date"] == prev_date


def test_spine_case_a_without_fix_reproduces_date_gap(tmp_path, monkeypatch):
    """Sanity check that the fixture genuinely reproduces the pre-fix bug when `spine` is
    omitted - proves case A is actually exercising the defect, not a no-op fixture."""
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = synthetic_universe(3)
    full_dates, spine_dates, holiday, session_date, prev_date = _session_around_holiday()
    store = _store(tmp_path)
    _seed_ok(store, universe, pd.DatetimeIndex(full_dates))
    store.close()

    dataset = ohlcv_service.load_universe(universe, session_date, prev_date)  # no spine
    assert dataset.series_by_symbol == {}
    assert all(reason == "date_gap" for reason in dataset.skipped_symbols.values())


# B - genuine missing session: date_gap protection preserved
def test_spine_case_b_genuine_missing_session_still_date_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = synthetic_universe(4)
    full_dates = [ts.date() for ts in pd.bdate_range(end=RECAP, periods=65)]
    missing = full_dates[-2]  # the immediately-prior genuine trading session, absent (no
    # placeholder) from the stock's own feed
    session_date, prev_date = full_dates[-1], full_dates[-2]

    store = _store(tmp_path)
    stock_dates = [d for d in full_dates if d != missing]
    _seed_ok(store, universe, pd.DatetimeIndex(stock_dates))
    store.close()

    from radar import session_alignment
    spine = session_alignment.canonical_session_spine(_benchmark(full_dates))  # spine includes `missing`

    dataset = ohlcv_service.load_universe(universe, session_date, prev_date, spine=spine)

    assert dataset.series_by_symbol == {}
    assert all(reason == "date_gap" for reason in dataset.skipped_symbols.values())


# C - future rows: spine given must not let a later-dated row leak into an earlier session
def test_spine_case_c_future_rows_excluded_from_earlier_session(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = synthetic_universe(2)
    all_dates = [ts.date() for ts in pd.bdate_range(end=RECAP + dt.timedelta(days=10), periods=70)]
    session_date = RECAP
    idx = all_dates.index(session_date)
    prev_date = all_dates[idx - 1]

    store = _store(tmp_path)
    _seed_ok(store, universe, pd.DatetimeIndex(all_dates))  # includes sessions after session_date
    store.close()

    from radar import session_alignment
    spine = session_alignment.canonical_session_spine(_benchmark(all_dates))  # spine also extends past D

    dataset = ohlcv_service.load_universe(universe, session_date, prev_date, spine=spine)

    for symbol, rows in dataset.series_by_symbol.items():
        assert rows[-1]["date"] == session_date
        assert all(row["date"] <= session_date for row in rows)


# D - consecutive valid sessions: unaffected by the fix
def test_spine_case_d_consecutive_sessions_unaffected(tmp_path, monkeypatch):
    universe = synthetic_universe(5)
    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 5)
    store = _store(tmp_path)
    _seed_ok(store, universe, idx)
    store.close()

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    from radar import session_alignment
    spine = session_alignment.canonical_session_spine(
        _benchmark([ts.date() for ts in idx]))

    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date, spine=spine)
    assert dataset.coverage.network_call_count == 0
    assert dataset.coverage.cache_complete == len(universe)
    assert set(dataset.series_by_symbol) == set(universe)


# E - detector invariance: a clean dataset (no placeholders) is identical with/without spine
def test_spine_case_e_clean_dataset_output_invariant(tmp_path, monkeypatch):
    universe = synthetic_universe(4)
    idx, recap_date, prev_date = universe_sessions(ohlcv_service.REQUIRED_LOOKBACK_SESSIONS + 5)
    store = _store(tmp_path)
    _seed_ok(store, universe, idx)
    store.close()
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)

    from radar import session_alignment, technical
    spine = session_alignment.canonical_session_spine(_benchmark([ts.date() for ts in idx]))

    dataset_no_spine = ohlcv_service.load_universe(universe, recap_date, prev_date)
    dataset_with_spine = ohlcv_service.load_universe(universe, recap_date, prev_date, spine=spine)

    assert dataset_no_spine.series_by_symbol == dataset_with_spine.series_by_symbol
    assert dataset_no_spine.skipped_symbols == dataset_with_spine.skipped_symbols

    snap_no_spine = technical.scan_technical_universe(
        list(universe), dataset_no_spine.series_by_symbol, recap_date)
    snap_with_spine = technical.scan_technical_universe(
        list(universe), dataset_with_spine.series_by_symbol, recap_date)
    assert [s.to_dict() for s in snap_no_spine.flagged] == [s.to_dict() for s in snap_with_spine.flagged]

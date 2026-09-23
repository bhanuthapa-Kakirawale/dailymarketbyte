"""Write-through from the existing universe Yahoo acquisition into `market_ohlcv.db`
(Phase 4.2 Packet 5.2). Offline only - `market._bulk_download_universe_ohlcv` is monkeypatched,
never a live yfinance call; the store writes to a tmp_path database.
"""
import datetime as dt

import market
import storage.ohlcv_repository as ohlcv_repository
from conftest_universe import fake_bulk_ohlcv, synthetic_universe
from storage.ohlcv_models import QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path


def _store(tmp_path):
    return OHLCVStore(default_db_path(str(tmp_path)))


# --------------------------------------------------------------------------- write-through
def test_existing_acquisition_persists_returned_bars(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons == {}

    store = _store(tmp_path)
    try:
        assert store.count_rows() > 0
        for symbol in universe:
            row = store.latest_session(symbol, source="yahoo")
            assert row is not None
            assert row.session_date == recap_date
            assert row.quality_status == QualityStatus.OK
            assert row.close is not None
    finally:
        store.close()


def test_backfill_pending_row_is_recorded_as_such(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    lagging = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, nan_close=[lagging])

    market.get_universe_technical_series(universe, recap_date, prev_date)

    store = _store(tmp_path)
    try:
        row = store.latest_session(lagging, source="yahoo")
        assert row is not None
        assert row.close is None
        assert row.quality_status == QualityStatus.BACKFILL_PENDING
    finally:
        store.close()


def test_missing_symbol_is_recorded_as_no_data(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    absent = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, missing=[absent])

    market.get_universe_technical_series(universe, recap_date, prev_date)

    store = _store(tmp_path)
    try:
        row = store.latest_session(absent, source="yahoo")
        assert row is not None
        assert row.session_date == recap_date
        assert row.quality_status == QualityStatus.NO_DATA
        assert row.close is None
    finally:
        store.close()


def test_no_additional_yahoo_call_is_caused_by_persistence(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    calls = []
    original = market._bulk_download_universe_ohlcv

    def counting(syms, period="1y"):
        calls.append(1)
        return original(syms, period=period)

    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", counting)

    market.get_universe_technical_series(universe, recap_date, prev_date)
    assert len(calls) == 1  # one bulk fetch total - persistence issued zero extra network calls


def test_repeated_storage_is_idempotent(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(2)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    market.get_universe_technical_series(universe, recap_date, prev_date)
    store = _store(tmp_path)
    try:
        first_count = store.count_rows()
    finally:
        store.close()

    # Second run for the same universe/dates persists the same rows, not new ones.
    market.get_universe_technical_series(universe, recap_date, prev_date)
    store = _store(tmp_path)
    try:
        assert store.count_rows() == first_count
    finally:
        store.close()


# --------------------------------------------------------------------------- failure isolation
def test_store_unavailable_does_not_break_acquisition(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    class BrokenStore:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated: database unavailable")

    monkeypatch.setattr(ohlcv_repository, "OHLCVStore", BrokenStore)

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons == {}
    assert set(series) == set(universe)


def test_store_write_failure_does_not_break_acquisition(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    def broken_upsert(self, bars):
        raise RuntimeError("simulated: disk full")

    monkeypatch.setattr(OHLCVStore, "upsert_bars", broken_upsert)

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    series, skip_reasons = market.get_universe_technical_series(universe, recap_date, prev_date)
    assert skip_reasons == {}
    assert set(series) == set(universe)


# --------------------------------------------------------------------------- canonical isolation
def test_market_history_db_is_untouched(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))

    universe = synthetic_universe(3)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    market.get_universe_technical_series(universe, recap_date, prev_date)

    from storage.repository import default_db_path as history_db_path
    assert not (tmp_path / "data" / "market_history.db").exists()
    assert history_db_path(str(tmp_path)) == str(tmp_path / "data" / "market_history.db")


# --------------------------------------------------------------------------- detector invariance
def test_detector_output_identical_with_and_without_write_through(monkeypatch):
    universe = synthetic_universe(4)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    series_before, skip_before = market.get_universe_technical_series(
        universe, recap_date, prev_date)

    # Re-run against the exact same fixture, now with write-through's store call forced to
    # fail loudly if it were to influence anything - detector output must be identical either
    # way, since write-through never feeds back into the returned values.
    class BrokenStore:
        def __init__(self, *a, **k):
            raise RuntimeError("must not affect detector output")

    monkeypatch.setattr(ohlcv_repository, "OHLCVStore", BrokenStore)
    series_after, skip_after = market.get_universe_technical_series(
        universe, recap_date, prev_date)

    assert series_before == series_after
    assert skip_before == skip_after

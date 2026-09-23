"""OHLCVStore: schema, upsert semantics, and queries (Phase 4.2 Packet 5.2). Offline only -
a real SQLite file under `tmp_path`, no network."""
import datetime as dt

from storage.ohlcv_models import OHLCVBar, QualityStatus
from storage.ohlcv_repository import DEFAULT_DB_RELPATH, OHLCVStore, default_db_path

NOW = dt.datetime(2026, 9, 22, 9, 0, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 9, 22, 9, 30, tzinfo=dt.timezone.utc)
D1 = dt.date(2026, 9, 22)
D2 = dt.date(2026, 9, 21)


def bar(symbol="ABC", session_date=D1, source="yahoo", close=812.40, retrieved_at=NOW,
       quality=QualityStatus.OK, open_=810.0, high=815.0, low=805.0, volume=1_000_000.0):
    return OHLCVBar(symbol=symbol, session_date=session_date, open=open_, high=high, low=low,
                    close=close, volume=volume, source=source, retrieved_at=retrieved_at,
                    quality_status=quality)


# --------------------------------------------------------------------------- schema
def test_default_db_path_matches_repository_convention(tmp_path):
    assert default_db_path(str(tmp_path)) == str(tmp_path / DEFAULT_DB_RELPATH)


def test_creates_database_and_stamps_schema_version(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        assert store.schema_version == 1
        version = store.conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1
    finally:
        store.close()


def test_required_indexes_exist(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        names = {r[1] for r in store.conn.execute("PRAGMA index_list(daily_ohlcv)").fetchall()}
        cols_by_index = {}
        for name in names:
            cols = [r[2] for r in store.conn.execute(f"PRAGMA index_info({name})").fetchall()]
            cols_by_index[name] = cols
        all_col_sets = list(cols_by_index.values())
        assert ["symbol", "session_date"] in all_col_sets
        assert ["session_date"] in all_col_sets
    finally:
        store.close()


def test_reopening_an_existing_database_does_not_recreate_tables(tmp_path):
    path = str(tmp_path / "market_ohlcv.db")
    store1 = OHLCVStore(path)
    store1.upsert_bars([bar()])
    store1.close()

    store2 = OHLCVStore(path)
    try:
        assert store2.count_rows() == 1
    finally:
        store2.close()


# --------------------------------------------------------------------------- upsert
def test_insert_one_bar(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        n = store.upsert_bars([bar()])
        assert n == 1
        assert store.count_rows() == 1
        row = store.latest_session("ABC")
        assert row.close == 812.40
        assert row.quality_status == QualityStatus.OK
    finally:
        store.close()


def test_repeated_identical_insert_does_not_duplicate(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar()])
        store.upsert_bars([bar()])
        assert store.count_rows() == 1
    finally:
        store.close()


def test_incomplete_row_corrected_by_later_upsert(tmp_path):
    """09:00 BACKFILL_PENDING (close=None) -> later corrected to OK with a real close, same
    (symbol, session_date, source) row, not a second row."""
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(close=None, quality=QualityStatus.BACKFILL_PENDING,
                               retrieved_at=NOW)])
        assert store.count_rows() == 1
        pending = store.latest_session("ABC")
        assert pending.close is None
        assert pending.quality_status == QualityStatus.BACKFILL_PENDING

        store.upsert_bars([bar(close=812.40, quality=QualityStatus.OK, retrieved_at=LATER)])
        assert store.count_rows() == 1
        corrected = store.latest_session("ABC")
        assert corrected.close == 812.40
        assert corrected.quality_status == QualityStatus.OK
        assert corrected.retrieved_at == LATER
    finally:
        store.close()


def test_same_session_different_providers_preserved(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(source="yahoo", close=812.40), bar(source="nse", close=812.55)])
        assert store.count_rows(symbol="ABC") == 2

        yahoo_row = store.latest_session("ABC", source="yahoo")
        nse_row = store.latest_session("ABC", source="nse")
        assert yahoo_row.close == 812.40
        assert nse_row.close == 812.55
    finally:
        store.close()


def test_upsert_for_one_provider_never_touches_another_providers_row(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(source="yahoo", close=812.40), bar(source="nse", close=812.55)])
        store.upsert_bars([bar(source="yahoo", close=900.0)])

        assert store.latest_session("ABC", source="yahoo").close == 900.0
        assert store.latest_session("ABC", source="nse").close == 812.55
    finally:
        store.close()


def test_upsert_empty_list_is_a_noop(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        assert store.upsert_bars([]) == 0
        assert store.count_rows() == 0
    finally:
        store.close()


# --------------------------------------------------------------------------- query
def test_get_range_filters_by_date_bounds(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(session_date=D2), bar(session_date=D1)])
        rows = store.get_range(["ABC"], D1, D1)
        assert [r.session_date for r in rows] == [D1]

        rows = store.get_range(["ABC"], D2, D1)
        assert [r.session_date for r in rows] == [D2, D1]
    finally:
        store.close()


def test_get_range_multi_symbol(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(symbol="ABC"), bar(symbol="XYZ")])
        rows = store.get_range(["ABC", "XYZ"], D1, D1)
        assert {r.symbol for r in rows} == {"ABC", "XYZ"}
    finally:
        store.close()


def test_get_range_source_filter(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(source="yahoo"), bar(source="nse")])
        rows = store.get_range(["ABC"], D1, D1, source="nse")
        assert len(rows) == 1
        assert rows[0].source == "nse"
    finally:
        store.close()


def test_get_range_empty_symbols_returns_empty(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        assert store.get_range([], D1, D1) == []
    finally:
        store.close()


def test_latest_session_returns_none_when_absent(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        assert store.latest_session("NOPE") is None
    finally:
        store.close()


def test_latest_session_picks_most_recent_date(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(session_date=D2, close=100.0), bar(session_date=D1, close=200.0)])
        assert store.latest_session("ABC").close == 200.0
    finally:
        store.close()


def test_count_rows_filters(tmp_path):
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    try:
        store.upsert_bars([bar(symbol="ABC", source="yahoo"), bar(symbol="ABC", source="nse"),
                           bar(symbol="XYZ", source="yahoo")])
        assert store.count_rows() == 3
        assert store.count_rows(symbol="ABC") == 2
        assert store.count_rows(source="yahoo") == 2
        assert store.count_rows(symbol="ABC", source="nse") == 1
    finally:
        store.close()

"""Phase 4.2 Packet 5.3D: focused, offline tests for the historical Radar validation runner.

No network, no live yfinance/NSE call anywhere - `market._bulk_download_universe_ohlcv` is
monkeypatched to raise if the offline loader ever tries to call it (the whole point of
`load_universe_offline`). `config.OUT_DIR` is isolated per test by the autouse
`_isolate_config_out_dir` fixture in `tests/conftest.py`.
"""
import datetime as dt
import inspect

import pandas as pd
import pytest

import market
import radar.historical_validation as hv
from storage.ohlcv_models import OHLCVBar, QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path
from storage.repository import MarketHistory

RECAP = dt.date(2026, 8, 3)


def _synthetic_universe(n=6, prefix="SYM"):
    return {f"{prefix}{i:03d}": f"{prefix}{i:03d} Ltd" for i in range(n)}


def _bdate_dates(end: dt.date, periods: int) -> list:
    return [ts.date() for ts in pd.bdate_range(end=end, periods=periods)]


def _seed(store: OHLCVStore, symbols, dates, *, base=100.0, quality=QualityStatus.OK,
         retrieved_at=None):
    retrieved_at = retrieved_at or dt.datetime(2026, 8, 3, 9, 0, tzinfo=dt.timezone.utc)
    bars = []
    for i, symbol in enumerate(symbols):
        for j, d in enumerate(dates):
            close = base + i + j * 0.1
            bars.append(OHLCVBar(symbol=symbol, session_date=d, open=close, high=close + 1,
                                 low=close - 1, close=close, volume=1_000_000.0 + j,
                                 source="yahoo", retrieved_at=retrieved_at, quality_status=quality))
    store.upsert_bars(bars)


def _benchmark(dates: list, base=20000.0) -> list:
    return [{"date": d, "close": base + i} for i, d in enumerate(dates)]


def _raise_if_called(*_a, **_k):
    raise AssertionError("load_universe_offline must never call Yahoo")


# --------------------------------------------------------------------------- offline loader
def test_offline_loader_never_calls_yahoo(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = _synthetic_universe(5)
    dates = _bdate_dates(RECAP, 60)
    store = OHLCVStore(default_db_path(str(tmp_path)))
    _seed(store, universe, dates)

    dataset = hv.load_universe_offline(universe, dates[-1], dates[-2], store)
    store.close()

    assert dataset.coverage.network_call_count == 0
    assert set(dataset.series_by_symbol) == set(universe)
    for rows in dataset.series_by_symbol.values():
        assert rows[-1]["date"] == dates[-1]
        assert rows[-2]["date"] == dates[-2]


def test_offline_loader_skips_incomplete_symbol_without_fetching(tmp_path, monkeypatch):
    """A symbol whose local cache stops one session short of `session_date` (a realistic
    mid-backfill gap) must be recorded as skipped, never trigger a Yahoo fetch."""
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = _synthetic_universe(3)
    dates = _bdate_dates(RECAP, 60)
    store = OHLCVStore(default_db_path(str(tmp_path)))
    _seed(store, ["SYM000", "SYM001"], dates)          # full history
    _seed(store, ["SYM002"], dates[:-1])                # missing the latest session

    dataset = hv.load_universe_offline(universe, dates[-1], dates[-2], store)
    store.close()

    assert dataset.coverage.network_call_count == 0
    assert set(dataset.series_by_symbol) == {"SYM000", "SYM001"}
    assert "SYM002" in dataset.skipped_symbols


# --------------------------------------------------------------------------- point-in-time
def test_future_session_never_read(tmp_path, monkeypatch):
    """A symbol's local cache extending past the requested session must never leak a future
    bar into that session's series - the strict point-in-time rule (packet spec section 4)."""
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = _synthetic_universe(2)
    all_dates = _bdate_dates(RECAP + dt.timedelta(days=10), 65)
    session_date = RECAP
    idx_at_session = all_dates.index(session_date)
    prev_date = all_dates[idx_at_session - 1]

    store = OHLCVStore(default_db_path(str(tmp_path)))
    # Seed with EXTREME values for the future rows so any leak is impossible to miss.
    bars = []
    for i, symbol in enumerate(universe):
        for j, d in enumerate(all_dates):
            future = d > session_date
            close = 999999.0 if future else 100.0 + i + j * 0.1
            volume = 50_000_000.0 if future else 1_000_000.0 + j
            bars.append(OHLCVBar(symbol=symbol, session_date=d, open=close, high=close, low=close,
                                 close=close, volume=volume, source="yahoo",
                                 retrieved_at=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
                                 quality_status=QualityStatus.OK))
    store.upsert_bars(bars)

    dataset = hv.load_universe_offline(universe, session_date, prev_date, store)
    store.close()

    assert dataset.series_by_symbol, "expected usable symbols"
    for symbol, rows in dataset.series_by_symbol.items():
        assert rows[-1]["date"] == session_date
        assert all(row["date"] <= session_date for row in rows), \
            f"{symbol}: a row dated after {session_date} leaked into its series"
        assert all(row["close"] < 999999.0 for row in rows), \
            f"{symbol}: an extreme future-dated close leaked into its series"


def test_run_one_session_benchmark_truncated_to_session_date(tmp_path, monkeypatch):
    """`run_one_session` must not even hold a benchmark row dated after `session_date` in
    memory - defense in depth beyond the store's own query bound."""
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = _synthetic_universe(4)
    dates = _bdate_dates(RECAP + dt.timedelta(days=20), 90)
    session_date = RECAP
    idx = dates.index(session_date)
    prev_date = dates[idx - 1]

    store = OHLCVStore(default_db_path(str(tmp_path)))
    _seed(store, universe, dates)  # includes sessions after session_date too

    benchmark_full = _benchmark(dates)  # includes future dates
    result = hv.run_one_session(universe, session_date, prev_date, benchmark_full, store)
    store.close()

    for snap_attr in ("technical_snapshot", "relative_snapshot"):
        snap = result[snap_attr]
        assert snap.session_date == session_date
    # every supporting session date any detector cites must be <= session_date
    for c in result["composite_snapshot"].candidates:
        assert all(d <= session_date for d in c.supporting_session_dates)


# --------------------------------------------------------------------------- holiday spine
def test_select_session_dates_excludes_holiday_placeholder():
    """The canonical spine used for session selection comes only from the benchmark's own
    dates - a weekday the benchmark never traded on (a holiday) is never selectable, even if a
    per-stock feed carries a spurious placeholder row for it."""
    dates = _bdate_dates(RECAP, 40)
    holiday = dates[20]
    spine_dates = [d for d in dates if d != holiday]
    benchmark = _benchmark(spine_dates)

    selected, prev_dates, warnings = hv.select_session_dates(benchmark, count=10)

    assert holiday not in selected
    assert len(selected) == 10
    assert selected == sorted(selected)


def test_holiday_row_dropped_from_series_in_run_one_session(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", _raise_if_called)
    universe = _synthetic_universe(3)
    dates = _bdate_dates(RECAP, 70)
    holiday = dates[40]                     # a weekday strictly inside the covered range
    spine_dates = [d for d in dates if d != holiday]
    session_date = dates[-1]
    prev_date = dates[-2]

    store = OHLCVStore(default_db_path(str(tmp_path)))
    _seed(store, universe, dates)           # per-stock feed DOES carry the holiday row
    benchmark_full = _benchmark(spine_dates)  # benchmark does NOT

    result = hv.run_one_session(universe, session_date, prev_date, benchmark_full, store)
    store.close()

    for symbol, rows in result["dataset"].series_by_symbol.items():
        assert holiday not in {row["date"] for row in rows}, \
            f"{symbol}: holiday placeholder row survived alignment"
    # Packet 5.3E: the holiday row is now filtered inside `load_universe_offline` (spine-aware,
    # BEFORE date-gap validation) rather than by the post-hoc `align_dataset_to_spine` safety
    # net - so all symbols stay usable, and the post-hoc pass finds nothing left to drop.
    assert set(result["dataset"].series_by_symbol) == set(universe)
    assert result["alignment_report"].rows_dropped == 0
    assert any("session spine filter" in w for w in result["dataset"].warnings)


# --------------------------------------------------------------------------- determinism
def test_deterministic_output(tmp_path):
    universe = _synthetic_universe(8)
    dates = _bdate_dates(RECAP, 70)
    session_date, prev_date = dates[-1], dates[-2]

    store = OHLCVStore(default_db_path(str(tmp_path)))
    _seed(store, universe, dates)
    benchmark_full = _benchmark(dates)
    as_of = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)

    r1 = hv.run_one_session(universe, session_date, prev_date, benchmark_full, store, as_of=as_of)
    r2 = hv.run_one_session(universe, session_date, prev_date, benchmark_full, store, as_of=as_of)
    store.close()

    c1 = [c.to_dict() for c in r1["composite_snapshot"].candidates]
    c2 = [c.to_dict() for c in r2["composite_snapshot"].candidates]
    assert c1 == c2
    assert (hv.session_metrics(r1) == hv.session_metrics(r2))


# --------------------------------------------------------------------------- no canonical mutation
def test_no_canonical_persistence_call_in_source():
    """`run_one_session` and everything it calls must never invoke `MarketHistory.save_report` -
    this validation run is explicitly non-canonical (packet spec section 16/22)."""
    source = inspect.getsource(hv)
    assert ".save_report(" not in source
    assert "history.save" not in source


def test_market_history_db_untouched(tmp_path):
    """A real canonical `market_history.db` in the same output directory must be byte-identical
    before and after a historical-validation run - `run_one_session` never opens or writes it
    (it takes no `MarketHistory`/history argument at all)."""
    db_path = str(tmp_path / "market_history.db")
    history = MarketHistory(db_path)
    history.close() if hasattr(history, "close") else None
    with open(db_path, "rb") as fh:
        before = fh.read()

    universe = _synthetic_universe(4)
    dates = _bdate_dates(RECAP, 70)
    session_date, prev_date = dates[-1], dates[-2]
    store = OHLCVStore(str(tmp_path / "market_ohlcv.db"))
    _seed(store, universe, dates)
    benchmark_full = _benchmark(dates)
    hv.run_one_session(universe, session_date, prev_date, benchmark_full, store)
    store.close()

    with open(db_path, "rb") as fh:
        after = fh.read()
    assert before == after

    sig = inspect.signature(hv.run_one_session)
    assert not any("history" in name.lower() for name in sig.parameters), \
        "run_one_session must take no canonical MarketHistory argument"


# --------------------------------------------------------------------------- artifact assembly
def test_build_validation_artifact_shapes(tmp_path):
    universe = _synthetic_universe(6)
    dates = _bdate_dates(RECAP, 70)
    store = OHLCVStore(default_db_path(str(tmp_path)))
    _seed(store, universe, dates)
    benchmark_full = _benchmark(dates)
    as_of = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)

    results = []
    for i in range(3):
        session_date, prev_date = dates[-3 + i], dates[-4 + i]
        results.append(hv.run_one_session(universe, session_date, prev_date, benchmark_full,
                                          store, as_of=as_of))
    store.close()

    artifact = hv.build_validation_artifact("SYNTH", universe, results, generated_at=as_of,
                                            network_calls={"benchmark_yahoo": 1}, elapsed_s=1.23)
    assert artifact["schema_version"] == hv.SCHEMA_VERSION
    assert len(artifact["session_summaries"]) == 3
    assert "candidates_per_session" in artifact["aggregate_metrics"]
    assert isinstance(artifact["candidates"], list)
    assert isinstance(artifact["quiet_discoveries"], list)
    assert artifact["metadata"]["network_calls"] == {"benchmark_yahoo": 1}

    md = hv.render_markdown(artifact)
    assert "Historical Validation" in md

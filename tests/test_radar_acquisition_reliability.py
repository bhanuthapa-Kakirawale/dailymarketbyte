"""Radar OHLCV acquisition reliability V1 (docs/OHLCV_STORE.md "Acquisition health"). Offline.

Root cause these pin (verified on production artifacts 2026-10-19): the Radar's daily tail
top-up fetches `TAIL_FETCH_PERIOD="1mo"`, which Yahoo serves as a calendar range back from the
run's own date - 19-22 sessions depending on weekday/holidays. `market.get_universe_technical_
series` held that WINDOW to `MIN_TECHNICAL_SESSIONS` (21) and rejected every symbol at once, and
`ohlcv_service._finalize_symbol` trusted that reason over the merged store history (~500
sessions) - 0/200 usable on 2026-10-05/06/08/12/19.
"""
import datetime as dt
import json
import os

import pandas as pd
import pytest

import config
import market
import radar.ohlcv_service as ohlcv_service
from conftest_universe import RECAP_DATE as RECAP
from conftest_universe import fake_bulk_ohlcv, synthetic_universe
from core.trading_calendar import SessionCalendar
from radar import relative_acquisition
from radar.ohlcv_service import AcquisitionHealth, CoverageDiagnostics, classify_health
from storage.ohlcv_models import OHLCVBar, QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path

H = AcquisitionHealth


def _seed(tmp_path, symbols, dates):
    store = OHLCVStore(default_db_path(str(tmp_path)))
    at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    store.upsert_bars(
        OHLCVBar(symbol=s, session_date=d, open=100.0, high=101.0, low=99.0, close=100.0 + j * .01,
                 volume=1e6, source="yahoo", retrieved_at=at, quality_status=QualityStatus.OK)
        for s in symbols for j, d in enumerate(dates))
    store.close()


def _bdays(end, n):
    return [ts.date() for ts in pd.bdate_range(end=end, periods=n)]


# --------------------------------------------------------------------------- root cause
@pytest.mark.parametrize("window_sessions", [19, 20])
def test_short_tail_window_uses_merged_store_history(tmp_path, monkeypatch, window_sessions):
    """The production failure, exactly: deep store history, today's bar missing (tail bucket),
    a 19/20-session top-up window. Before the fix: 0/N usable, every symbol
    `insufficient_technical_history`. After: every symbol usable over its merged history."""
    universe = synthetic_universe(30)
    history = _bdays(RECAP, 300)
    _seed(tmp_path, universe, history[:-1])          # store ends the session before RECAP
    recap, prev = fake_bulk_ohlcv(monkeypatch, universe, sessions=window_sessions)

    ds = ohlcv_service.load_universe(universe, recap, prev)

    assert ds.skipped_symbols == {}
    assert len(ds.series_by_symbol) == 30
    assert ds.coverage.fetch_window_short == 30
    assert ds.coverage.health == H.HEALTHY
    assert ds.coverage.provider_returned == 30 and ds.coverage.bars_written > 0
    for rows in ds.series_by_symbol.values():
        assert rows[-1]["date"] == recap and rows[-2]["date"] == prev
        assert len(rows) >= 250        # the merged store history (400-day query window), not the window


def test_genuinely_short_history_is_still_rejected(tmp_path, monkeypatch):
    """The 21-session floor is NOT waived - it is applied to the merged history. A symbol whose
    store + fetch together hold fewer than 21 sessions is still insufficient."""
    universe = synthetic_universe(3)
    recap, prev = fake_bulk_ohlcv(monkeypatch, universe, sessions=15)
    ds = ohlcv_service.load_universe(universe, recap, prev)
    assert ds.series_by_symbol == {}
    assert set(ds.skipped_symbols.values()) == {"insufficient_technical_history"}
    assert ds.coverage.health == H.INSUFFICIENT


def test_missing_final_bar_is_never_served_from_older_history(tmp_path, monkeypatch):
    """Deep store history never stands in for a session bar the provider did not deliver."""
    universe = synthetic_universe(10)
    _seed(tmp_path, universe, _bdays(RECAP, 300)[:-1])
    missing, pending = ("SYM000", "SYM001"), ("SYM002",)
    recap, prev = fake_bulk_ohlcv(monkeypatch, universe, sessions=19, missing=missing,
                                  nan_close=pending)
    ds = ohlcv_service.load_universe(universe, recap, prev)
    assert ds.skipped_symbols == {"SYM000": "no_recap_row", "SYM001": "no_recap_row",
                                  "SYM002": "backfill_pending"}
    assert len(ds.series_by_symbol) == 7
    assert ds.coverage.health == H.PARTIAL
    assert ds.coverage.skip_reason_counts == {"backfill_pending": 1, "no_recap_row": 2}


def test_post_holiday_session_placeholder_never_fakes_a_date_gap(tmp_path, monkeypatch):
    """Real calendar: Mon 2026-10-05 follows the Fri 2026-10-02 holiday, whose placeholder row
    is in the store AND in the fetch. The spine filter after the re-read must actually reach
    `_finalize_symbol` (it used to filter a throwaway dict - a universe-wide false date_gap)."""
    session, prev = dt.date(2026, 10, 5), dt.date(2026, 10, 1)
    universe = synthetic_universe(5)
    _seed(tmp_path, universe, _bdays(dt.date(2026, 10, 2), 200))   # includes the 10-02 placeholder
    recap, _ = fake_bulk_ohlcv(monkeypatch, universe, recap_date=session, sessions=20)
    spine = SessionCalendar.from_index_dates([]).sessions_between(dt.date(2025, 1, 1), session)
    assert dt.date(2026, 10, 2) not in spine
    ds = ohlcv_service.load_universe(universe, session, prev, spine=spine)
    assert ds.skipped_symbols == {}
    assert ds.coverage.health == H.HEALTHY
    for rows in ds.series_by_symbol.values():
        assert rows[-2]["date"] == prev
        assert dt.date(2026, 10, 2) not in {r["date"] for r in rows}


# --------------------------------------------------------------------------- source failures
def test_empty_bulk_response_is_source_failure(tmp_path, monkeypatch):
    universe = synthetic_universe(10)
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv",
                        lambda syms, period="3mo": pd.DataFrame())
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)
    recap, prev = _bdays(RECAP, 2)[1], _bdays(RECAP, 2)[0]
    ds = ohlcv_service.load_universe(universe, recap, prev)
    assert ds.coverage.health == H.SOURCE_FAILURE
    assert ds.coverage.provider_returned == 0 and ds.coverage.fetch_requested == 10
    assert ds.coverage.skip_reason_counts == {"no_recap_row": 10}


def test_fetch_exception_is_source_failure(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionError("network down")
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", boom)
    universe = synthetic_universe(4)
    ds = ohlcv_service.load_universe(universe, RECAP, _bdays(RECAP, 2)[0])
    assert ds.coverage.health == H.SOURCE_FAILURE
    assert set(ds.skipped_symbols.values()) == {"fetch_failed"}


def test_partial_provider_response_counts_and_no_fabricated_bars(tmp_path, monkeypatch):
    universe = synthetic_universe(20)
    _seed(tmp_path, universe, _bdays(RECAP, 120)[:-1])
    missing = tuple(f"SYM{i:03d}" for i in range(5))
    recap, prev = fake_bulk_ohlcv(monkeypatch, universe, sessions=19, missing=missing)
    ds = ohlcv_service.load_universe(universe, recap, prev)
    assert ds.coverage.health == H.PARTIAL
    assert ds.coverage.provider_returned == 15
    assert len(ds.series_by_symbol) == 15
    assert all(s not in ds.series_by_symbol for s in missing)


# --------------------------------------------------------------------------- isolation
def test_one_malformed_symbol_never_collapses_the_universe(tmp_path, monkeypatch):
    """One frame that raises while its rows are built (a non-numeric Close) is excluded on its
    own - it used to abort the whole fetch (every symbol -> fetch_failed) and the whole
    write-through (no bar persisted for anyone)."""
    universe = synthetic_universe(10)
    recap, prev = fake_bulk_ohlcv(monkeypatch, universe, sessions=60)
    raw = market._bulk_download_universe_ohlcv(list(universe))
    raw[("SYM003.NS", "Close")] = raw[("SYM003.NS", "Close")].astype(object)
    raw.loc[raw.index[-5], ("SYM003.NS", "Close")] = "n/a"
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", lambda syms, period="3mo": raw)

    ds = ohlcv_service.load_universe(universe, recap, prev)

    assert ds.skipped_symbols == {"SYM003": "malformed"}
    assert len(ds.series_by_symbol) == 9
    assert ds.coverage.health == H.PARTIAL
    store = OHLCVStore(default_db_path(str(tmp_path)))
    try:
        persisted = {r.symbol for r in store.get_range(list(universe), recap, recap)}
    finally:
        store.close()
    assert persisted == set(universe) - {"SYM003"}


# --------------------------------------------------------------------------- health classes
@pytest.mark.parametrize("requested,usable,returned,complete,expected", [
    (200, 200, 200, 0, H.HEALTHY), (200, 190, 200, 0, H.HEALTHY), (200, 189, 200, 0, H.PARTIAL),
    (200, 0, 200, 0, H.INSUFFICIENT), (200, 0, 0, 5, H.INSUFFICIENT),
    (200, 0, 0, 0, H.SOURCE_FAILURE), (0, 0, 0, 0, H.NOT_REQUESTED)])
def test_classify_health(requested, usable, returned, complete, expected):
    cov = CoverageDiagnostics(requested_symbols=requested, usable_symbols=usable,
                              provider_returned=returned, cache_complete=complete)
    assert classify_health(cov) == expected


# --------------------------------------------------------------------------- movers message
def test_movers_shortfall_message_names_the_lost_stage(monkeypatch):
    import yfinance as yf
    universe = synthetic_universe(12)
    recap, prev = fake_bulk_ohlcv(monkeypatch, universe, sessions=40, date_gap=tuple(universe))
    raw = market._bulk_download_universe_ohlcv(list(universe))
    monkeypatch.setattr(yf, "download", lambda *a, **k: raw)       # get_movers' own fetch
    with pytest.raises(RuntimeError) as exc:
        market.get_movers_audited(universe, recap, prev)
    msg = str(exc.value)
    assert msg.startswith(f"Only 0/12 stocks had clean data for {recap}")
    assert "provider_returned=12" in msg and "date_gap=12" in msg
    assert "primary_cause=SESSION_DATE_MISMATCH" in msg
    assert "prev_row_seen=" in msg and f"(expected {prev})" in msg


def test_movers_empty_response_is_provider_empty(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)
    universe = synthetic_universe(12)
    with pytest.raises(RuntimeError, match="primary_cause=PROVIDER_EMPTY_RESPONSE"):
        market.get_movers_audited(universe, RECAP, _bdays(RECAP, 2)[0])


# --------------------------------------------------------------------------- benchmark recap
D = dt.date


def _archive_fetch(close=23329.0, chg=-85.3, pct=-0.36):
    def fetch(day):
        row = {"Index Name": "Nifty 50", "Index Date": day.strftime("%d-%m-%Y"),
               "Open Index Value": 23454.05, "High Index Value": 23489.0,
               "Low Index Value": 23285.75, "Closing Index Value": close,
               "Points Change": chg, "Change(%)": pct}
        return {"rows": {"NIFTY 50": row}, "url": "https://nsearchives.nseindia.com/x.csv",
                "retrieved_at": "2026-09-22T14:00:00+00:00", "published_at": None}
    return fetch


def _bench_until(last):
    dates = SessionCalendar().sessions_between(D(2026, 6, 1), last)
    series = [{"date": d, "close": 23000.0 + i} for i, d in enumerate(dates)]
    series[-1]["close"] = 23414.3
    return series


def test_benchmark_lag_recovered_puts_session_on_spine():
    """2026-10-14 07:03: Yahoo ^NSEI ended a session early; the Radar's spine lost the session
    the report itself had recovered. The same validated NSE recap close now extends the spine."""
    from radar import session_alignment
    now = dt.datetime(2026, 9, 22, 20, 0, tzinfo=config.IST)
    series = _bench_until(D(2026, 9, 21))
    out = relative_acquisition.extend_benchmark_to_session(series, D(2026, 9, 22), now=now,
                                                           fetch=_archive_fetch())
    assert out[-1]["date"] == D(2026, 9, 22) and out[-1]["close"] == 23329.0
    assert out[-1]["source"] and out[-1]["provenance"]["validation_status"] == "VALIDATED"
    assert D(2026, 9, 22) in session_alignment.canonical_session_list(out)


def test_benchmark_invalid_archive_row_adds_nothing():
    now = dt.datetime(2026, 9, 22, 20, 0, tzinfo=config.IST)
    series = _bench_until(D(2026, 9, 21))
    out = relative_acquisition.extend_benchmark_to_session(
        series, D(2026, 9, 22), now=now, fetch=_archive_fetch(chg=+400.0, pct=1.7))
    assert out == series


def test_benchmark_extension_is_a_noop_without_network_when_covered():
    series = _bench_until(D(2026, 9, 21))

    def no_fetch(_day):
        raise AssertionError("no fetch when the session is already covered")
    assert relative_acquisition.extend_benchmark_to_session(series, D(2026, 9, 18),
                                                            fetch=no_fetch) is series


def test_benchmark_extension_never_uses_an_unfinished_session():
    now = dt.datetime(2026, 9, 22, 11, 0, tzinfo=config.IST)          # session still trading

    def no_fetch(_day):
        raise AssertionError("an in-progress session is never fetched")
    series = _bench_until(D(2026, 9, 21))
    assert relative_acquisition.extend_benchmark_to_session(series, D(2026, 9, 22), now=now,
                                                            fetch=no_fetch) == series


# --------------------------------------------------------------------------- diagnostics command
def test_acquisition_diagnostics_is_read_only(tmp_path, monkeypatch, capsys):
    from radar import acquisition_diagnostics as diag
    radar_dir = tmp_path / "radar"
    radar_dir.mkdir()
    base = {"pipeline_status": "SUCCESS", "universe_requested": 200, "composite_candidate_count": 0}
    (radar_dir / "daily_radar_2026-10-12.json").write_text(json.dumps(
        {**base, "universe_usable": 0, "coverage_diagnostics": {"skipped_symbols": 200}}))
    (radar_dir / "daily_radar_2026-10-14.json").write_text(json.dumps(
        {**base, "universe_usable": 200, "composite_candidate_count": 18}))
    (radar_dir / "daily_radar_2026-10-15.json").write_text(json.dumps(
        {**base, "pipeline_status": "FAILED", "universe_usable": 0, "coverage_diagnostics": {
            "acquisition": {"health": "SOURCE_FAILURE", "expected_universe": 200, "usable": 0,
                            "skip_reason_counts": {"fetch_failed": 200}}}}))
    jobs = tmp_path / "report_jobs" / "2026-10-13"
    jobs.mkdir(parents=True)
    (jobs / "r.json").write_text(json.dumps({"run_status": "FAILED",
                                             "failure_reason": "RuntimeError: Only 0 stocks"}))
    before = {p: os.path.getmtime(p) for p in map(str, tmp_path.rglob("*"))}

    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no network")))
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))
    assert diag.main(["--sessions", "20"]) == 0
    out = capsys.readouterr().out
    rows = diag.collect(str(tmp_path))
    summary = diag.summarize(rows)

    assert {p: os.path.getmtime(p) for p in map(str, tmp_path.rglob("*"))} == before
    assert summary["acquisition_failed"] == ["2026-10-12", "2026-10-15"]
    assert summary["no_radar_artifact"] == ["2026-10-13"]
    assert "INSUFFICIENT*" in out and "SOURCE_FAILURE" in out

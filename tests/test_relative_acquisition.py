"""radar/relative_acquisition.py: benchmark acquisition for relative-performance detection.
Offline only - `market.history` is monkeypatched, never called for real.
"""
import datetime as dt

import pandas as pd
import pytest

import market
from radar.relative_acquisition import (BENCHMARK_SYMBOL, build_market_benchmark_series,
                                        build_sector_benchmark_series)
from storage.ohlcv_repository import OHLCVStore, default_db_path


def _df(closes: list, start: dt.date = dt.date(2026, 1, 1)) -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


def _full_df(closes: list, start: dt.date = dt.date(2026, 1, 1)) -> pd.DataFrame:
    """A benchmark fetch with the full OHLCV columns `market.history` actually returns -
    needed to exercise write-through, which reads Open/High/Low/Volume too."""
    idx = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({
        "Open": [c - 0.5 for c in closes], "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes], "Close": closes,
        "Volume": [0.0] * len(closes),
    }, index=idx)


def test_build_market_benchmark_series_shape(monkeypatch):
    monkeypatch.setattr(market, "history", lambda ticker, period: _df([100.0, 101.0, 102.5]))
    series = build_market_benchmark_series()
    assert [p["close"] for p in series] == [100.0, 101.0, 102.5]
    assert series[0]["date"] == dt.date(2026, 1, 1)


def test_build_market_benchmark_series_degrades_to_empty_on_failure(monkeypatch):
    def _boom(ticker, period):
        raise RuntimeError("no data for ^NSEI")
    monkeypatch.setattr(market, "history", _boom)
    assert build_market_benchmark_series() == []


def test_build_sector_benchmark_series_one_fetch_per_sector(monkeypatch):
    calls = []

    def _fake(ticker, period):
        calls.append(ticker)
        return _df([100.0, 103.0])
    monkeypatch.setattr(market, "history", _fake)

    result = build_sector_benchmark_series()
    assert set(result.keys()) == {label for label, _nse, _yt in market.SECTORS}
    assert len(calls) == len(market.SECTORS)              # one fetch per sector, no N+1
    assert len(set(calls)) == len(calls)                  # each ticker fetched exactly once


def test_build_sector_benchmark_series_skips_failing_sector(monkeypatch):
    def _fake(ticker, period):
        if ticker == market.SECTORS[0][2]:
            raise RuntimeError("boom")
        return _df([100.0, 101.0])
    monkeypatch.setattr(market, "history", _fake)

    result = build_sector_benchmark_series()
    assert market.SECTORS[0][0] not in result
    assert len(result) == len(market.SECTORS) - 1


# --------------------------------------------------------------------- benchmark write-through
# Phase 4.2 Packet 6.2V: build_market_benchmark_series now also persists the same fetch into
# market_ohlcv.db (radar/relative_acquisition.py::_write_through_benchmark_ohlcv), mirroring
# market._write_through_ohlcv's pattern for a single benchmark instrument. These tests prove the
# write-through never adds a network call, actually persists what was already fetched, survives
# a storage failure without corrupting the function's own return value, and touches only the
# OHLCV cache - never any other local database.
def test_benchmark_write_through_does_not_increase_fetch_count(monkeypatch, tmp_path):
    calls = []

    def _fake(ticker, period):
        calls.append(ticker)
        return _full_df([100.0, 101.0, 102.5])
    monkeypatch.setattr(market, "history", _fake)

    build_market_benchmark_series(out_dir=str(tmp_path))
    assert calls == ["^NSEI"]


def test_benchmark_write_through_persists_already_fetched_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "history",
                        lambda ticker, period: _full_df([100.0, 101.0, 102.5]))
    series = build_market_benchmark_series(out_dir=str(tmp_path))
    assert len(series) == 3

    store = OHLCVStore(default_db_path(str(tmp_path)))
    try:
        rows = store.get_range([BENCHMARK_SYMBOL], dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    finally:
        store.close()
    assert [r.close for r in rows] == [100.0, 101.0, 102.5]
    assert all(r.symbol == BENCHMARK_SYMBOL for r in rows)
    assert all(r.source == "yahoo" for r in rows)
    assert all(r.quality_status.value == "OK" for r in rows)


def test_benchmark_write_through_store_failure_does_not_break_return_value(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "history",
                        lambda ticker, period: _full_df([100.0, 101.0]))
    import storage.ohlcv_repository as ohlcv_repository

    def _boom(*a, **k):
        raise RuntimeError("store unavailable")
    monkeypatch.setattr(ohlcv_repository, "OHLCVStore", _boom)

    series = build_market_benchmark_series(out_dir=str(tmp_path))
    assert [p["close"] for p in series] == [100.0, 101.0]


def test_benchmark_write_through_touches_only_ohlcv_db(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "history",
                        lambda ticker, period: _full_df([100.0, 101.0]))
    build_market_benchmark_series(out_dir=str(tmp_path))
    created = {p.name for p in tmp_path.rglob("*.db")}
    assert created == {"market_ohlcv.db"}

"""radar/relative_acquisition.py: benchmark acquisition for relative-performance detection.
Offline only - `market.history` is monkeypatched, never called for real.
"""
import datetime as dt

import pandas as pd
import pytest

import market
from radar.relative_acquisition import build_market_benchmark_series, build_sector_benchmark_series


def _df(closes: list, start: dt.date = dt.date(2026, 1, 1)) -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


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

"""Benchmark acquisition for the relative-performance detector (Phase 4.2 Packet 3).

Mirrors `radar/technical_acquisition.py`'s split: acquisition (this module, talks to
`market.py`, which talks to yfinance) is kept separate from detection (`radar/relative.py`,
pure and offline). Per-stock series are NOT re-fetched here - `radar.relative.
scan_relative_performance_universe` reuses the same per-symbol OHLC series
`radar.technical_acquisition.build_universe_technical_series` already fetches (only `close`/
`date` are read), so a relative-performance scan issues exactly ONE extra network call (the
market benchmark) on top of an existing technical-structure scan, never a second per-symbol
bulk fetch (packet spec section 15: no N+1 acquisition).

Sector-index acquisition is provided here too, reusing `market.SECTORS` (the same 12 tickers
`market.get_sectors` already trusts) - fetched once per sector, not once per stock. This module
never invents a stock->sector MAPPING (which stock belongs to which sector) - that remains
entirely caller-supplied (`radar.relative`'s `sector_map` parameter), since no reliable mapping
exists anywhere in this codebase yet. See docs/MARKET_INTELLIGENCE_RADAR.md.
"""
from __future__ import annotations

import market


def build_market_benchmark_series(period: str = "1y") -> list:
    """The market benchmark's own session series, oldest first: `[{"date": date, "close":
    float}, ...]`. Uses `market.history("^NSEI", ...)` - the same generic, already-verified
    single-ticker fetch path `market.get_globals`/`market.get_sectors` use for every other
    index/ticker in this pipeline, rather than a new acquisition path. Returns `[]` (never
    raises) if the fetch fails, so a benchmark outage degrades to "market-relative unavailable"
    rather than aborting the whole scan.
    """
    try:
        df = market.history("^NSEI", period)
    except Exception as exc:
        print(f"[radar.relative] market benchmark (^NSEI) unavailable: {exc}")
        return []
    return [{"date": ts.date(), "close": float(row.Close)} for ts, row in df.iterrows()
           if row.Close is not None and row.Close > 0]


def build_sector_benchmark_series(period: str = "1y") -> dict:
    """Every `market.SECTORS` index's own session series, oldest first, keyed by the same
    label `market.get_sectors` uses (`"Bank"`, `"IT"`, ...): `{label: [{"date": date, "close":
    float}, ...]}`. One fetch per sector (12 total), never per stock. A sector whose fetch
    fails is simply absent from the returned dict - `radar.relative` already treats a missing
    sector series as "sector-relative unavailable" for any symbol mapped to it, never a crash.
    """
    out = {}
    for label, _nse_name, yahoo_ticker in market.SECTORS:
        try:
            df = market.history(yahoo_ticker, period)
        except Exception as exc:
            print(f"[radar.relative] sector benchmark {label} ({yahoo_ticker}) unavailable: {exc}")
            continue
        out[label] = [{"date": ts.date(), "close": float(row.Close)} for ts, row in df.iterrows()
                      if row.Close is not None and row.Close > 0]
    return out


__all__ = ["build_market_benchmark_series", "build_sector_benchmark_series"]

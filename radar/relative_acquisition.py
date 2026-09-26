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

## Benchmark write-through (Phase 4.2 Packet 6.2V)

`build_market_benchmark_series` also persists the SAME OHLCV rows it already downloaded into
the local raw-OHLCV cache (`market_ohlcv.db`, `storage/ohlcv_repository.py`), under the literal
symbol `"^NSEI"` - no collision is possible with any stock symbol, which never contains `^`.
This is a write-through side effect on an EXISTING acquisition call, not a new fetch: it fires
from the same `df` this function already has in memory before trimming it down to its
`[{"date","close"}]` return shape, mirrors `market._write_through_ohlcv`'s exact pattern
(Packet 5.2) applied to a single benchmark instrument instead of a stock universe, and can never
change what this function returns or raise out of it (wrapped in try/except, storage-failure
isolated exactly like the stock path). `radar.visual_evidence.build_visual_evidence` is the sole
reader of these rows - it never calls this function or `market.history` itself, only
`storage.ohlcv_repository.OHLCVStore.get_range(["^NSEI"], ...)`, so a Radar video render adds
zero network calls once at least one Radar run has fetched the benchmark.
"""
from __future__ import annotations

import datetime as dt

import market

BENCHMARK_SYMBOL = "^NSEI"


def build_market_benchmark_series(period: str = "1y", *, out_dir: str | None = None) -> list:
    """The market benchmark's own session series, oldest first: `[{"date": date, "close":
    float}, ...]`. Uses `market.history("^NSEI", ...)` - the same generic, already-verified
    single-ticker fetch path `market.get_globals`/`market.get_sectors` use for every other
    index/ticker in this pipeline, rather than a new acquisition path. Returns `[]` (never
    raises) if the fetch fails, so a benchmark outage degrades to "market-relative unavailable"
    rather than aborting the whole scan.

    As a side effect, write-through persists this same fetch into `market_ohlcv.db` (see module
    docstring) - `out_dir` only controls where that local cache lives (defaults to
    `config.OUT_DIR`, same as every other store in this codebase) and never affects the
    network fetch or this function's return value.
    """
    try:
        df = market.history("^NSEI", period)
    except Exception as exc:
        print(f"[radar.relative] market benchmark (^NSEI) unavailable: {exc}")
        return []
    try:
        _write_through_benchmark_ohlcv(df, out_dir=out_dir)
    except Exception as exc:
        # Storage failure must never take down the acquisition it rides on - same defense in
        # depth as market._write_through_ohlcv's own caller (Phase 4.2 Packet 5.2, section 9).
        print(f"[radar.relative_acquisition] benchmark OHLCV write-through raised unexpectedly, "
             f"ignoring: {exc}")
    # Benchmark gap recovery (AFTER the write-through, so the raw Yahoo cache stays Yahoo-only):
    # a canonical session ^NSEI lacks (2026-09-22) comes from NSE's end-of-day index file,
    # validated; every recovered row carries its own provenance under "source"/"provenance".
    recovered = {}
    try:
        df, records = market.recover_index_gaps(df, BENCHMARK_SYMBOL)
        recovered = {r["session_date"]: r for r in records if r["validation_status"] == "VALIDATED"}
        _LAST_RECOVERY[:] = records
    except Exception as exc:
        print(f"[radar.relative_acquisition] benchmark gap recovery failed, gaps stay gaps: {exc}")
        _LAST_RECOVERY[:] = []
    out = []
    for ts, row in df.iterrows():
        if row.Close is None or not row.Close > 0:
            continue
        item = {"date": ts.date(), "close": float(row.Close)}
        rec = recovered.get(ts.date().isoformat())
        if rec is not None:
            item["source"] = rec["source"]
            item["provenance"] = rec
        out.append(item)
    return out


# Provenance of the most recent benchmark fetch's gap recovery (every attempt, validated or not)
# - read by `radar.daily_pipeline` so a run's issues say when a fallback was tried.
_LAST_RECOVERY: list = []


def last_benchmark_recovery() -> list:
    return list(_LAST_RECOVERY)


def _write_through_benchmark_ohlcv(df, *, symbol: str = BENCHMARK_SYMBOL,
                                   source: str = "yahoo", out_dir: str | None = None) -> None:
    """Persist the benchmark OHLCV `build_market_benchmark_series` already downloaded into
    `market_ohlcv.db`, using ONLY the bars already in memory - no second Yahoo call. Mirrors
    `market._write_through_ohlcv` (Packet 5.2): a row is skipped only when Close is missing or
    non-positive; Volume is persisted as `None` rather than fabricated when the index provider
    doesn't supply a meaningful reading (never assumed to be 0). Write-only and pure
    log-and-return-on-failure - never raises, never read back here to influence anything.
    """
    try:
        import pandas as pd

        from config import OUT_DIR
        from storage.ohlcv_models import OHLCVBar, QualityStatus
        from storage.ohlcv_repository import OHLCVStore, default_db_path
    except Exception as exc:
        print(f"[radar.relative_acquisition] OHLCV store unavailable, skipping benchmark "
             f"write-through: {exc}")
        return

    retrieved_at = dt.datetime.now(dt.timezone.utc)
    bars = []
    for ts, row in df.iterrows():
        close = None if pd.isna(row.Close) else float(row.Close)
        if close is None or close <= 0:
            continue  # not a usable reading - skip rather than guess a status
        open_ = None if pd.isna(row.Open) else float(row.Open)
        high = None if pd.isna(row.High) else float(row.High)
        low = None if pd.isna(row.Low) else float(row.Low)
        volume = None if pd.isna(row.Volume) else float(row.Volume)
        bars.append(OHLCVBar(symbol=symbol, session_date=ts.date(), open=open_, high=high,
                             low=low, close=close, volume=volume, source=source,
                             retrieved_at=retrieved_at, quality_status=QualityStatus.OK))

    try:
        store = OHLCVStore(default_db_path(out_dir or OUT_DIR))
    except Exception as exc:
        print(f"[radar.relative_acquisition] OHLCV store unavailable, skipping benchmark "
             f"write-through: {exc}")
        return
    try:
        n = store.upsert_bars(bars)
        print(f"[radar.relative_acquisition] benchmark OHLCV write-through: {n} bars persisted "
             f"for {symbol}")
    except Exception as exc:
        print(f"[radar.relative_acquisition] benchmark OHLCV write-through failed: {exc}")
    finally:
        store.close()


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


__all__ = ["build_market_benchmark_series", "build_sector_benchmark_series", "BENCHMARK_SYMBOL",
           "last_benchmark_recovery"]

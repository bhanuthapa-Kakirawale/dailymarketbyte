"""Relative-performance detection: is a stock behaving unusually strongly or weakly relative to
its market and, where a reliable mapping exists, its sector - never a prediction, a rating, or
classical chart-based "Relative Strength" (Phase 4.2 Packet 3).

Two hard boundaries, mirrored from `radar/technical.py`:

* **No acquisition.** The only inputs are per-symbol OHLC session series and a benchmark
  session series, both already fetched by `radar.relative_acquisition`. Nothing here calls a
  provider, a website or a model.
* **No mutation.** Inputs are read and never written; every reading is a fresh
  `RelativePerformance`.

This is a return DIFFERENTIAL, not RSI and not Mansfield RS:

    relative_performance_P = stock_return_P - benchmark_return_P

where `return_P = (current_close / close_P_sessions_ago - 1) * 100`, in percentage points, for
P in (1, 5, 20) sessions. The current session is included in the return (this detector describes
*current* relative performance), so a P-session return needs P+1 closes.

Independent of every other detector by construction: this module imports nothing from
`radar.volume` or `radar.technical` and takes no `VolumeAnomaly`/`TechnicalStructure` as input -
a quiet, non-mover stock with genuine relative strength must be preserved exactly like one that
is also flagged elsewhere (packet spec Case A).
"""
from __future__ import annotations

import datetime as dt

from .models import RelativePerformance, RelativePerformanceSnapshot, RelativePersistenceState
from .thresholds import DEFAULT_RELATIVE_THRESHOLDS, RelativePerformanceThresholds

WINDOWS = (1, 5, 20)


def scan_relative_performance_universe(universe: list, series_by_symbol: dict,
                                       benchmark_series: list, session_date: dt.date, *,
                                       benchmark_source: str = "NIFTY 50 (^NSEI)",
                                       sector_map: dict | None = None,
                                       sector_series_by_name: dict | None = None,
                                       sector_source: str | None = None,
                                       skip_reasons: dict | None = None,
                                       thresholds: RelativePerformanceThresholds = DEFAULT_RELATIVE_THRESHOLDS,
                                       as_of: dt.datetime | None = None) -> RelativePerformanceSnapshot:
    """Compute market- (and, where mapped, sector-) relative performance for every instrument
    in `universe` as of `session_date`.

    `universe` is entirely caller-supplied, exactly like `radar.volume.scan_universe` and
    `radar.technical.scan_technical_universe` - never defaulted to or hardcoded as a particular
    index's constituents.

    `series_by_symbol[symbol]` is oldest-first `[{"date": date, "close": float, ...}, ...]`
    session history ending at the stock's own latest available session - the exact shape
    `radar.technical_acquisition.build_universe_technical_series` already returns, reused here
    rather than re-fetched (`high`/`low` keys, if present, are ignored). A symbol absent, too
    short, or not ending at `session_date` (suspended / stale / incomplete current data) is
    recorded in `skipped`, never guessed at.

    `benchmark_series` is the market benchmark's own session series in the same shape, fetched
    ONCE by the caller (`radar.relative_acquisition.build_market_benchmark_series`) - never
    refetched per symbol.

    `sector_map` (symbol -> sector label) and `sector_series_by_name` (sector label -> session
    series) are both optional and both default to empty: this package never fabricates a
    stock->sector mapping. A symbol with no entry in `sector_map`, or whose sector has no entry
    in `sector_series_by_name`, simply gets `sector_relative_*_pp = None` with a `data_quality`
    note - market-relative output is entirely unaffected by a sector-data failure.
    """
    universe_list = list(dict.fromkeys(universe))          # dedupe, preserve caller's order
    snapshot = RelativePerformanceSnapshot(
        session_date=session_date, generated_at=as_of or dt.datetime.now(dt.timezone.utc),
        universe_size=len(universe_list), universe_source="caller-supplied universe list",
        benchmark_source=benchmark_source, sector_source=sector_source)

    benchmark_by_date = _series_to_close_by_date(benchmark_series)
    if not benchmark_by_date:
        snapshot.warnings.append("benchmark series empty or unavailable; "
                                 "market-relative unavailable for every symbol")

    sector_map = sector_map or {}
    sector_by_date_cache = {name: _series_to_close_by_date(series)
                            for name, series in (sector_series_by_name or {}).items()}

    for symbol in universe_list:
        series = series_by_symbol.get(symbol)
        skip_reason = _skip_reason(series, session_date)
        if skip_reason is not None:
            snapshot.skipped[symbol] = (skip_reasons or {}).get(symbol, skip_reason)
            continue
        snapshot.scanned.append(symbol)

        sector_name = sector_map.get(symbol)
        sector_by_date = sector_by_date_cache.get(sector_name) if sector_name else None
        snapshot.results.append(_build_relative_performance(
            symbol, series, session_date, benchmark_by_date, benchmark_source,
            sector_name, sector_by_date, sector_source, thresholds))

    snapshot.results.sort(key=lambda r: r.instrument)
    return snapshot


def _skip_reason(series: list | None, session_date: dt.date) -> str | None:
    if not series or len(series) < 2:
        return "no price series available"
    if series[-1]["date"] != session_date:
        return (f"no current session for {session_date} (last available session: "
               f"{series[-1]['date']}) - suspended, stale, or incomplete current data")
    return None


def _build_relative_performance(symbol: str, series: list, session_date: dt.date,
                                benchmark_by_date: dict, benchmark_source: str,
                                sector_name: str | None, sector_by_date: dict | None,
                                sector_source: str | None,
                                thresholds: RelativePerformanceThresholds) -> RelativePerformance:
    data_quality: list = []
    dates = [p["date"] for p in series]
    if len(dates) != len(set(dates)):
        data_quality.append("duplicate session dates detected in input series")

    stock_returns, market_returns, market_rel = {}, {}, {}
    sector_returns, sector_rel = {}, {}
    supporting_dates: set = set()

    for p in WINDOWS:
        ret, prior_date, current_date = _return_pct(series, p)
        stock_returns[p] = ret
        if ret is None:
            data_quality.append(f"{p}D unavailable: fewer than {p + 1} stock sessions "
                               f"({len(series)} available)")
            continue
        supporting_dates.update((prior_date, current_date))

        bench_ret = _benchmark_return_between(benchmark_by_date, current_date, prior_date)
        market_returns[p] = bench_ret
        if bench_ret is None:
            data_quality.append(f"{p}D market-relative unavailable: benchmark missing a "
                               f"session on {prior_date} or {current_date}")
        else:
            market_rel[p] = ret - bench_ret

        if sector_name is None:
            continue
        if sector_by_date is None:
            continue
        sec_ret = _benchmark_return_between(sector_by_date, current_date, prior_date)
        sector_returns[p] = sec_ret
        if sec_ret is None:
            data_quality.append(f"{p}D sector-relative unavailable: sector benchmark for "
                               f"'{sector_name}' missing a session on {prior_date} or "
                               f"{current_date}")
        else:
            sector_rel[p] = ret - sec_ret

    if sector_name is None:
        data_quality.append("sector-relative unavailable: no sector mapping supplied for "
                           "this symbol")
    elif sector_by_date is None:
        data_quality.append(f"sector-relative unavailable: no benchmark series supplied for "
                           f"sector '{sector_name}'")

    persistence_state = _classify_persistence(market_rel.get(5), market_rel.get(20), thresholds)
    relative_shift_pp = (market_rel[5] - market_rel[20]
                         if market_rel.get(5) is not None and market_rel.get(20) is not None
                         else None)

    return RelativePerformance(
        instrument=symbol, session_date=session_date,
        stock_return_1d=stock_returns.get(1), stock_return_5d=stock_returns.get(5),
        stock_return_20d=stock_returns.get(20),
        market_return_1d=market_returns.get(1), market_return_5d=market_returns.get(5),
        market_return_20d=market_returns.get(20),
        market_relative_1d_pp=market_rel.get(1), market_relative_5d_pp=market_rel.get(5),
        market_relative_20d_pp=market_rel.get(20),
        sector=sector_name, sector_return_1d=sector_returns.get(1),
        sector_return_5d=sector_returns.get(5), sector_return_20d=sector_returns.get(20),
        sector_relative_1d_pp=sector_rel.get(1), sector_relative_5d_pp=sector_rel.get(5),
        sector_relative_20d_pp=sector_rel.get(20),
        persistence_state=persistence_state, relative_shift_pp=relative_shift_pp,
        supporting_session_dates=sorted(supporting_dates),
        benchmark_source=benchmark_source, sector_source=sector_source if sector_name else None,
        data_quality=data_quality)


def _return_pct(series: list, sessions_ago: int):
    """`(current_close / close_sessions_ago - 1) * 100`, needing `sessions_ago + 1` closes.

    Returns `(return_pct, prior_date, current_date)`, or `(None, None, None)` when the series
    is too short or either close is missing/non-positive (a suspended/invalid session must
    never silently produce a return).
    """
    if len(series) < sessions_ago + 1:
        return None, None, None
    current, prior = series[-1], series[-(sessions_ago + 1)]
    current_close, prior_close = current.get("close"), prior.get("close")
    if current_close is None or prior_close is None or current_close <= 0 or prior_close <= 0:
        return None, None, None
    return (current_close / prior_close - 1) * 100, prior["date"], current["date"]


def _benchmark_return_between(by_date: dict, current_date: dt.date, prior_date: dt.date):
    """Benchmark return over the EXACT two calendar dates the stock's own window used - never
    the benchmark's own P-sessions-ago count, which could silently point at a different pair of
    sessions than the ones being judged (packet spec section 10: no silent date substitution)."""
    current_close, prior_close = by_date.get(current_date), by_date.get(prior_date)
    if current_close is None or prior_close is None or current_close <= 0 or prior_close <= 0:
        return None
    return (current_close / prior_close - 1) * 100


def _classify_persistence(five_day_pp: float | None, twenty_day_pp: float | None,
                          thresholds: RelativePerformanceThresholds) -> RelativePersistenceState | None:
    """`None` when either window is unavailable - persistence is a statement about BOTH windows
    agreeing, and cannot be made from one alone. Otherwise: both windows exceed the threshold in
    the same direction -> persistent; both within the threshold band -> neutral; anything else
    (opposite signs, or one large / one small) -> mixed. A one-day move never enters this
    classification - only 5D and 20D do."""
    if five_day_pp is None or twenty_day_pp is None:
        return None
    t = thresholds.persistence_threshold_pp
    if five_day_pp > t and twenty_day_pp > t:
        return RelativePersistenceState.PERSISTENT_POSITIVE
    if five_day_pp < -t and twenty_day_pp < -t:
        return RelativePersistenceState.PERSISTENT_NEGATIVE
    if abs(five_day_pp) <= t and abs(twenty_day_pp) <= t:
        return RelativePersistenceState.NEUTRAL
    return RelativePersistenceState.MIXED


def _series_to_close_by_date(series: list | None) -> dict:
    if not series:
        return {}
    return {p["date"]: p["close"] for p in series if p.get("close") is not None}


__all__ = ["scan_relative_performance_universe", "WINDOWS"]

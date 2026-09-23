"""Shared read-through OHLCV acquisition for the Market Intelligence Radar (Phase 4.2 Packet 5.3).

Packet 5.2 made `market_ohlcv.db` a write-through side effect of the technical-structure
detector's own Yahoo fetch, but nothing read it back - RVOL, technical structure and relative
performance each still triggered their own independent Yahoo bulk download every run. This
module is the ONE shared acquisition/read-through boundary all three now sit behind:

    requested universe/session
            |
            v
    OHLCVService.load_universe
            |
    check market_ohlcv.db -> complete enough for every symbol? --yes--> shared dataset,
            |no                                                          0 Yahoo calls
            v
    fetch ONLY the missing tail/symbols from Yahoo (the EXISTING
    market.get_universe_technical_series seam, which already validates and writes through -
    not duplicated here) -> re-read the merged (old + new) history from the store
            |
            v
    UniverseOHLCVDataset --> radar.volume (via relative_volume_rows_from_dataset)
                         --> radar.technical.scan_technical_universe (series_by_symbol directly)
                         --> radar.relative.scan_relative_performance_universe (series_by_symbol directly)

See docs/OHLCV_STORE.md for the warm/cold/partial-cache behaviour and the exact lookback
requirement this implements.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import config
import market
from storage.ohlcv_models import QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path

from . import session_alignment

# The true maximum lookback any of the three Packet 5.3 detector families needs - discovered
# from their own thresholds (radar/thresholds.py, radar/relative.py), not guessed:
#   - RVOL (market.RELATIVE_VOLUME_LOOKBACK):        20 prior sessions + current = 21
#   - Technical 50-session range/SMA50 (TechnicalThresholds.range_window_long/sma_long):
#                                                     50 prior sessions + current = 51
#   - Technical RANGE_COMPRESSION (TechnicalThresholds.compression_window=5,
#     .compression_lookback_windows=10): the recent 5-session window PLUS 10 prior
#     non-overlapping 5-session windows = 5 * (10 + 1) = 55 sessions - the LARGEST of the
#     three families' requirements, so it is what this service targets.
#   - Relative performance (radar.relative.WINDOWS = (1, 5, 20)): 20 prior + current = 21
# A symbol with fewer sessions than this is not dropped outright - every detector already
# degrades gracefully on a shorter series (see market.MIN_TECHNICAL_SESSIONS=21, the absolute
# floor even the 20-session range detector needs) - REQUIRED_LOOKBACK_SESSIONS only decides
# when the cache is treated as "complete enough" to skip a Yahoo call.
REQUIRED_LOOKBACK_SESSIONS = 55

# How far back the store is queried to decide whether a symbol's cache is already sufficient.
# Generous on purpose: REQUIRED_LOOKBACK_SESSIONS trading sessions spans roughly 90 calendar
# days even accounting for weekends/holidays, so 400 days leaves wide headroom - a real gap
# must never be mistaken for "the query window itself wasn't wide enough".
STORE_QUERY_WINDOW_DAYS = 400

# Calendar-window `period` strings passed to the EXISTING `market.get_universe_technical_series`
# seam (which already fetches, validates and writes through - deliberately not duplicated here,
# see docs/OHLCV_STORE.md "Existing write-through").
#   - "tail" symbols already have usable history in the store, just missing the latest
#     session(s): a short trailing window is enough to bridge the gap without re-downloading
#     everything.
#   - "cold" symbols have no usable history at all, or too little to ever reach
#     REQUIRED_LOOKBACK_SESSIONS: a window comfortably above REQUIRED_LOOKBACK_SESSIONS trading
#     sessions (~55), but far short of the unconditional "1y" the technical detector used to
#     request for every symbol on every run regardless of what was already cached.
TAIL_FETCH_PERIOD = "1mo"
COLD_FETCH_PERIOD = "6mo"

_SKIP_FETCH_FAILED = "fetch_failed"
_SKIP_NO_RECAP_ROW = "no_recap_row"
_SKIP_DATE_GAP = "date_gap"
_SKIP_INSUFFICIENT_HISTORY = "insufficient_technical_history"


@dataclass
class CoverageDiagnostics:
    """Operational telemetry for one `load_universe` call - not publication content."""
    requested_symbols: int = 0
    cache_complete: int = 0
    cache_partial: int = 0
    cache_miss: int = 0
    freshly_fetched: int = 0
    usable_symbols: int = 0
    skipped_symbols: int = 0
    network_call_count: int = 0
    bars_loaded_from_store: int = 0
    bars_written: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class UniverseOHLCVDataset:
    """The one dataset RVOL, technical and relative-performance detection all consume.

    `series_by_symbol[symbol]` is oldest-first OHLCV session history ending at `session_date`
    inclusive: `[{"date", "open", "high", "low", "close", "volume"}, ...]` - the same shape
    `market.get_universe_technical_series` already returns (extended with `open`/`volume` so
    RVOL can be computed from it too - see `relative_volume_rows_from_dataset`). Only symbols
    that passed the same recap/prev-date alignment and minimum-history checks
    `market.get_universe_technical_series` already enforces are present here; everything else
    is in `skipped_symbols` with the same deterministic reason vocabulary.
    """
    session_date: dt.date
    series_by_symbol: dict = field(default_factory=dict)
    requested_symbols: list = field(default_factory=list)
    available_symbols: list = field(default_factory=list)
    skipped_symbols: dict = field(default_factory=dict)
    cache_served_symbols: set = field(default_factory=set)
    freshly_fetched_symbols: set = field(default_factory=set)
    missing_sessions: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    source: str = "yahoo"
    coverage: CoverageDiagnostics = field(default_factory=CoverageDiagnostics)


def apply_session_spine(by_symbol: dict, spine: frozenset | None) -> int:
    """In-place: drop any stored bar whose date falls WITHIN `spine`'s covered range but is not
    itself a spine date - a confirmed non-trading placeholder (Phase 4.2 Packet 5.3E) - from
    every symbol's row list in `by_symbol` (`{symbol: [OHLCVRow, ...]}`).

    This must run BEFORE any completeness/date-gap check: a holiday-placeholder row sitting
    between two genuine sessions otherwise looks, to a positional "is the row before the latest
    one dated `prev_date`" check, exactly like a missing real session - see
    docs/OHLCV_STORE.md's Packet 5.3E section. Reuses `radar.session_alignment.
    align_series_to_spine`'s exact rule (never a second implementation of it), applied to
    `OHLCVRow.session_date` instead of a dict's `"date"` key. `spine=None`/empty is a no-op -
    "no spine available" must never be treated as "everything is a placeholder".

    Returns the total number of rows dropped, for caller diagnostics/warnings.
    """
    if not spine:
        return 0
    dropped_total = 0
    for symbol, rows in list(by_symbol.items()):
        filtered, dropped = session_alignment.align_series_to_spine(rows, spine, date_key="session_date")
        by_symbol[symbol] = filtered
        dropped_total += dropped
    return dropped_total


def load_universe(universe: dict, session_date: dt.date, prev_date: dt.date, *,
                  required_lookback: int = REQUIRED_LOOKBACK_SESSIONS,
                  out_dir: str | None = None, source: str = "yahoo",
                  store: OHLCVStore | None = None,
                  spine: frozenset | None = None) -> UniverseOHLCVDataset:
    """Load one shared `UniverseOHLCVDataset` for `universe` as of `session_date`.

    Warm cache (every symbol already sufficiently covered through `session_date`): zero Yahoo
    calls. Cold/partial cache: fetches only the symbols/tail actually missing, via the existing
    `market.get_universe_technical_series` seam (which validates and writes through on its
    own), then re-reads the merged result so a "tail" symbol's returned series is its FULL
    history, not just the short window the top-up fetch covered.

    `spine` (Phase 4.2 Packet 5.3E): the canonical NSE trading-session spine
    (`radar.session_alignment.canonical_session_spine`), when the caller already has one (e.g.
    from a benchmark fetch it made before calling this). When given, every stored bar is
    filtered against it (`apply_session_spine`) BEFORE completeness/date-gap validation, so a
    provider holiday-placeholder row can never manufacture a false `date_gap`. Omitting it
    (the default) preserves this function's pre-5.3E behaviour exactly - a caller with no spine
    yet available still gets the same completeness/date-gap checks as before, and
    `radar.session_alignment.align_dataset_to_spine` remains available as an after-the-fact
    filter for whichever path (this one, or the Yahoo-fallback below) a caller applies it to.

    `store` may be supplied by a caller that wants to reuse one open connection across several
    calls (e.g. a test, or a future multi-universe run); otherwise one is opened and closed
    here. A store that cannot be opened or read never fails this call - it falls back to a
    single direct Yahoo bulk fetch, exactly like Packet 5.2's own write-through failure
    isolation (docs/OHLCV_STORE.md).
    """
    symbols = list(universe)
    dataset = UniverseOHLCVDataset(session_date=session_date, requested_symbols=symbols,
                                   source=source)
    dataset.coverage.requested_symbols = len(symbols)

    owns_store = False
    if store is None:
        try:
            store = OHLCVStore(default_db_path(out_dir if out_dir is not None else config.OUT_DIR))
            owns_store = True
        except Exception as exc:
            print(f"[radar.ohlcv_service] store unavailable ({exc}); falling back to direct "
                 "Yahoo acquisition")
            dataset.warnings.append(f"OHLCV store unavailable: {exc}")
            return _fallback_full_fetch(universe, session_date, prev_date, dataset)

    try:
        _load_with_store(store, universe, session_date, prev_date, required_lookback, dataset, spine)
    except Exception as exc:
        print(f"[radar.ohlcv_service] store read failed ({exc}); falling back to direct Yahoo "
             "acquisition")
        dataset.warnings.append(f"OHLCV store read failed: {exc}")
        dataset.series_by_symbol.clear()
        dataset.skipped_symbols.clear()
        dataset.cache_served_symbols.clear()
        dataset.freshly_fetched_symbols.clear()
        _fallback_full_fetch(universe, session_date, prev_date, dataset)
    finally:
        if owns_store:
            store.close()
    return dataset


def _load_with_store(store: OHLCVStore, universe: dict, session_date: dt.date,
                     prev_date: dt.date, required_lookback: int,
                     dataset: UniverseOHLCVDataset, spine: frozenset | None = None) -> None:
    symbols = list(universe)
    start = session_date - dt.timedelta(days=STORE_QUERY_WINDOW_DAYS)
    rows = store.get_range(symbols, start, session_date, source=dataset.source)
    dataset.coverage.bars_loaded_from_store += len(rows)

    by_symbol: dict = {}
    for r in rows:
        if r.quality_status != QualityStatus.OK:
            continue
        by_symbol.setdefault(r.symbol, []).append(r)

    # Packet 5.3E: filter confirmed non-trading placeholder rows OUT before completeness/
    # date-gap validation runs - a holiday row sitting between two genuine sessions otherwise
    # looks, to the positional "is the row before the latest dated prev_date" check below,
    # exactly like a missing real session.
    dropped = apply_session_spine(by_symbol, spine)
    if dropped:
        dataset.warnings.append(f"session spine filter: {dropped} provider row(s) for confirmed "
                                f"non-trading dates excluded before completeness validation")

    complete, tail_bucket, cold_bucket = [], [], []
    for s in symbols:
        ok_rows = sorted(by_symbol.get(s, []), key=lambda r: r.session_date)
        if not ok_rows:
            cold_bucket.append(s)
        elif ok_rows[-1].session_date < session_date:
            tail_bucket.append(s)
        elif ok_rows[-1].session_date == session_date and len(ok_rows) < required_lookback:
            cold_bucket.append(s)
        elif ok_rows[-1].session_date == session_date:
            complete.append(s)
        else:
            # A stored latest session strictly after session_date is unexpected (a caller
            # asking about an older session than what's cached) - never trusted blindly.
            cold_bucket.append(s)

    dataset.coverage.cache_complete = len(complete)
    dataset.coverage.cache_partial = len(tail_bucket)
    dataset.coverage.cache_miss = len(cold_bucket)
    dataset.cache_served_symbols = set(complete)

    for bucket, period in ((tail_bucket, TAIL_FETCH_PERIOD), (cold_bucket, COLD_FETCH_PERIOD)):
        if not bucket:
            continue
        subset = {s: universe[s] for s in bucket}
        try:
            _series, skip_reasons = market.get_universe_technical_series(
                subset, session_date, prev_date, period=period)
        except Exception as exc:
            print(f"[radar.ohlcv_service] Yahoo fetch failed for {len(bucket)} symbols "
                 f"({period}): {exc}")
            dataset.warnings.append(
                f"Yahoo fetch failed for {len(bucket)} symbols (period={period}): {exc}")
            for s in bucket:
                dataset.skipped_symbols[s] = _SKIP_FETCH_FAILED
            continue
        dataset.coverage.network_call_count += 1
        for s, reason in skip_reasons.items():
            dataset.skipped_symbols[s] = reason
        dataset.freshly_fetched_symbols.update(s for s in bucket if s not in skip_reasons)

    fetched_all = tail_bucket + cold_bucket
    if fetched_all:
        # Re-read so a "tail" symbol's final series is its FULL merged history (old rows
        # already in the store + whatever the top-up fetch above just wrote through), not just
        # the short window that fetch itself covered.
        refreshed = store.get_range(fetched_all, start, session_date, source=dataset.source)
        dataset.coverage.bars_loaded_from_store += len(refreshed)
        for s in fetched_all:
            by_symbol.pop(s, None)
        for r in refreshed:
            if r.quality_status != QualityStatus.OK:
                continue
            by_symbol.setdefault(r.symbol, []).append(r)
        refreshed_by_symbol = {s: by_symbol[s] for s in fetched_all if s in by_symbol}
        dropped = apply_session_spine(refreshed_by_symbol, spine)
        if dropped:
            dataset.warnings.append(f"session spine filter: {dropped} provider row(s) for "
                                    "confirmed non-trading dates excluded from freshly-fetched "
                                    "symbols before completeness validation")

    for s in symbols:
        ok_rows = sorted(by_symbol.get(s, []), key=lambda r: r.session_date)
        _finalize_symbol(dataset, s, ok_rows, session_date, prev_date)

    dataset.available_symbols = sorted(dataset.series_by_symbol)
    dataset.coverage.freshly_fetched = len(dataset.freshly_fetched_symbols)
    dataset.coverage.usable_symbols = len(dataset.series_by_symbol)
    dataset.coverage.skipped_symbols = len(dataset.skipped_symbols)


def _finalize_symbol(dataset: UniverseOHLCVDataset, symbol: str, ok_rows: list,
                     session_date: dt.date, prev_date: dt.date) -> None:
    """Apply the SAME alignment/minimum-history checks `market.get_universe_technical_series`
    already enforces, uniformly, regardless of whether a symbol's final series came straight
    from the store or from a fresh fetch this call made - a warm-cache symbol must be exactly
    as trustworthy as a freshly-fetched one."""
    if symbol in dataset.skipped_symbols:
        return  # a fetch this call already recorded a definitive, more specific reason
    if not ok_rows or ok_rows[-1].session_date != session_date:
        dataset.skipped_symbols[symbol] = _SKIP_NO_RECAP_ROW
        return
    if len(ok_rows) >= 2 and ok_rows[-2].session_date != prev_date:
        dataset.skipped_symbols[symbol] = _SKIP_DATE_GAP
        return
    if len(ok_rows) < market.MIN_TECHNICAL_SESSIONS:
        dataset.skipped_symbols[symbol] = _SKIP_INSUFFICIENT_HISTORY
        return
    dataset.series_by_symbol[symbol] = [
        {"date": r.session_date, "open": r.open, "high": r.high, "low": r.low,
         "close": r.close, "volume": r.volume}
        for r in ok_rows]


def _fallback_full_fetch(universe: dict, session_date: dt.date, prev_date: dt.date,
                         dataset: UniverseOHLCVDataset) -> UniverseOHLCVDataset:
    """Store unavailable/unreadable: fall back to a single direct Yahoo bulk fetch via the
    existing seam (which still attempts its own best-effort write-through, so a store that
    recovers by the next run picks the data back up) - a broken cache must never stop the
    Radar from running at all (Packet 5.2's failure-isolation guarantee, extended here)."""
    try:
        series, skip_reasons = market.get_universe_technical_series(
            universe, session_date, prev_date, period=COLD_FETCH_PERIOD)
    except Exception as exc:
        print(f"[radar.ohlcv_service] Yahoo fallback fetch failed: {exc}")
        dataset.warnings.append(f"Yahoo fallback fetch failed: {exc}")
        for s in universe:
            dataset.skipped_symbols[s] = _SKIP_FETCH_FAILED
        dataset.coverage.cache_miss = len(universe)
        dataset.coverage.skipped_symbols = len(dataset.skipped_symbols)
        return dataset

    dataset.coverage.network_call_count += 1
    for s, rows in series.items():
        dataset.series_by_symbol[s] = [
            {"date": r["date"], "open": r.get("open"), "high": r["high"], "low": r["low"],
             "close": r["close"], "volume": r.get("volume")}
            for r in rows]
    dataset.skipped_symbols.update(skip_reasons)
    dataset.freshly_fetched_symbols.update(series)
    dataset.available_symbols = sorted(dataset.series_by_symbol)
    dataset.coverage.cache_miss = len(universe)
    dataset.coverage.freshly_fetched = len(dataset.freshly_fetched_symbols)
    dataset.coverage.usable_symbols = len(dataset.series_by_symbol)
    dataset.coverage.skipped_symbols = len(dataset.skipped_symbols)
    return dataset


def relative_volume_rows_from_dataset(dataset: UniverseOHLCVDataset, universe: dict,
                                      lookback: int = market.RELATIVE_VOLUME_LOOKBACK):
    """RVOL rows (`{"symbol", "name", "volx"}`, the exact shape
    `market.get_universe_relative_volume` returns) computed from the shared dataset's own
    volume series, via the SAME `market.relative_volume()` formula - never a second
    implementation of the metric, only a different acquisition source for its inputs.

    Returns `(rows, skip_reasons)`. A symbol the dataset already excluded (no recap row, date
    gap, insufficient technical history, ...) carries that same reason forward rather than a
    generic one, so a caller cannot see two different explanations for the same symbol.
    """
    import pandas as pd

    rows, skip_reasons = [], {}
    for symbol, name in universe.items():
        if symbol in dataset.skipped_symbols:
            skip_reasons[symbol] = dataset.skipped_symbols[symbol]
            continue
        series = dataset.series_by_symbol.get(symbol)
        if not series:
            skip_reasons[symbol] = _SKIP_NO_RECAP_ROW
            continue
        volumes = pd.Series([row.get("volume") for row in series])
        volx = market.relative_volume(volumes, lookback=lookback)
        if volx is None:
            skip_reasons[symbol] = "insufficient_relative_volume_history"
            continue
        rows.append({"symbol": symbol, "name": name, "volx": volx})
    return rows, skip_reasons


__all__ = ["UniverseOHLCVDataset", "CoverageDiagnostics", "load_universe", "apply_session_spine",
          "relative_volume_rows_from_dataset", "REQUIRED_LOOKBACK_SESSIONS",
          "STORE_QUERY_WINDOW_DAYS", "TAIL_FETCH_PERIOD", "COLD_FETCH_PERIOD"]

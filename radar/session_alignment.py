"""Canonical NSE trading-session alignment for the Market Intelligence Radar (Phase 4.2 Packet
5.3C).

Packet 5.3B fixed acceptance/look-ahead for the RECAP session itself (a symbol's own later
sessions must never invalidate, or leak into, a calculation for an earlier recap date). It also
surfaced a second, distinct defect: a per-stock Yahoo series can carry a row for a calendar date
NSE never actually traded on at all - verified 2026-09-23 with a direct fetch: `^NSEI`'s own
history has NO row for 2026-09-14 (a genuine NSE holiday - the index jumps straight from 09-11 to
09-15), while ~198/200 NIFTY200 stocks' own Yahoo series carry a spurious row for that exact
date (Close identical to 09-11's, a DIFFERENT volume - a placeholder, not a real trading
session). Every session-COUNT-based calculation silently consumed that fake row as if it were a
real one: RVOL's prior-20-session mean volume, SMA20/50, the 20/50-session range, compression's
5-session windows, and relative-performance's "P sessions ago" POSITIONAL lookup (`radar.
relative._return_pct`) all shift by one real session for any window that spans a holiday like
this.

This module fetches nothing and is not a provider. It filters ALREADY-ACQUIRED per-symbol
series (`radar.ohlcv_service.UniverseOHLCVDataset.series_by_symbol`) against a canonical spine
of genuine trading-session dates, reusing the market benchmark series the Radar already fetches
for relative-performance (`radar.relative_acquisition.build_market_benchmark_series`) - no new
network call, no new provider, no change to any detector's own mathematics. Raw bars in
`market_ohlcv.db` are never touched, deleted, or rewritten - this is a read-time filter over the
in-memory series a Radar run hands to its detectors, preserving Packet 5.2's "raw provider
history stays raw, untouched by anything downstream of acquisition" guarantee.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import market
from core.trading_calendar import SessionCalendar


def canonical_session_spine(benchmark_series: list) -> frozenset:
    """The canonical NSE trading sessions across the market benchmark's own covered range.

    Data-reliability patch (holiday / session alignment): this used to be the benchmark's bar
    dates alone. That spine is missing a REAL session - Yahoo's ^NSEI (and every other Indian
    index feed, even BSE's ^BSESN) has no bar for Tue 2026-09-22, which NSE's own holiday list
    confirms was a trading day and on which every stock has a genuine bar. It is now
    `core.trading_calendar.SessionCalendar`: benchmark bar dates UNION the weekdays NSE's
    published holiday list does not close, inside [first, last] benchmark date. Years the list
    doesn't cover fall back to the benchmark bar dates exactly as before. The returned set can
    therefore contain a session the benchmark itself has no bar for - callers that need the
    benchmark's close on a date must still look it up by date and treat absence as absence.

    Original rationale, still true of the benchmark's bars:

    An INDEX's own history is not subject to the per-stock holiday-placeholder artifact
    described above - an index is computed only from constituents that actually traded that
    session, so a date NSE didn't trade on simply has no bar at all (confirmed directly against
    real data, see module docstring). This is the smallest reliable canonical NSE
    trading-session spine already available to the Radar: it reuses the SAME benchmark series
    already fetched once per run for relative-performance detection, never a second/new
    acquisition.
    """
    index_dates = frozenset(row["date"] for row in benchmark_series if row.get("date") is not None)
    if not index_dates:
        return frozenset()
    cal = SessionCalendar.from_index_dates(index_dates)
    return frozenset(cal.sessions_between(min(index_dates), max(index_dates)))


def canonical_session_list(benchmark_series: list, end: dt.date | None = None) -> list:
    """`canonical_session_spine` as an oldest-first list (optionally cut at `end` inclusive) -
    the one way a spine LIST (for `spine[idx - 1]` previous-session lookups) is built."""
    spine = sorted(canonical_session_spine(benchmark_series))
    return [d for d in spine if end is None or d <= end]


def benchmark_missing_sessions(benchmark_series: list) -> list:
    """Canonical sessions the benchmark has no bar for (e.g. 2026-09-22) - audit only."""
    index_dates = frozenset(row["date"] for row in benchmark_series if row.get("date") is not None)
    return SessionCalendar.from_index_dates(index_dates).benchmark_gaps()


@dataclass
class AlignmentReport:
    """Diagnostics for one `align_dataset_to_spine` call - operational telemetry, not
    publication content."""
    spine_size: int = 0
    spine_available: bool = False
    symbols_filtered: int = 0
    rows_dropped: int = 0
    newly_skipped: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def align_series_to_spine(series: list, spine: frozenset, date_key: str = "date") -> tuple[list, int]:
    """Drop any row whose date falls WITHIN the spine's own covered range but is NOT itself a
    spine date - a genuine non-trading day the stock's own feed nonetheless carries a row for.

    A row dated OUTSIDE the spine's covered range (before its earliest date, or after its
    latest) is simply UNKNOWN to the benchmark, not proven invalid, and is kept rather than
    guessed at - filtering a confirmed holiday placeholder is allowed; inventing an opinion
    about a date the benchmark never covered is not (Section 7/8: never interpolate, never
    drop on absence of evidence alone).

    `date_key` names the attribute/key each row carries its date under - `"date"` (the default)
    for the `{"date": ..., "close": ...}` dict shape most callers use; `radar.ohlcv_service`
    passes `"session_date"` to filter `storage.ohlcv_repository.OHLCVRow` objects directly
    (Phase 4.2 Packet 5.3E) - the exact same rule, never a second implementation of it, applied
    to a plain attribute instead of a dict key.
    """
    if not spine:
        return list(series), 0
    spine_min, spine_max = min(spine), max(spine)
    aligned, dropped = [], 0
    for row in series:
        d = row[date_key] if isinstance(row, dict) else getattr(row, date_key)
        if spine_min <= d <= spine_max and d not in spine:
            dropped += 1
            continue
        aligned.append(row)
    return aligned, dropped


def align_dataset_to_spine(dataset, benchmark_series: list) -> AlignmentReport:
    """Filter every symbol's series in `dataset.series_by_symbol`
    (`radar.ohlcv_service.UniverseOHLCVDataset`) against
    `canonical_session_spine(benchmark_series)`, in place.

    A symbol that no longer ends at `dataset.session_date` after filtering, or that no longer
    has at least `market.MIN_TECHNICAL_SESSIONS` valid sessions, moves from `series_by_symbol`
    into `skipped_symbols` with the same deterministic reason vocabulary
    `radar.ohlcv_service`/`market.py` already use - never silently truncated in place and left
    looking usable. `dataset.session_date` itself is, by construction, a real trading session
    (it is how `market.get_market()` determined `recap_date`), so filtering removing it should
    not happen in practice; the check exists as defense in depth.

    Never fetches, never invents a bar, never touches `market_ohlcv.db` - purely an in-memory
    filter over series the caller already acquired. If `benchmark_series` is empty/unavailable,
    no spine can be built at all: every series is left exactly as acquired (never filtered on
    no information) and a warning is recorded on `dataset.warnings` - the same "degrade, never
    guess" pattern the rest of this pipeline uses for a missing benchmark.
    """
    spine = canonical_session_spine(benchmark_series)
    report = AlignmentReport(spine_size=len(spine), spine_available=bool(spine))
    if not spine:
        dataset.warnings.append("session alignment skipped: benchmark series unavailable, no "
                                "canonical trading-session spine to align against")
        return report

    for symbol in list(dataset.series_by_symbol):
        series = dataset.series_by_symbol[symbol]
        aligned, dropped = align_series_to_spine(series, spine)
        if dropped:
            report.symbols_filtered += 1
            report.rows_dropped += dropped

        if not aligned or aligned[-1]["date"] != dataset.session_date:
            reason = "no_recap_row"
        elif len(aligned) < market.MIN_TECHNICAL_SESSIONS:
            reason = "insufficient_technical_history"
        else:
            dataset.series_by_symbol[symbol] = aligned
            continue

        del dataset.series_by_symbol[symbol]
        dataset.skipped_symbols[symbol] = reason
        report.newly_skipped[symbol] = reason

    dataset.available_symbols = sorted(dataset.series_by_symbol)
    return report


__all__ = ["canonical_session_spine", "canonical_session_list", "benchmark_missing_sessions",
          "align_series_to_spine", "align_dataset_to_spine", "AlignmentReport"]

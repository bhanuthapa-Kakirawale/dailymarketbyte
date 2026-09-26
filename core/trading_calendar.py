"""Canonical NSE trading-session calendar (data-reliability patch: holiday / session alignment).

Provider rows never define trading sessions. This module answers one question - "did NSE's
equity segment hold a session on date D?" - from two pieces of evidence, and nothing else:

  1. NSE's own published equity-segment trading-holiday list (`NSE_TRADING_HOLIDAYS`, copied
     from https://www.nseindia.com/api/holiday-master?type=trading), plus the special sessions
     NSE held on a weekend/listed holiday (`SPECIAL_SESSIONS`: Budget-day Saturdays, Muhurat).
  2. The dates the benchmark INDEX's own series carries (`index_dates`, e.g. `^NSEI`). An index
     is computed only from constituents that actually traded, so it has never been seen to
     print a bar on a non-trading day - an index bar is positive evidence of a session.

Why both. The Radar's original spine was the `^NSEI` bar dates alone. Verified 2026-09-25
against the local OHLCV store (247 sessions) and NSE's holiday master: every weekday `^NSEI`
skips is an official NSE holiday EXCEPT Tue 2026-09-22, a real trading session (not on NSE's
list; every NIFTY200 stock has a genuine bar that day - real OHLC ranges, real volume) that
Yahoo's Indian index feeds (^NSEI, ^NSEBANK, ^CNXIT, ^INDIAVIX and even BSE's ^BSESN) all
lack. The index spine alone would therefore have discarded a real session's stock rows and
turned every 23 Sep move into an unlabelled two-session change. Conversely the per-stock
feeds carry fake placeholder rows (O=H=L=C = previous close, volume 0) on genuine holidays
(2026-05-01, 05-28, 06-26, 09-14) - which the holiday list rejects.

Rule (`SessionCalendar.status`), in order:
  - a listed special session, or a date the index printed a bar for       -> SESSION
  - a year the holiday list covers: weekday and not a listed holiday      -> SESSION,
                                     otherwise                             -> NON_SESSION
  - a year the list does NOT cover: fall back to the index-bar spine (the pre-patch
    behaviour) - inside the index's covered range a date without a bar is NON_SESSION,
    outside it the answer is UNKNOWN and callers keep the row rather than guess.

The list must be extended each December when NSE publishes the next year's holidays;
until then `covers()` is False for that year and the calendar degrades to the index spine
(recorded as `calendar_covered=False` in every alignment audit), never to a guess.

Pure: no fetching, no clock, no provider. Nothing here writes or rewrites raw provider data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

SESSION = "SESSION"
NON_SESSION = "NON_SESSION"
UNKNOWN = "UNKNOWN"


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


# NSE equity-segment trading holidays. Weekend-dated entries NSE also lists (e.g. 15 Aug 2026,
# a Saturday) are kept for completeness; they change nothing since weekends are closed anyway.
# 2026: NSE holiday-master API, retrieved 2026-09-25. 2025: NSE's published 2025 list,
# cross-checked 2026-09-25 against every 2025 weekday Yahoo's ^NSEI has no bar for (exact match).
NSE_TRADING_HOLIDAYS: dict[int, frozenset] = {
    2025: frozenset(_d(x) for x in (
        "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18",
        "2025-05-01", "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22",
        "2025-11-05", "2025-12-25")),
    2026: frozenset(_d(x) for x in (
        "2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21", "2026-03-26",
        "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
        "2026-08-15", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10",
        "2026-11-24", "2026-12-25")),
}

# Sessions NSE held on a weekend or a listed holiday. Muhurat trading is announced on the
# Diwali Laxmi Pujan holiday itself (asterisked in NSE's list).
SPECIAL_SESSIONS: dict[dt.date, str] = {
    _d("2025-02-01"): "Union Budget special session (Saturday)",
    _d("2025-10-21"): "Muhurat trading (Diwali Laxmi Pujan)",
    _d("2026-11-08"): "Muhurat trading (Diwali Laxmi Pujan)",
}

# How far `previous_session` walks back before giving up: longer than any NSE closure run.
_MAX_WALK_DAYS = 15


@dataclass(frozen=True)
class SessionCalendar:
    """The canonical session rule, optionally informed by a benchmark index's own bar dates."""
    index_dates: frozenset = field(default_factory=frozenset)
    holidays: dict = field(default_factory=lambda: NSE_TRADING_HOLIDAYS)
    special: dict = field(default_factory=lambda: SPECIAL_SESSIONS)

    @classmethod
    def from_index_dates(cls, dates) -> "SessionCalendar":
        return cls(index_dates=frozenset(as_date(d) for d in dates if d is not None))

    def covers(self, d: dt.date) -> bool:
        return d.year in self.holidays

    def status(self, d) -> str:
        d = as_date(d)
        if d in self.special or d in self.index_dates:
            return SESSION
        if self.covers(d):
            return SESSION if d.weekday() < 5 and d not in self.holidays[d.year] else NON_SESSION
        if self.index_dates and min(self.index_dates) <= d <= max(self.index_dates):
            return NON_SESSION
        return UNKNOWN

    def is_session(self, d) -> bool | None:
        s = self.status(d)
        return None if s == UNKNOWN else s == SESSION

    def previous_session(self, d) -> dt.date | None:
        """The canonical session immediately before `d` - never "calendar day - 1", never the
        previous row of some provider's series. None if it can't be established."""
        cur = as_date(d)
        for _ in range(_MAX_WALK_DAYS):
            cur -= dt.timedelta(days=1)
            s = self.status(cur)
            if s == SESSION:
                return cur
            if s == UNKNOWN:
                return None
        return None

    def sessions_between(self, start, end) -> list:
        """Canonical sessions in [start, end], oldest first (UNKNOWN dates excluded)."""
        cur, end = as_date(start), as_date(end)
        out = []
        while cur <= end:
            if self.status(cur) == SESSION:
                out.append(cur)
            cur += dt.timedelta(days=1)
        return out

    def benchmark_gaps(self) -> list:
        """Canonical sessions inside the index's own covered range that the index has no bar
        for - e.g. 2026-09-22 for Yahoo's ^NSEI. The stock rows for such a date are real."""
        if not self.index_dates:
            return []
        return [d for d in self.sessions_between(min(self.index_dates), max(self.index_dates))
                if d not in self.index_dates]

    def benchmark_bars_on_listed_holidays(self) -> list:
        """Index bars on a date the holiday list calls closed and no special session explains -
        kept as session evidence, surfaced so the list can be corrected."""
        return sorted(d for d in self.index_dates
                      if self.covers(d) and d not in self.special
                      and (d.weekday() >= 5 or d in self.holidays[d.year]))


def as_date(d) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if hasattr(d, "date") and callable(d.date):     # pandas.Timestamp
        return d.date()
    return dt.date.fromisoformat(str(d)[:10])


# ------------------------------------------------------------------ provider-row alignment
@dataclass
class AlignmentAudit:
    """Why provider rows were or weren't used - operational telemetry, never publication
    content. Accumulates across symbols when one instance is passed to several calls."""
    provider_rows_seen: int = 0
    canonical_rows_used: int = 0
    non_session_rows_ignored: int = 0
    non_session_dates: dict = field(default_factory=dict)     # iso date -> row count
    duplicate_rows_removed: int = 0
    out_of_order_series: int = 0
    unknown_date_rows_kept: int = 0
    symbols_with_non_session_rows: int = 0
    calendar_covered: bool = True

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["non_session_dates"] = dict(sorted(self.non_session_dates.items()))
        return d


def align_rows(rows: list, calendar: SessionCalendar, date_of=lambda r: r["date"],
               audit: AlignmentAudit | None = None) -> list:
    """Provider rows -> canonical-session rows, oldest first.

    Sorts (a provider can return rows out of order), removes duplicate dates keeping the LAST
    occurrence (the same rule `market._normalize_index` already applies to Yahoo frames),
    and drops every row the calendar calls NON_SESSION. UNKNOWN dates are kept (absence of
    evidence is not proof of a holiday). Never fills a missing session - a gap stays a gap.
    """
    audit = audit if audit is not None else AlignmentAudit()
    audit.provider_rows_seen += len(rows)
    keyed = [(as_date(date_of(r)), i, r) for i, r in enumerate(rows)]
    if any(keyed[i][0] > keyed[i + 1][0] for i in range(len(keyed) - 1)):
        audit.out_of_order_series += 1
    by_date: dict = {}
    for d, i, r in sorted(keyed, key=lambda x: (x[0], x[1])):
        if d in by_date:
            audit.duplicate_rows_removed += 1
        by_date[d] = r
    out, had_non_session = [], False
    for d in sorted(by_date):
        s = calendar.status(d)
        if s == NON_SESSION:
            audit.non_session_rows_ignored += 1
            iso = d.isoformat()
            audit.non_session_dates[iso] = audit.non_session_dates.get(iso, 0) + 1
            had_non_session = True
            continue
        if s == UNKNOWN:
            audit.unknown_date_rows_kept += 1
        if not calendar.covers(d):
            audit.calendar_covered = False
        out.append(by_date[d])
    if had_non_session:
        audit.symbols_with_non_session_rows += 1
    audit.canonical_rows_used += len(out)
    return out


__all__ = ["SESSION", "NON_SESSION", "UNKNOWN", "as_date", "NSE_TRADING_HOLIDAYS", "SPECIAL_SESSIONS",
           "SessionCalendar", "AlignmentAudit", "align_rows"]

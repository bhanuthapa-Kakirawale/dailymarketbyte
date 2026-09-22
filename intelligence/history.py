"""Shared access to canonical history: windows, sessions, eligibility, compatibility.

Every analyser reads history through this module, so three rules that are easy to get subtly
wrong are enforced once rather than six times:

* **The current session is excluded.** History is loaded strictly before the session being
  reported. Comparing today against a window that already contains today quietly flatters
  every percentile and inflates every average.
* **Sessions are trading sessions.** A window of 20 means the last 20 market_dates that
  actually exist in canonical history. Weekends and holidays are not missing sessions - they
  are not sessions.
* **Only eligible facts count.** Conflicted, stale, rejected or AI-only-provisional numbers
  never enter a calculation, per `ELIGIBLE_HISTORICAL_STATUSES`.

Loading is bulk: one query per metric for the whole window, never one per session.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from core import Metric

from .models import ELIGIBLE_HISTORICAL_STATUSES

# How much history to pull in one go. Generous enough for every 20-session metric with room
# for gaps, bounded so the query stays small as the database grows into years.
DEFAULT_WINDOW_SESSIONS = 60


@dataclass
class SessionValue:
    """One metric reading on one trading session, with its provenance."""
    market_date: dt.date
    value: float
    report_id: str
    fact_id: str
    instrument: str
    metadata: dict = field(default_factory=dict)


def _as_date(value) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def _dedupe_by_session(points: list) -> list:
    """One reading per (instrument, session).

    Two reports could describe the same session - a rerun that produced a corrected report,
    say. Counting both would double-weight that day in every average and streak, so the
    first seen wins (queries return newest report first).
    """
    seen, out = set(), []
    for point in points:
        key = (point.instrument, point.market_date)
        if key in seen:
            continue
        seen.add(key)
        out.append(point)
    return out


class HistoricalWindow:
    """Canonical history strictly before one session, loaded once and queried in memory."""

    def __init__(self, history, session_date: dt.date, report_id: str | None = None,
                 window_sessions: int = DEFAULT_WINDOW_SESSIONS, include_demo: bool = False):
        self.history = history
        self.session_date = session_date
        self.report_id = report_id
        self.window_sessions = window_sessions
        self.include_demo = include_demo
        self.warnings: list = []
        self._series: dict = {}
        self._points: dict = {}

    # ------------------------------------------------------------------ loading
    def series(self, metric: Metric, instrument: str | None = None) -> list:
        """Readings for a metric, newest session first, strictly before the current one."""
        key = (metric.value, instrument)
        if key not in self._series:
            self._series[key] = self._load(metric, instrument)
        return self._series[key]

    def _load(self, metric: Metric, instrument: str | None) -> list:
        try:
            facts = self.history.get_recent_facts(
                metric.value, instrument=instrument, before_date=self.session_date,
                statuses=sorted(ELIGIBLE_HISTORICAL_STATUSES),
                limit_sessions=self.window_sessions, include_demo=self.include_demo)
        except Exception as exc:
            raise HistoryUnavailable(f"{type(exc).__name__}: {exc}") from exc

        points = []
        for fact in facts:
            market_date = _as_date(fact.market_date)
            # report_id guard is belt-and-braces: the date filter already excludes today,
            # but a report mis-dated to an earlier session must not sneak into its own window.
            if market_date is None or fact.value is None or fact.report_id == self.report_id:
                continue
            if market_date >= self.session_date:
                continue
            points.append(SessionValue(market_date=market_date, value=float(fact.value),
                                       report_id=fact.report_id, fact_id=fact.fact_id,
                                       instrument=fact.instrument, metadata=fact.metadata))
        return _dedupe_by_session(points)

    def points(self, metric: Metric, instrument: str | None = None) -> list:
        """Readings joined to observation metadata, for metrics whose comparability depends
        on it (relative-volume definition version, mover bucket)."""
        key = (metric.value, instrument)
        if key not in self._points:
            try:
                rows = self.history.get_recent_metric_points(
                    metric.value, instrument=instrument, before_date=self.session_date,
                    statuses=sorted(ELIGIBLE_HISTORICAL_STATUSES),
                    limit_sessions=self.window_sessions, include_demo=self.include_demo)
            except Exception as exc:
                raise HistoryUnavailable(f"{type(exc).__name__}: {exc}") from exc
            self._points[key] = [r for r in rows
                                 if r.report_id != self.report_id
                                 and (_as_date(r.market_date) or self.session_date) < self.session_date]
        return self._points[key]

    # ------------------------------------------------------------------ session accounting
    def sessions(self, metric: Metric, instrument: str | None = None) -> list:
        """Distinct trading sessions available for a metric, newest first."""
        return sorted({p.market_date for p in self.series(metric, instrument)}, reverse=True)

    def available(self, metric: Metric, instrument: str | None = None) -> int:
        return len(self.sessions(metric, instrument))

    def recent(self, metric: Metric, instrument: str | None = None, limit: int | None = None) -> list:
        """The most recent `limit` readings, newest first."""
        values = self.series(metric, instrument)
        return values[:limit] if limit else values

    def depth(self) -> int:
        """Total distinct trading sessions present in canonical history before this one.

        Uses the index move series as the spine: every report has one, so it is the honest
        measure of how much history exists at all.
        """
        return self.available(Metric.INDEX_CHANGE_PCT, "NIFTY 50")

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


class HistoryUnavailable(RuntimeError):
    """Canonical history could not be read. Distinct from "there is not enough of it yet"."""


# --------------------------------------------------------------------- shared calculations
def strength_for(sample_size: int, required: int):
    """Data support for a window, never a probability."""
    from .models import Strength
    if sample_size >= required:
        return Strength.FULL_HISTORY
    if sample_size == 0:
        return Strength.INSUFFICIENT_HISTORY
    return Strength.PARTIAL_HISTORY


def count_below(values: list, target: float) -> int:
    """How many of `values` are strictly smaller than `target`.

    The basis for every "larger than N of the previous M sessions" statement - a plain count
    rather than a percentile, because a count is something a viewer can check.
    """
    return sum(1 for v in values if v < target)


def streak(values: list, positive: bool) -> int:
    """Length of the run at the START of `values` sharing a sign.

    `values` is newest-first and must already include the current session when the caller
    wants the run to end today. Zero is neither positive nor negative and breaks a run.
    """
    length = 0
    for value in values:
        if (value > 0) if positive else (value < 0):
            length += 1
        else:
            break
    return length


def compounded_return(pct_changes: list) -> float:
    """Compound daily percentage changes properly: product(1 + r/100) - 1, as a percentage.

    Summing daily percentages and calling the total a return is wrong and gets more wrong the
    longer the window, so the arithmetic is done once here rather than approximated per caller.
    """
    total = 1.0
    for change in pct_changes:
        total *= (1 + change / 100.0)
    return (total - 1) * 100.0


def mean(values: list) -> float | None:
    return sum(values) / len(values) if values else None


def median(values: list) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


__all__ = ["HistoricalWindow", "SessionValue", "HistoryUnavailable", "DEFAULT_WINDOW_SESSIONS",
           "strength_for", "count_below", "streak", "compounded_return", "mean", "median"]

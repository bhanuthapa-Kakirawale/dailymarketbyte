"""52-week high/low classification - a NEW, deliberately CLOSE-based convention, not a third
window size bolted onto `radar.technical` (whose 20/50-session RANGE_UP/DOWN events are
INTRADAY high/low based). 52-week extremes are conventionally reported off closing price in
financial media, so this module picks that convention explicitly and documents the divergence
from `radar.technical` rather than silently reusing or changing it.

Pure and standalone: takes one symbol's own OHLC session series, no Radar event machinery, no
acquisition. PRIOR-N-EXCLUSIVE, matching every other window convention in this codebase
(`radar/technical.py`, `market_structure/observations.py::_dated_change`): the session being
evaluated never participates in its own comparison window.
"""
from __future__ import annotations

import datetime as dt

FIFTY_TWO_WEEK_WINDOW = 252                 # prior sessions compared against, PRIOR-N-EXCLUSIVE
FIFTY_TWO_WEEK_LOOKBACK_SESSIONS = 253       # window (252) + the session being evaluated (1)

STATUS_OK = "OK"
STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def _row_date(row: dict) -> dt.date | None:
    d = row.get("date")
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        return dt.date.fromisoformat(d[:10])
    if hasattr(d, "date"):
        return d.date()
    return None


def classify_fifty_two_week(series: list, session_date: dt.date) -> dict:
    """`series`: oldest-first OHLC session rows (same `{"date","open","high","low","close",
    "volume"}` shape used throughout this codebase), ending at `session_date` inclusive.

    Returns {"status": OK|INSUFFICIENT_HISTORY, "new_52w_high": bool, "new_52w_low": bool}.

    Requires the series' own last row to be dated exactly `session_date` (never trusted
    positionally - mirrors `observations.py::_dated_change`'s date-matching discipline) and a
    FULL `FIFTY_TWO_WEEK_WINDOW` (252) prior sessions - never a shrinking window, never guessed.
    A close exactly equal to the prior extreme is NOT a new high/low (strict `>` / `<`, matching
    `radar/technical.py::_range_break_event`'s own strict-inequality convention)."""
    if not series or _row_date(series[-1]) != session_date:
        return {"status": STATUS_INSUFFICIENT_HISTORY, "new_52w_high": False, "new_52w_low": False}
    prior = series[:-1]
    if len(prior) < FIFTY_TWO_WEEK_WINDOW:
        return {"status": STATUS_INSUFFICIENT_HISTORY, "new_52w_high": False, "new_52w_low": False}
    window = prior[-FIFTY_TWO_WEEK_WINDOW:]
    close = series[-1].get("close")
    if close is None:
        return {"status": STATUS_INSUFFICIENT_HISTORY, "new_52w_high": False, "new_52w_low": False}
    distinct_dates = {_row_date(r) for r in window}
    if len(distinct_dates) < FIFTY_TWO_WEEK_WINDOW:
        # a duplicate-dated row must never quietly satisfy the 252-session requirement
        return {"status": STATUS_INSUFFICIENT_HISTORY, "new_52w_high": False, "new_52w_low": False}
    closes = [r.get("close") for r in window if r.get("close") is not None]
    if len(closes) < FIFTY_TWO_WEEK_WINDOW:
        return {"status": STATUS_INSUFFICIENT_HISTORY, "new_52w_high": False, "new_52w_low": False}
    prior_high, prior_low = max(closes), min(closes)
    return {"status": STATUS_OK, "new_52w_high": close > prior_high, "new_52w_low": close < prior_low}


__all__ = ["classify_fifty_two_week", "FIFTY_TWO_WEEK_WINDOW", "FIFTY_TWO_WEEK_LOOKBACK_SESSIONS",
           "STATUS_OK", "STATUS_INSUFFICIENT_HISTORY"]

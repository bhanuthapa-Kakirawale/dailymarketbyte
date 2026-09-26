"""Which canonical session a scheduled job is for.

Every answer comes from `core.trading_calendar.SessionCalendar` (NSE's published holiday list +
special sessions) - never "calendar day - 1" and never the last row some provider returned. When
the calendar cannot establish a date (a year it does not cover) the answer is None and the caller
falls back to its existing, benchmark-driven path rather than guessing.
"""
from __future__ import annotations

import datetime as dt

from core.trading_calendar import SessionCalendar

# The one "bar is final" rule (`market.SESSION_FINAL_TIME`), repeated here as a value so this
# module does not import the market-data layer; a test pins the two together.
SESSION_FINAL_TIME = dt.time(15, 40)
_MAX_WALK_DAYS = 15


def _ist_naive(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        return now
    from config import IST
    return now.astimezone(IST).replace(tzinfo=None)


def latest_final_session(now: dt.datetime, calendar: SessionCalendar | None = None) -> dt.date | None:
    """The most recent canonical session whose close is final at `now` (IST): today once
    SESSION_FINAL_TIME has passed on a session day, otherwise the previous session."""
    cal = calendar or SessionCalendar()
    local = _ist_naive(now)
    today = local.date()
    open_today = cal.is_session(today)
    if open_today is None:
        return None
    if open_today and local.time() >= SESSION_FINAL_TIME:
        return today
    return cal.previous_session(today)


def next_session(d: dt.date, calendar: SessionCalendar | None = None) -> dt.date | None:
    """The canonical session immediately after `d`, or None if the calendar can't say."""
    cal = calendar or SessionCalendar()
    cur = d
    for _ in range(_MAX_WALK_DAYS):
        cur += dt.timedelta(days=1)
        s = cal.is_session(cur)
        if s is None:
            return None
        if s:
            return cur
    return None


def edition_date_for(session: dt.date, calendar: SessionCalendar | None = None) -> dt.date:
    """The report's `report_date` for a recap of `session`: the morning it is published for,
    i.e. the next canonical session. This is what the 07:40 run always used (its own date), so
    a report built the evening of D and one built the next morning share ONE report_id - the
    report job and the POST fallback can never both create a canonical report for a session.
    Falls back to the next weekday when the calendar does not cover the year."""
    nxt = next_session(session, calendar)
    if nxt is not None:
        return nxt
    cur = session + dt.timedelta(days=1)
    while cur.weekday() >= 5:
        cur += dt.timedelta(days=1)
    return cur


__all__ = ["latest_final_session", "next_session", "edition_date_for", "SESSION_FINAL_TIME"]

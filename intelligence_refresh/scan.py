"""Resolves which sessions `refresh`/`status` audits: the previous `days` calendar days (or an
explicit `--from`/`--to`/`--session-date` override), oldest-first, as real canonical trading
sessions, with the latest final session excluded (that one is handled by `latest_session.py`).
"""
from __future__ import annotations

import datetime as dt


def sessions_to_audit(out_dir: str, now: dt.datetime, *, days: int = 30,
                      from_date: dt.date | None = None, to_date: dt.date | None = None,
                      session_date: dt.date | None = None, calendar=None) -> tuple:
    """-> (historical_sessions: list[date] oldest-first, latest_session: date|None)."""
    from core.trading_calendar import SessionCalendar
    from operations.sessions import latest_final_session

    cal = calendar or SessionCalendar()
    latest = latest_final_session(now, cal)

    if session_date is not None:
        sessions = [session_date] if cal.is_session(session_date) else []
    else:
        if from_date is not None or to_date is not None:
            lo = from_date if from_date is not None else (now.date() - dt.timedelta(days=days))
            hi = to_date if to_date is not None else now.date()
        else:
            hi = now.date()
            lo = hi - dt.timedelta(days=days)
        sessions = cal.sessions_between(lo, hi)

    # Never audit a session newer than the latest FINAL session - today's session before
    # SESSION_FINAL_TIME has no finalized data to backfill at all, and isn't "historical" yet.
    # An explicit --from/--to/--session-date that reaches past the latest final session is
    # silently bounded by it, never attempted.
    historical = [d for d in sessions if d != latest and (latest is None or d <= latest)]
    return historical, latest


__all__ = ["sessions_to_audit"]

"""intelligence_refresh.scan.sessions_to_audit: resolves the audited window, oldest-first,
excluding the latest final session - and never includes a session newer than the latest final
session (today's session before close has no finalized data to backfill at all)."""
from __future__ import annotations

import datetime as dt

from intelligence_refresh.scan import sessions_to_audit

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# 2026-10-09 is a Friday trading session; 08:00 IST is before SESSION_FINAL_TIME (15:40), so
# the latest FINAL session is the previous one, 2026-10-08 (Thursday).
MORNING_BEFORE_CLOSE = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)


def test_todays_unfinished_session_is_never_included_as_historical(tmp_path):
    historical, latest = sessions_to_audit(str(tmp_path), MORNING_BEFORE_CLOSE, days=5)
    assert latest == dt.date(2026, 10, 8)
    assert dt.date(2026, 10, 9) not in historical
    assert all(d <= latest for d in historical)


def test_explicit_to_date_past_the_latest_final_session_is_bounded(tmp_path):
    historical, latest = sessions_to_audit(
        str(tmp_path), MORNING_BEFORE_CLOSE,
        from_date=dt.date(2026, 10, 1), to_date=dt.date(2026, 10, 9))
    assert dt.date(2026, 10, 9) not in historical
    assert max(historical) <= latest


def test_historical_sessions_are_oldest_first(tmp_path):
    historical, _ = sessions_to_audit(str(tmp_path), MORNING_BEFORE_CLOSE, days=10)
    assert historical == sorted(historical)


def test_latest_final_session_itself_is_excluded_from_historical(tmp_path):
    historical, latest = sessions_to_audit(str(tmp_path), MORNING_BEFORE_CLOSE, days=5)
    assert latest not in historical

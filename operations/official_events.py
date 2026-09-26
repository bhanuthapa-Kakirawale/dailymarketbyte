"""Early warning for the hand-maintained official schedules - BEFORE they go stale.

`core.event_calendar` already fails closed: an entry verified more than
MAX_VERIFICATION_AGE_DAYS before the day being planned is simply not shown. That protects the
video, but it fails silently from an operator's point of view - the RBI card just stops
appearing. This check turns the same thresholds into an advance reminder:

    EXPIRED        a showable-looking entry is already past the verification limit
    WARN_EXPIRING  its verification expires within WARNING_DAYS
    WARN_COVERAGE  the last accepted official event is within COVERAGE_WARNING_DAYS - the
                   organiser's next schedule has probably been published and is not entered
    WARN_FILE      the file itself is missing, invalid or has rejected entries
    WARN_HOLIDAYS  the NSE holiday list does not cover the coming year (December task)
    OK

Read-only and offline: it never fetches an organiser's site and never edits the file. To act on
a warning, an operator re-verifies with `python verify_official_events.py --stamp`.
"""
from __future__ import annotations

import datetime as dt

WARNING_DAYS = 30
COVERAGE_WARNING_DAYS = 60
CHECK_VERSION = "official-events-check-1.0"


def check_official_events(today: dt.date, path: str | None = None,
                          warning_days: int = WARNING_DAYS) -> dict:
    from core.event_calendar import MAX_VERIFICATION_AGE_DAYS, load_official_events
    from core.trading_calendar import NSE_TRADING_HOLIDAYS
    cal = load_official_events(path)
    findings = []
    if cal.status != "OK":
        findings.append({"level": "WARN_FILE", "message":
                         f"official event file {cal.status}: {cal.error or cal.rejected}"})
    upcoming = [e for e in cal.events if e.event_date >= today]
    for e in upcoming:
        expires = e.verified_on + dt.timedelta(days=MAX_VERIFICATION_AGE_DAYS)
        left = (expires - today).days
        if left < 0:
            findings.append({"level": "EXPIRED", "event_id": e.event_id, "message":
                             f"{e.event_id} verification ({e.verified_on}) expired {expires} - "
                             "it is no longer shown; re-verify from the organiser's page"})
        elif left <= warning_days:
            findings.append({"level": "WARN_EXPIRING", "event_id": e.event_id, "message":
                             f"{e.event_id} verification ({e.verified_on}) expires {expires} "
                             f"({left} days) - run `python verify_official_events.py --stamp`"})
    last = max((e.event_date for e in cal.events), default=None)
    if last is None or (last - today).days <= COVERAGE_WARNING_DAYS:
        findings.append({"level": "WARN_COVERAGE", "message":
                         f"last official event in the file is {last} - enter the organiser's "
                         "next published schedule (RBI MPC: next financial year)"})
    if today.month == 12 and (today.year + 1) not in NSE_TRADING_HOLIDAYS:
        findings.append({"level": "WARN_HOLIDAYS", "message":
                         f"NSE holiday list for {today.year + 1} is not in core/trading_calendar.py"})
    order = ("EXPIRED", "WARN_FILE", "WARN_EXPIRING", "WARN_COVERAGE", "WARN_HOLIDAYS")
    status = next((lvl for lvl in order if any(f["level"] == lvl for f in findings)), "OK")
    next_expiry = min((e.verified_on + dt.timedelta(days=MAX_VERIFICATION_AGE_DAYS)
                       for e in upcoming), default=None)
    return {"version": CHECK_VERSION, "checked_on": today.isoformat(), "status": status,
            "file": cal.path, "file_status": cal.status, "accepted": len(cal.events),
            "upcoming": len(upcoming), "last_event": last.isoformat() if last else None,
            "next_verification_expiry": next_expiry.isoformat() if next_expiry else None,
            "max_verification_age_days": MAX_VERIFICATION_AGE_DAYS,
            "warning_days": warning_days, "findings": findings}


__all__ = ["check_official_events", "WARNING_DAYS", "COVERAGE_WARNING_DAYS"]

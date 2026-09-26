"""Freshness of pre-open data (PRE-MARKET V1).

A pre-market fact is only useful if it is the RIGHT reading: the US close that actually
happened overnight, an Asian index as it stood minutes ago, a GIFT Nifty print taken this
morning. A number that fetched successfully but belongs to an older session is not "slightly
stale" - it describes a different night. So every live/pre-open reading carries its own
market date/timestamp, and the rules below decide FRESH or not, deterministically, from:

    the session about to open (`pre_date`, IST)
    the cutoff the video is built at (`as_of`, IST)
    the reading's own `market_date` / `market_timestamp` (recorded at acquisition)

Anything not FRESH is omitted from the Short, never shown with a caveat. No model, no network.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")

FRESHNESS_VERSION = "pre-fresh-1.0"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"                  # the reading the pre-open briefing needs
    STALE = "STALE"                  # an older session/print than expected - omit
    IN_PROGRESS = "IN_PROGRESS"      # the session it belongs to is not finished - omit
    FUTURE = "FUTURE"                # dated after the cutoff (look-ahead) - reject
    UNKNOWN = "UNKNOWN"              # no market date/timestamp recorded - omit


class QuoteKind(str, Enum):
    SESSION_CLOSE = "SESSION_CLOSE"  # a completed session's close (US indices, India VIX)
    LIVE = "LIVE"                    # a reading taken during a running session (Asia, GIFT)


# --------------------------------------------------------------------------- policy
# US cash session closes 16:00 New York time; the daily bar is treated as final a little
# after that (exchange closing auction + vendor settle).
US_CLOSE_LOCAL = dt.time(16, 0)
US_FINAL_GRACE = dt.timedelta(minutes=20)
# A live index reading older than this at the cutoff is not "this morning's" level (vendor
# delays of 15-20 min are normal; beyond that the print is not the current market).
LIVE_MAX_AGE = {"ASIA": dt.timedelta(minutes=30), "GIFT": dt.timedelta(minutes=20)}
DEFAULT_LIVE_MAX_AGE = dt.timedelta(minutes=30)


def to_ist(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def expected_us_session(pre_date: dt.date) -> dt.date:
    """The US session whose close is "overnight" for an Indian session on `pre_date`: the
    latest weekday strictly before it (Mon -> Fri). A US holiday on that weekday means there is
    no fresh US close - the older bar is then STALE, never presented as last night's."""
    d = pre_date - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def us_close_final_at(session: dt.date) -> dt.datetime:
    """IST moment after which the US daily bar for `session` is final (DST-correct)."""
    local = dt.datetime.combine(session, US_CLOSE_LOCAL, tzinfo=NEW_YORK)
    return (local + US_FINAL_GRACE).astimezone(IST)


def us_close_freshness(market_date: dt.date | None, pre_date: dt.date,
                       as_of: dt.datetime) -> tuple:
    """(status, reason) for a US index's daily close used as an overnight cue."""
    if market_date is None:
        return FreshnessStatus.UNKNOWN, "no bar date recorded"
    want = expected_us_session(pre_date)
    if market_date > want:
        return FreshnessStatus.FUTURE, (f"bar dated {market_date} is after the expected "
                                        f"overnight session {want}")
    if market_date < want:
        return FreshnessStatus.STALE, (f"latest bar {market_date} is older than the expected "
                                       f"overnight session {want} (US holiday or missing bar)")
    if to_ist(as_of) < us_close_final_at(market_date):
        return FreshnessStatus.IN_PROGRESS, f"US session {market_date} not final at the cutoff"
    return FreshnessStatus.FRESH, f"US close of {market_date:%a %d %b} (the overnight session)"


def live_freshness(market_timestamp: dt.datetime | None, pre_date: dt.date,
                   as_of: dt.datetime, region: str = "ASIA") -> tuple:
    """(status, reason) for a live reading (Asian index, GIFT Nifty) taken before the open."""
    if market_timestamp is None:
        return FreshnessStatus.UNKNOWN, "no market timestamp recorded"
    ts, cut = to_ist(market_timestamp), to_ist(as_of)
    if ts > cut:
        return FreshnessStatus.FUTURE, f"reading at {ts:%H:%M} is after the cutoff {cut:%H:%M}"
    if ts.date() != pre_date:
        return FreshnessStatus.STALE, (f"reading dated {ts.date()} is not from the morning of "
                                       f"{pre_date} (market closed or no data yet)")
    age = cut - ts
    limit = LIVE_MAX_AGE.get(region, DEFAULT_LIVE_MAX_AGE)
    if age > limit:
        return FreshnessStatus.STALE, (f"reading at {ts:%H:%M} IST is {int(age.total_seconds() // 60)} "
                                       f"min old at the cutoff (limit {int(limit.total_seconds() // 60)})")
    return FreshnessStatus.FRESH, f"reading at {ts:%H:%M} IST, {int(age.total_seconds() // 60)} min before the cutoff"


def session_freshness(market_date: dt.date | None, previous_session: dt.date) -> tuple:
    """(status, reason) for a previous-session close (India VIX, flows): it must belong to the
    canonical previous session, nothing older."""
    if market_date is None:
        return FreshnessStatus.UNKNOWN, "no session date recorded"
    if market_date == previous_session:
        return FreshnessStatus.FRESH, f"close of the previous session {previous_session}"
    if market_date < previous_session:
        return FreshnessStatus.STALE, (f"reading from {market_date}, not the previous session "
                                       f"{previous_session}")
    return FreshnessStatus.FUTURE, f"reading from {market_date}, after {previous_session}"


def clock_label(ts: dt.datetime) -> str:
    """"7:52 AM" - the IST time a live reading was true, as drawn on screen."""
    t = to_ist(ts)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


__all__ = ["FreshnessStatus", "QuoteKind", "expected_us_session", "us_close_final_at",
           "us_close_freshness", "live_freshness", "session_freshness", "clock_label", "to_ist",
           "IST", "NEW_YORK", "LIVE_MAX_AGE", "FRESHNESS_VERSION"]

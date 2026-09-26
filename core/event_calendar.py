"""Scheduled events for the pre-market briefing (PRE-MARKET V1).

A PRE Short may put an event on screen only if it is a VERIFIED SCHEDULE - never because a
headline mentioned it. Two sources qualify:

    OFFICIAL_SCHEDULE  a date published by the organiser, entered by hand with the page it
                       was read from and the day it was checked (like the NSE holiday list in
                       core.trading_calendar - add next year's dates every December)
    RULE_DERIVED       an exchange rule applied to the canonical trading calendar (F&O expiry)

News-derived "events" in the MarketReport (Google News RSS headlines tagged RBI/IPO/...) are
NOT schedules: they report something that happened or was talked about. They are never shown
as today's event - the PRE plan records them as omitted with that reason.

Times: an event carries a time only when the time itself was verified. Otherwise `event_time`
is None and the card says so plainly ("US time") rather than guessing an IST clock time.

Official schedules live in two places: the FOMC dates below (code, as in V1) and the
CONTROLLED OFFICIAL-EVENT FILE `data/official_events.json` (RBI MPC). The file is maintained
by hand from the organiser's own page and re-checked by `verify_official_events.py`; the daily
run never depends on the organiser's website being reachable. `load_official_events()` fails
closed: an entry that is incomplete, cites a non-official host, has dates that disagree with
its own quoted source line, or claims a time without a verified time source is REJECTED (never
shown) and recorded in the audit. An OFFICIAL_SCHEDULE last verified more than
MAX_VERIFICATION_AGE_DAYS before the day being planned is not shown either - an official
schedule can be amended, and an unchecked old copy is not a verified one.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from urllib.parse import urlparse

EVENT_CALENDAR_VERSION = "events-1.1"
OFFICIAL_EVENTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "official_events.json")
MAX_VERIFICATION_AGE_DAYS = 180
# The organiser's own hosts. A schedule cited from anywhere else is not an official schedule.
OFFICIAL_HOSTS = {"RBI": ("www.rbi.org.in", "rbi.org.in", "website.rbi.org.in",
                          "rbidocs.rbi.org.in"),
                  "FED": ("www.federalreserve.gov", "federalreserve.gov")}


class EventValidation(str, Enum):
    OFFICIAL_SCHEDULE = "OFFICIAL_SCHEDULE"     # organiser's published calendar, verified
    RULE_DERIVED = "RULE_DERIVED"               # exchange rule on the canonical calendar
    TENTATIVE = "TENTATIVE"                     # organiser says "tentative" - never shown
    UNVERIFIED = "UNVERIFIED"                   # news/headline/model - never shown


class Importance(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


SHOWABLE = frozenset({EventValidation.OFFICIAL_SCHEDULE, EventValidation.RULE_DERIVED})


@dataclass(frozen=True)
class ScheduledEvent:
    event_name: str               # "US Fed policy decision"
    event_date: dt.date           # the date the event falls on (organiser's calendar date)
    event_time: dt.time | None    # IST, only when the time itself was verified
    time_label: str               # what the card says about timing ("10:00 AM IST", "US time")
    event_type: str               # RBI / FED / F&O / ...
    tag: str                      # the chip on screen
    source: str                   # organiser page or rule name
    importance: Importance
    validation_status: EventValidation
    verified_on: dt.date | None = None
    note: str = ""
    source_reference: str = ""    # "RBI Press Release 2025-2026/2306"
    retrieved_at: str = ""        # when the organiser's page was read (ISO)
    event_id: str = ""

    @property
    def showable(self) -> bool:
        return self.validation_status in SHOWABLE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_date"] = self.event_date.isoformat()
        d["event_time"] = self.event_time.strftime("%H:%M") if self.event_time else None
        d["importance"] = self.importance.value
        d["validation_status"] = self.validation_status.value
        d["verified_on"] = self.verified_on.isoformat() if self.verified_on else None
        return d


# --------------------------------------------------------------------------- official schedules
_FED_SOURCE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_FED_REFERENCE = "Federal Reserve FOMC meeting calendar"
_FED_CHECKED = dt.date(2026, 9, 25)
# FOMC meetings, read from the Fed's own calendar page on 2026-09-25. The DECISION day is the
# meeting's last day. The statement time is not on that page, so no time is claimed: the card
# says "US time" (it lands overnight in India). 2027 dates are marked tentative by the Fed
# ("Each meeting date is tentative until confirmed") and are never shown until confirmed.
_FOMC_2026 = ("2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
              "2026-09-16", "2026-10-28", "2026-12-09")
_FOMC_2027_TENTATIVE = ("2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28",
                        "2027-09-15", "2027-10-27", "2027-12-08")

# RBI MPC: NOT in code - see data/official_events.json (read from RBI Press Release
# 2025-2026/2306 on www.rbi.org.in on 2026-09-25) and load_official_events() below.


def _fomc(d: str, status: EventValidation) -> ScheduledEvent:
    return ScheduledEvent(
        event_name="US Fed policy decision", event_date=dt.date.fromisoformat(d),
        event_time=None, time_label="US time - overnight in India", event_type="FED",
        tag="US FED", source=_FED_SOURCE, importance=Importance.HIGH,
        validation_status=status, verified_on=_FED_CHECKED,
        note="FOMC meeting's final day; statement time not claimed",
        source_reference=_FED_REFERENCE, retrieved_at="2026-09-25", event_id=f"fomc-{d}")


FOMC_EVENTS: tuple = tuple(
    [_fomc(d, EventValidation.OFFICIAL_SCHEDULE) for d in _FOMC_2026]
    + [_fomc(d, EventValidation.TENTATIVE) for d in _FOMC_2027_TENTATIVE])


# --------------------------------------------------------------------------- controlled file
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_MEETING_LINE = re.compile(
    r"\b(" + "|".join(m.title() for m in _MONTHS) + r")\s+"
    r"(\d{1,2}(?:\s*(?:,|and|&)\s*\d{1,2})*)\s*,\s*(\d{4})\b")


def parse_meeting_line(text: str) -> list:
    """"October 5, 6 and 7, 2026" -> [[date(2026,10,5), date(2026,10,6), date(2026,10,7)]].
    Every meeting line found, in order. Pure; used by the loader (an entry must match its own
    quoted line) and by verify_official_events.py (the file must match the organiser's page)."""
    out = []
    for m in _MEETING_LINE.finditer(text or ""):
        month = _MONTHS.index(m.group(1).lower()) + 1
        year = int(m.group(3))
        try:
            days = [dt.date(year, month, int(d)) for d in re.findall(r"\d{1,2}", m.group(2))]
        except ValueError:
            continue
        out.append(days)
    return out


@dataclass
class OfficialCalendar:
    """What the controlled file yielded: accepted events and rejected entries (with reasons)."""
    path: str
    status: str                      # OK / PARTIAL / UNAVAILABLE / INVALID
    events: tuple = ()
    rejected: list = field(default_factory=list)
    error: str = ""
    schema_version: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "status": self.status, "schema_version": self.schema_version,
                "accepted": [e.to_dict() for e in self.events], "rejected": list(self.rejected),
                "error": self.error}


_REQUIRED = ("event_id", "organiser", "event_type", "event_name", "tag", "event_date",
             "meeting_dates", "time_label", "importance", "source_url", "source_reference",
             "source_text", "retrieved_at", "verified_on", "validation_status")


def validate_official_entry(entry: dict) -> tuple:
    """(ScheduledEvent | None, reason). Fail closed: any doubt is a rejection."""
    if not isinstance(entry, dict):
        return None, "entry is not an object"
    missing = [k for k in _REQUIRED if entry.get(k) in (None, "", [])]
    if missing:
        return None, f"missing required field(s): {', '.join(missing)}"
    org = str(entry["organiser"]).upper()
    hosts = OFFICIAL_HOSTS.get(org)
    if not hosts:
        return None, f"organiser {org!r} has no registered official host"
    url = urlparse(str(entry["source_url"]))
    if url.scheme != "https" or url.hostname not in hosts:
        return None, (f"source_url {entry['source_url']!r} is not on {org}'s own site "
                      f"({', '.join(hosts)})")
    try:
        day = dt.date.fromisoformat(entry["event_date"])
        meeting = sorted(dt.date.fromisoformat(d) for d in entry["meeting_dates"])
        verified = dt.date.fromisoformat(entry["verified_on"])
        dt.datetime.fromisoformat(entry["retrieved_at"])
    except (TypeError, ValueError) as exc:
        return None, f"unparseable date: {exc}"
    quoted = parse_meeting_line(entry["source_text"])
    if len(quoted) != 1 or sorted(quoted[0]) != meeting:
        return None, (f"meeting_dates {[d.isoformat() for d in meeting]} do not match the quoted "
                      f"source line {entry['source_text']!r}")
    if day != meeting[-1]:
        return None, f"event_date {day} is not the meeting's final day {meeting[-1]}"
    try:
        status = EventValidation(entry["validation_status"])
        importance = Importance(entry["importance"])
    except ValueError as exc:
        return None, f"invalid enum: {exc}"
    if status not in (EventValidation.OFFICIAL_SCHEDULE, EventValidation.TENTATIVE):
        return None, f"validation_status {status.value} is not an official schedule"
    etime = None
    if entry.get("event_time"):
        if not entry.get("time_source_text"):
            return None, "event_time given without a verified time_source_text - no invented times"
        try:
            etime = dt.time.fromisoformat(entry["event_time"])
        except ValueError as exc:
            return None, f"unparseable event_time: {exc}"
    return ScheduledEvent(
        event_name=entry["event_name"], event_date=day, event_time=etime,
        time_label=entry["time_label"], event_type=entry["event_type"], tag=entry["tag"],
        source=entry["source_url"], importance=importance, validation_status=status,
        verified_on=verified, note=entry.get("note", ""),
        source_reference=entry["source_reference"], retrieved_at=entry["retrieved_at"],
        event_id=entry["event_id"]), ""


def load_official_events(path: str | None = None) -> OfficialCalendar:
    """Read and validate the controlled file. Never raises: an unreadable file means no
    file-based events (UNAVAILABLE/INVALID), recorded - never invented."""
    path = path or OFFICIAL_EVENTS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return OfficialCalendar(path, "UNAVAILABLE", error="official event file not found")
    except (OSError, ValueError) as exc:
        return OfficialCalendar(path, "INVALID", error=f"{type(exc).__name__}: {exc}")
    entries = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return OfficialCalendar(path, "INVALID", error="no 'events' list")
    accepted, rejected, seen = [], [], set()
    for entry in entries:
        ev, why = validate_official_entry(entry)
        eid = entry.get("event_id") if isinstance(entry, dict) else None
        if ev is not None and eid in seen:
            ev, why = None, "duplicate event_id"
        if ev is None:
            rejected.append({"event_id": eid, "reason": why})
            continue
        seen.add(eid)
        accepted.append(ev)
    status = "OK" if not rejected else ("PARTIAL" if accepted else "INVALID")
    return OfficialCalendar(path, status, tuple(accepted), rejected,
                            schema_version=str(doc.get("schema_version", "")))


OFFICIAL_EVENTS: tuple = FOMC_EVENTS + load_official_events().events


# --------------------------------------------------------------------------- rule-derived
def expiry_event(day: dt.date, calendar, expiry_weekday: int) -> ScheduledEvent | None:
    """Nifty F&O expiry on `day`, from the configured expiry weekday and the canonical trading
    calendar. Only when `day` itself is a session on that weekday: when the weekday is a holiday
    the exchange moves expiry, and that move is not guessed here - no event is shown."""
    if day.weekday() != expiry_weekday or calendar.is_session(day) is not True:
        return None
    later = day + dt.timedelta(days=7)
    monthly = later.month != day.month
    kind = "monthly" if monthly else "weekly"
    return ScheduledEvent(
        event_name=f"Nifty {kind} F&O expiry", event_date=day, event_time=None,
        time_label="During today's session", event_type="F&O", tag="F&O EXPIRY",
        source="expiry_calendar_rule (config NIFTY_EXPIRY_WEEKDAY + core.trading_calendar)",
        importance=Importance.HIGH if monthly else Importance.MEDIUM,
        validation_status=EventValidation.RULE_DERIVED,
        note=f"{kind} expiry on the configured weekday", event_id=f"expiry-{day}")


def verification_current(e: ScheduledEvent, day: dt.date) -> bool:
    """An official schedule is shown only if it was verified against the organiser's page
    within MAX_VERIFICATION_AGE_DAYS before `day` (a verification after `day` also counts)."""
    if e.validation_status is not EventValidation.OFFICIAL_SCHEDULE:
        return True
    return e.verified_on is not None and (day - e.verified_on).days <= MAX_VERIFICATION_AGE_DAYS


def scheduled_events_for(day: dt.date, calendar, expiry_weekday: int,
                         official: tuple | None = None) -> list:
    """Every VERIFIED scheduled event on `day`, most important first. Tentative/unverified
    entries, and official entries whose verification is too old, are never returned."""
    official = OFFICIAL_EVENTS if official is None else official
    out = [e for e in official if e.event_date == day and e.showable
           and verification_current(e, day)]
    exp = expiry_event(day, calendar, expiry_weekday)
    if exp is not None:
        out.append(exp)
    rank = {Importance.HIGH: 0, Importance.MEDIUM: 1, Importance.LOW: 2}
    return sorted(out, key=lambda e: (rank[e.importance], e.event_name))


def event_calendar_audit(day: dt.date, calendar, expiry_weekday: int,
                         official_file: OfficialCalendar | None = None) -> dict:
    """What the calendar knew about `day` and why each candidate was or was not shown."""
    cal_file = official_file or load_official_events()
    official = FOMC_EVENTS + tuple(cal_file.events)
    shown = scheduled_events_for(day, calendar, expiry_weekday, official=official)
    excluded = []
    for e in official:
        if e.event_date != day or e in shown:
            continue
        why = (f"{e.validation_status.value} - never shown" if not e.showable else
               f"verified {e.verified_on}, more than {MAX_VERIFICATION_AGE_DAYS} days before {day}")
        excluded.append({"event_id": e.event_id, "event_name": e.event_name, "reason": why})
    return {"day": day.isoformat(), "version": EVENT_CALENDAR_VERSION,
            "official_file": {"path": cal_file.path, "status": cal_file.status,
                              "schema_version": cal_file.schema_version,
                              "accepted": len(cal_file.events), "rejected": list(cal_file.rejected),
                              "error": cal_file.error},
            "shown": [e.to_dict() for e in shown], "excluded": excluded}


__all__ = ["ScheduledEvent", "EventValidation", "Importance", "OFFICIAL_EVENTS", "FOMC_EVENTS",
           "scheduled_events_for", "expiry_event", "SHOWABLE", "EVENT_CALENDAR_VERSION",
           "OfficialCalendar", "load_official_events", "validate_official_entry",
           "parse_meeting_line", "verification_current", "event_calendar_audit",
           "OFFICIAL_EVENTS_PATH", "MAX_VERIFICATION_AGE_DAYS", "OFFICIAL_HOSTS"]

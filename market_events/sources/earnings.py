"""EARNINGS adapter: NSE's board-meeting prior-intimation feed
(`/api/corporate-board-meetings?index=equities`, confirmed live and reachable from this
environment), filtered to filings whose OWN text explicitly states financial-results
consideration. A board meeting for any other purpose is dropped, never shown - the same
discipline `ipo_watch.parse_nse_issues` already applies to its own rows.

Event identity: this feed carries no stable per-filing ID that survives a reschedule (the
meeting date is exactly the field a reschedule changes), so identity is reconciled via
`MarketEventsService._lookup_open_event` (see `market_events/service.py`) rather than any
field here - a reschedule reuses the existing open event_key (`status=REVISED_DATE`); a new
quarterly cycle (no open event, or the prior one is terminal) mints a fresh key.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from core import sources as S

from ..models import (EARNINGS, PARSE_ERROR, REVISED_DATE, SCHEDULED, SCHEMA_VERSION,
                      SOURCE_UNAVAILABLE, SUCCESS, FamilyFetchResult, MarketEvent)

BOARD_MEETINGS_URL = "https://www.nseindia.com/api/corporate-board-meetings?index=equities"
PARSER_VERSION = "earnings-nse-bm-1.0"

_RESULTS_RE = re.compile(r"financial\s+results", re.I)
_PERIOD_RE = re.compile(
    r"\b(Q[1-4]\s?FY\s?\d{2,4}|H[12]\s?FY\s?\d{2,4}|9M\s?FY\s?\d{2,4}|Q[1-4]|H[12]|9M|FY\s?\d{2,4})\b",
    re.I)
_COMPANY_FROM_DESC_RE = re.compile(r"^(.*?)\s+has informed the Exchange", re.I)


def _parse_date(txt) -> dt.date | None:
    s = str(txt or "").strip()
    if not s or s == "-":
        return None
    try:
        return dt.datetime.strptime(s.title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _company_name(row: dict) -> str | None:
    for key in ("sm_name", "bm_sm_name", "company", "companyName"):
        v = row.get(key)
        if v:
            return str(v).strip()
    m = _COMPANY_FROM_DESC_RE.match(str(row.get("bm_desc") or ""))
    return m.group(1).strip() if m else None


def _reporting_period(row: dict) -> str:
    """Only ever a token the source's OWN text states - never computed from bm_date."""
    text = f"{row.get('bm_purpose') or ''} {row.get('bm_desc') or ''}"
    m = _PERIOD_RE.search(text)
    return m.group(1).upper().replace(" ", "") if m else "UNKNOWN"


def parse_board_meetings(rows, now_iso: str, lookup_fn=None) -> tuple:
    """(events, notes). Deterministic classification/extraction only - never a network call."""
    if not isinstance(rows, list):
        return [], ["payload is not a list"]
    try:
        today = dt.datetime.fromisoformat(now_iso).date().isoformat()
    except (TypeError, ValueError):
        today = dt.date.today().isoformat()
    events, notes, seen = [], [], set()
    for row in rows:
        if not isinstance(row, dict):
            notes.append("dropped row: not an object")
            continue
        symbol = str(row.get("bm_symbol") or "").strip().upper() or None
        bm_date = _parse_date(row.get("bm_date"))
        if not symbol or bm_date is None:
            notes.append(f"dropped row {symbol or '?'}: missing symbol or unparseable bm_date")
            continue
        text = f"{row.get('bm_purpose') or ''} {row.get('bm_desc') or ''}"
        if not _RESULTS_RE.search(text):
            continue                       # not EARNINGS-qualifying - not a drop, just not ours
        if symbol in seen:
            continue                       # one row per symbol per fetch
        company = _company_name(row)
        if not company:
            notes.append(f"dropped row {symbol}: no company name stated")
            continue
        seen.add(symbol)
        existing = lookup_fn(EARNINGS, symbol) if lookup_fn else None
        if existing is not None:
            event_key = existing.event_key
            status = REVISED_DATE if existing.data_as_of != bm_date.isoformat() else SCHEDULED
        else:
            natural_id = hashlib.sha256(f"{symbol}|{EARNINGS}|{today}".encode()).hexdigest()[:16]
            event_key = f"{EARNINGS}:{symbol}:{natural_id}"
            status = SCHEDULED
        events.append(MarketEvent(
            schema_version=SCHEMA_VERSION, family=EARNINGS, event_key=event_key, symbol=symbol,
            company=company, status=status, sub_type=None, data_as_of=bm_date.isoformat(),
            source_name=S.SRC_NSE_BOARD_MEETINGS, source_reference=BOARD_MEETINGS_URL,
            source_text=str(row.get("bm_desc") or ""), retrieved_at=now_iso or "",
            status_capture=SUCCESS,
            facts=[{"label": "reporting_period", "value": _reporting_period(row)}],
            parser_version=PARSER_VERSION))
    return events, notes


def fetch_earnings(now_iso: str, lookup_fn=None, nse=None) -> FamilyFetchResult:
    from operations.connectivity import classify_exception
    if nse is None:
        from market import NSE
        try:
            nse = NSE()
        except Exception as exc:
            return FamilyFetchResult(status=SOURCE_UNAVAILABLE, events=[],
                                     connectivity=classify_exception(exc),
                                     reason=f"NSE client: {type(exc).__name__}: {exc}")
    try:
        payload = nse.get("/api/corporate-board-meetings?index=equities")
    except Exception as exc:
        return FamilyFetchResult(status=SOURCE_UNAVAILABLE, events=[],
                                 connectivity=classify_exception(exc),
                                 reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    if not isinstance(payload, list):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="payload is not a list")
    events, notes = parse_board_meetings(payload, now_iso, lookup_fn=lookup_fn)
    if payload and not events and notes and len(notes) == len(payload):
        # every row failed structurally - a real shape change, never "no results today"
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="; ".join(notes)[:300])
    return FamilyFetchResult(status=SUCCESS, events=events, connectivity="REACHABLE",
                             reason="; ".join(notes)[:300])


fetch_earnings.needs_lookup = True

__all__ = ["BOARD_MEETINGS_URL", "PARSER_VERSION", "parse_board_meetings", "fetch_earnings"]

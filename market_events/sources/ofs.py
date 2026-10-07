"""OFS adapter: NSE's own Offer For Sale mechanism feed - the exchange's dedicated OFS page
(`/market-data/all-upcoming-issues-ofs`) is served by the SAME frontend controller as the IPO
page (`upcoming-ipo.js`), which itself calls:

    /api/live-ofs-active-issues     - OFS currently open/scheduled (confirmed live, 2026-10-06:
                                       reachable, `{"data": []}` - genuinely no OFS active that
                                       day, not a parse failure)
    /api/live-ofs-past-issues       - OFS already completed (confirmed live: 471 real historical
                                       records, e.g. "Sustainable Energy Infra Trust"/
                                       SEITINVITCUMU, offerDate 24-Sep-2026, floorPrice 120)

Every row from either endpoint IS an OFS by construction - this is NSE's own OFS listing, not a
classification over a general feed, so there is no "reject a non-OFS row" rule the way EARNINGS
rejects a non-results board meeting; the source's own identity as the OFS page is the
explicit-source-text requirement.

Field names for the (currently empty) active feed are taken from NSE's own frontend JS
(`upcoming-ipo.js`, which reads `eachAdd.symbol`, `eachAdd.series`, `eachAdd.ofsStartDate ||
eachAdd.startDate`, `eachAdd.ofsEndDate || eachAdd.endDate`, `eachAdd.status` off this exact
endpoint's response) - official evidence of the schema, not a guess. A populated row that does
not carry these fields fails closed (PARSE_ERROR), never a fabricated value.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from core import sources as S

from ..models import (ANNOUNCED, CLOSED, COMPLETED, OFS, OPEN, PARSE_ERROR, SCHEMA_VERSION,
                      SOURCE_UNAVAILABLE, SUCCESS, FamilyFetchResult, MarketEvent)

ACTIVE_URL = "https://www.nseindia.com/api/live-ofs-active-issues"
PAST_URL = "https://www.nseindia.com/api/live-ofs-past-issues"
PARSER_VERSION = "ofs-nse-live-1.0"
RECENT_WINDOW_DAYS = 7     # past issues older than this are not re-imported as live events


def _parse_date(txt) -> dt.date | None:
    s = str(txt or "").strip()
    if not s or s == "-":
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s.title(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(txt):
    s = str(txt or "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _classify_active(start: dt.date, end: dt.date, today: dt.date):
    """(data_as_of, status) for TODAY specifically, or None if this structurally-valid row has
    nothing to report today (a middle day of a >2-day window, or already past close) - this is
    the OFS analogue of `ipo_watch.todays_event`: a row not matching today is skipped silently,
    never counted as a schema/structural failure."""
    if today < start:
        return start, ANNOUNCED
    if today == start:
        return start, (OPEN if start != end else CLOSED)
    if today == end:
        return end, CLOSED
    return None


def _active_event(row: dict, today: dt.date, lookup_fn=None, retrieved_at: str = ""):
    """One row from live-ofs-active-issues. Returns (event_or_None, structurally_valid).
    `structurally_valid=False` means the row itself was malformed (missing the fields NSE's
    own frontend JS reads) - a real schema-drift signal. `structurally_valid=True` with
    `event=None` just means this valid row has no "today" fact to report, which must never be
    mistaken for a parse failure."""
    symbol = str(row.get("symbol") or "").strip().upper()
    start = _parse_date(row.get("ofsStartDate") or row.get("startDate"))
    end = _parse_date(row.get("ofsEndDate") or row.get("endDate"))
    if not symbol or start is None or end is None:
        return None, False
    classified = _classify_active(start, end, today)
    if classified is None:
        return None, True
    data_as_of, status = classified
    company = str(row.get("companyName") or row.get("company") or symbol).strip()
    floor_price = _parse_float(row.get("floorPrice"))
    facts = [{"label": "ofs_open_date", "value": start.isoformat()},
            {"label": "ofs_close_date", "value": end.isoformat()}]
    if floor_price is not None:
        facts.append({"label": "floor_price", "value": f"{floor_price:g}"})

    existing = lookup_fn(OFS, symbol) if lookup_fn else None
    if existing is not None:
        event_key = existing.event_key
        if existing.data_as_of != data_as_of.isoformat() and status != CLOSED:
            from ..models import REVISED_DATE
            status = REVISED_DATE
    else:
        natural_id = hashlib.sha256(f"{symbol}|{OFS}|{today.isoformat()}".encode()).hexdigest()[:16]
        event_key = f"{OFS}:{symbol}:{natural_id}"
    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=OFS, event_key=event_key,
                     symbol=symbol, company=company, status=status, sub_type=None,
                     data_as_of=data_as_of.isoformat(), source_name=S.SRC_NSE_OFS,
                     source_reference=ACTIVE_URL, source_text="", retrieved_at=retrieved_at,
                     status_capture=SUCCESS, facts=facts, parser_version=PARSER_VERSION)
    return ev, True


def _past_events(rows: list, now: dt.date, retrieved_at: str = "") -> list:
    """Recently-completed OFS (within RECENT_WINDOW_DAYS) from live-ofs-past-issues - one
    MarketEvent per (symbol, offerDate, category), keeping the row with the highest sr_no
    (NSE's own table carries more than one raw row per OFS instance; the latest sr_no is the
    most complete snapshot - never averaged or guessed)."""
    cutoff = now - dt.timedelta(days=RECENT_WINDOW_DAYS)
    best: dict = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        offer_date = _parse_date(row.get("offerDate"))
        if not symbol or offer_date is None or offer_date < cutoff or offer_date > now:
            continue
        key = (symbol, offer_date.isoformat(), str(row.get("category") or ""))
        sr = row.get("sr_no") or 0
        if key not in best or sr >= best[key][0]:
            best[key] = (sr, row)
    out = []
    for (symbol, offer_iso, category), (_, row) in best.items():
        company = str(row.get("companyName") or symbol).strip()
        floor_price = _parse_float(row.get("floorPrice"))
        allocate_price = _parse_float(row.get("allocatePrice"))
        facts = [{"label": "offer_date", "value": offer_iso}]
        if category:
            facts.append({"label": "category", "value": category})
        if floor_price is not None:
            facts.append({"label": "floor_price", "value": f"{floor_price:g}"})
        if allocate_price is not None:
            facts.append({"label": "allocate_price", "value": f"{allocate_price:g}"})
        event_key = f"{OFS}:{symbol}:{offer_iso}-{category or 'NA'}"
        out.append(MarketEvent(schema_version=SCHEMA_VERSION, family=OFS, event_key=event_key,
                               symbol=symbol, company=company, status=COMPLETED, sub_type=None,
                               data_as_of=offer_iso, source_name=S.SRC_NSE_OFS,
                               source_reference=PAST_URL, source_text="",
                               retrieved_at=retrieved_at, status_capture=SUCCESS, facts=facts,
                               parser_version=PARSER_VERSION))
    return out


def fetch_ofs(now_iso: str, lookup_fn=None, nse=None) -> FamilyFetchResult:
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
        today = dt.datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = dt.date.today()

    try:
        active_payload = nse.get("/api/live-ofs-active-issues")
    except Exception as exc:
        return FamilyFetchResult(status=SOURCE_UNAVAILABLE, events=[],
                                 connectivity=classify_exception(exc),
                                 reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    if not isinstance(active_payload, dict) or "data" not in active_payload:
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="active-issues payload missing 'data' key")
    active_rows = active_payload.get("data") or []
    if not isinstance(active_rows, list):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="active-issues 'data' is not a list")

    events, structural_failures = [], 0
    for row in active_rows:
        ev, structurally_valid = _active_event(row, today, lookup_fn=lookup_fn,
                                               retrieved_at=now_iso)
        if not structurally_valid:
            structural_failures += 1
            continue
        if ev is not None:
            events.append(ev)
    if active_rows and structural_failures == len(active_rows):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="every active row was missing symbol/start/end date - "
                                       "a real shape change, not an empty day")

    try:
        past_payload = nse.get("/api/live-ofs-past-issues")
        past_rows = (past_payload or {}).get("data") or [] if isinstance(past_payload, dict) else []
    except Exception:
        past_rows = []     # past issues is supplementary - never fails the whole fetch
    events.extend(_past_events(past_rows, today, retrieved_at=now_iso))

    return FamilyFetchResult(status=SUCCESS, events=events, connectivity="REACHABLE", reason="")


fetch_ofs.needs_lookup = True

__all__ = ["ACTIVE_URL", "PAST_URL", "PARSER_VERSION", "RECENT_WINDOW_DAYS", "fetch_ofs"]

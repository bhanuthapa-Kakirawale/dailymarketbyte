"""BUYBACK adapter: NSE's own structured corporate-actions feed
(`/api/corporates-corporateActions?index=equities`, `subject == "Buy Back"` - an exact
categorical match, confirmed live and reachable), cross-referenced with the general
corporate-announcements feed (`desc` in a fixed 4-value set) for lifecycle status
(announced -> closed) and route/price/quantity ONLY when a filing's own text states them
explicitly, plus the dedicated daily-buyback disclosure feed for an explicit "currently open"
signal. See docs/MARKET_EVENTS_ENGINE.md.

Every path was found via a real nav link or a real script-tag grep on NSE's own pages, never
guessed:
    /api/corporates-corporateActions?index=equities   PRIMARY - structured company/symbol/
                                                        record date/ex-date/face value
    /api/corporate-announcements?index=equities        SECONDARY - lifecycle narrative; route/
                                                        price/quantity when explicitly stated
    /api/corporates-daily-buyback                       SECONDARY - explicit proof of an
                                                        active daily purchase today

Event identity reuses the SAME `needs_lookup`/`lookup_fn` reconciliation EARNINGS/OFS already
use: a symbol's latest NON-TERMINAL buyback event is reused across multiple filings for one
cycle (a real example: TeamLease Services filed a Public Announcement on 01-Jul-2026 then a
Letter of Offer on 07-Jul-2026 for the SAME buyback); once a cycle reaches `COMPLETED`
(terminal), the NEXT genuinely new buyback for that symbol automatically mints a fresh
`event_key` - no new logic, the existing `_lookup_open_event` terminal-status filter already
does this.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from core import sources as S

from ..models import (ANNOUNCED, BUYBACK, COMPLETED, OPEN, PARSE_ERROR, SCHEMA_VERSION,
                      SOURCE_UNAVAILABLE, SUCCESS, WITHDRAWN, FamilyFetchResult, MarketEvent)

CORPORATE_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities"
ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
DAILY_BUYBACK_URL = "https://www.nseindia.com/api/corporates-daily-buyback?"
PARSER_VERSION = "buyback-nse-corpactions-1.0"

_ANNOUNCED_DESC = frozenset({"Buyback", "Public Announcement - Buyback of Shares"})
_CLOSURE_DESC = frozenset({"Post Buyback Public Announcement", "Closure of Buy Back"})
_ALL_DESC = _ANNOUNCED_DESC | _CLOSURE_DESC

_TENDER_RE = re.compile(r"\btender offer\b", re.I)
_OPEN_MARKET_RE = re.compile(r"\bopen market\b", re.I)
_PRICE_RE = re.compile(r"price of\s*(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)", re.I)
_QTY_RE = re.compile(r"([\d,]+)\s*(?:\([^)]*\)\s*)?(?:fully paid-?up\s*)?Equity Shares", re.I)
_WITHDRAWN_RE = re.compile(r"\b(withdraw(?:al|n)?|laps(?:e|ed))\b", re.I)


def _clean(txt):
    s = str(txt or "").strip()
    return None if not s or s == "-" else s


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


def _extract_route(text: str) -> str | None:
    if _TENDER_RE.search(text):
        return "TENDER"
    if _OPEN_MARKET_RE.search(text):
        return "OPEN_MARKET"
    return None


def _merge(ca_rows: list, ann_rows: list, daily_rows: list) -> dict:
    """Per-symbol merge of the three sources. A field absent from every contributing row stays
    absent - never fabricated."""
    by_symbol: dict = {}

    def slot(symbol: str, company) -> dict:
        d = by_symbol.setdefault(symbol, {"company": None, "record_date": None, "ex_date": None,
                                          "face_value": None, "route": None,
                                          "buyback_price": None, "shares_offered": None,
                                          "closed": False, "withdrawn": False, "open_today": False,
                                          "dates": []})
        if company and not d["company"]:
            d["company"] = str(company).strip()
        return d

    for row in ca_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        d = slot(symbol, row.get("comp"))
        rec, ex, fv = _clean(row.get("recDate")), _clean(row.get("exDate")), _clean(row.get("faceVal"))
        if rec and not d["record_date"]:
            d["record_date"] = rec
        if ex and not d["ex_date"]:
            d["ex_date"] = ex
        if fv and not d["face_value"]:
            d["face_value"] = fv
        parsed = _parse_date(rec) or _parse_date(ex)
        if parsed:
            d["dates"].append(parsed)

    for row in ann_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        desc = str(row.get("desc") or "")
        if not symbol or desc not in _ALL_DESC:
            continue
        d = slot(symbol, row.get("sm_name"))
        text = str(row.get("attchmntText") or "")
        if desc in _CLOSURE_DESC:
            d["closed"] = True
        if "buyback" in text.lower() and _WITHDRAWN_RE.search(text):
            d["withdrawn"] = True
        route = _extract_route(text)
        if route and not d["route"]:
            d["route"] = route
        price_m = _PRICE_RE.search(text)
        if price_m and not d["buyback_price"]:
            d["buyback_price"] = price_m.group(1).replace(",", "")
        qty_m = _QTY_RE.search(text)
        if qty_m and not d["shares_offered"] and "buyback" in text.lower():
            d["shares_offered"] = qty_m.group(1).replace(",", "")
        an_date = _parse_date(str(row.get("an_dt") or "").split(" ")[0])
        if an_date:
            d["dates"].append(an_date)

    for row in daily_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        d = slot(symbol, row.get("sm_name"))
        d["open_today"] = True
        an_date = _parse_date(str(row.get("an_dt") or "").split(" ")[0])
        if an_date:
            d["dates"].append(an_date)

    return by_symbol


def _status_for(d: dict) -> str:
    if d["withdrawn"]:
        return WITHDRAWN
    if d["closed"]:
        return COMPLETED
    if d["open_today"]:
        return OPEN
    return ANNOUNCED


def parse_buyback(ca_rows: list, ann_rows: list, daily_rows: list, now_iso: str,
                  lookup_fn=None) -> tuple:
    """(events, notes). Deterministic merge/classification only - never a network call."""
    try:
        today = dt.datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = dt.date.today()
    merged = _merge(ca_rows, ann_rows, daily_rows)
    events, notes = [], []
    for symbol, d in merged.items():
        if not d["company"]:
            notes.append(f"dropped row {symbol}: no company name stated")
            continue
        status = _status_for(d)
        data_as_of = (max(d["dates"]) if d["dates"] else today).isoformat()
        facts = [{"label": label, "value": d[key]} for label, key in
                (("record_date", "record_date"), ("ex_date", "ex_date"),
                 ("face_value", "face_value"), ("route", "route"),
                 ("buyback_price", "buyback_price"), ("shares_offered", "shares_offered"))
                if d.get(key)]
        existing = lookup_fn(BUYBACK, symbol) if lookup_fn else None
        if existing is not None:
            event_key = existing.event_key
        else:
            natural_id = hashlib.sha256(f"{symbol}|{BUYBACK}|{today.isoformat()}".encode()
                                       ).hexdigest()[:16]
            event_key = f"{BUYBACK}:{symbol}:{natural_id}"
        events.append(MarketEvent(
            schema_version=SCHEMA_VERSION, family=BUYBACK, event_key=event_key, symbol=symbol,
            company=d["company"], status=status, sub_type=None, data_as_of=data_as_of,
            source_name=S.SRC_NSE_CORPORATE_ACTIONS, source_reference=CORPORATE_ACTIONS_URL,
            source_text="", retrieved_at=now_iso or "", status_capture=SUCCESS, facts=facts,
            parser_version=PARSER_VERSION))
    return events, notes


def fetch_buyback(now_iso: str, lookup_fn=None, nse=None) -> FamilyFetchResult:
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
        ca_payload = nse.get("/api/corporates-corporateActions?index=equities")
    except Exception as exc:
        return FamilyFetchResult(status=SOURCE_UNAVAILABLE, events=[],
                                 connectivity=classify_exception(exc),
                                 reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    if not isinstance(ca_payload, list):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="corporateActions payload is not a list")
    if ca_payload and not any(isinstance(r, dict) and "subject" in r for r in ca_payload):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="corporateActions rows missing 'subject' key - shape change")
    ca_rows = [r for r in ca_payload if isinstance(r, dict)
              and str(r.get("subject", "")).strip() == "Buy Back"]

    try:
        ann_payload = nse.get("/api/corporate-announcements?index=equities")
    except Exception:
        ann_payload = []           # supplementary source - never fails the whole fetch
    ann_rows = ann_payload if isinstance(ann_payload, list) else []

    try:
        daily_payload = nse.get("/api/corporates-daily-buyback?")
        daily_rows = ((daily_payload or {}).get("data") or []
                     if isinstance(daily_payload, dict) else [])
    except Exception:
        daily_rows = []             # supplementary source - never fails the whole fetch

    events, notes = parse_buyback(ca_rows, ann_rows, daily_rows, now_iso, lookup_fn=lookup_fn)
    return FamilyFetchResult(status=SUCCESS, events=events, connectivity="REACHABLE",
                             reason="; ".join(notes)[:300])


fetch_buyback.needs_lookup = True

__all__ = ["CORPORATE_ACTIONS_URL", "ANNOUNCEMENTS_URL", "DAILY_BUYBACK_URL", "PARSER_VERSION",
           "parse_buyback", "fetch_buyback"]

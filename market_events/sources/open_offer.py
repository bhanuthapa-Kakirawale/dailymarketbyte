"""OPEN_OFFER adapter: NSE's general corporate-announcements feed
(`/api/corporate-announcements?index=equities`, filtered to the exact category
`desc == "Public Announcement-Open Offer"`). This is the SAME feed BUYBACK already reads as its
SECONDARY source (`buyback.py`'s `ANNOUNCEMENTS_URL`) - not a guessed endpoint. NSE's structured
corporate-actions feed (BUYBACK's PRIMARY source) carries no open-offer rows at all, even over a
full year - an open offer is acquirer-driven under SEBI (SAST) Regulations, not a company
corporate action, so it never appears there. NSE has no dedicated open-offer/takeover page or
API (confirmed by reading the "Corporate Filings - Offer Documents" page and its own JS
controller, which turned out to be about IPO/FPO prospectuses - a dead end). SEBI's own site
hosts only the underlying PDFs (Public Announcement / Detailed Public Statement / Letter of
Offer), no JSON/API. So this one `desc` category is the sole viable official, machine-readable
source. See docs/MARKET_EVENTS_ENGINE.md.

Every open-offer lifecycle filing - the initial Public Announcement, the Detailed Public
Statement, a draft/final Letter of Offer, a corrigendum, the offer-opening announcement
(Regulation 18(7) of SEBI (SAST) Regulations, 2011), the post-offer advertisement
(Regulation 18(12)), a withdrawal - shares this ONE `desc` value, so lifecycle stage can only be
read from the filing's own stated text/regulation, never from a separate categorical field like
BUYBACK's closure/announced split.

Event identity reuses the SAME `needs_lookup`/`lookup_fn` reconciliation EARNINGS/BUYBACK already
use: a symbol's latest NON-TERMINAL open-offer event is reused across multiple filings of one
cycle (real examples: G-TEC JAINX EDUCATION filed a Public Announcement on 29-Sep-2026 then a
Detailed Public Statement the very next day; Shankara Building Products filed a Public
Announcement on 15-Jul-2026 then an offer-opening announcement/corrigendum on 04-Sep-2026 - the
SAME cycle, 51 days apart). Once a cycle reaches a TERMINAL status (COMPLETED/WITHDRAWN), the
next genuinely new open offer for that symbol automatically mints a fresh `event_key` - no new
logic, the existing `_lookup_open_event` terminal-status filter already does this.

Because one cycle can span weeks or months, and each capture only requests a trailing
`lookback_days` window (not the whole feed every time), status is carried forward NON-
REGRESSIVELY: each event's own `facts` remember the most-advanced stage any filing has ever
stated for it (`stage_evidence`), and a later run that only re-sees an older, less-advanced
filing (or misses the window entirely) never downgrades it back.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from core import sources as S

from ..models import (ANNOUNCED, COMPLETED, OPEN, OPEN_OFFER, PARSE_ERROR, SCHEMA_VERSION,
                      SOURCE_UNAVAILABLE, SUCCESS, WITHDRAWN, FamilyFetchResult, MarketEvent)

ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
PARSER_VERSION = "open-offer-nse-announcements-1.0"
TARGET_DESC = "Public Announcement-Open Offer"
DEFAULT_LOOKBACK_DAYS = 10

# Stage rank - higher outranks lower; a later, less-advanced filing never downgrades a cycle.
_STAGE_RANK = {ANNOUNCED: 0, OPEN: 1, COMPLETED: 2, WITHDRAWN: 2}

_WITHDRAWN_RE = re.compile(r"\bwithdraw(?:al|n)?\b|\blapsed?\b", re.I)
_POST_OFFER_RE = re.compile(r"post[\s-]offer advertisement|regulation\s*18\s*\(?\s*12\s*\)?", re.I)
_OPENING_RE = re.compile(r"offer opening|regulation\s*18\s*\(?\s*7\s*\)?", re.I)
_ACQUIRER_RE = re.compile(r"made by\s+([A-Z][^,]{2,80}),\s*the acquirer", re.I)


def _clean(txt):
    s = str(txt or "").strip()
    return None if not s or s == "-" else s


def _parse_date(txt) -> dt.date | None:
    s = str(txt or "").strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s.title(), fmt).date()
        except ValueError:
            continue
    return None


def _stage_for_text(text: str) -> str:
    if _WITHDRAWN_RE.search(text):
        return WITHDRAWN
    if _POST_OFFER_RE.search(text):
        return COMPLETED
    if _OPENING_RE.search(text):
        return OPEN
    return ANNOUNCED


def _prior_stage(existing) -> str | None:
    if existing is None:
        return None
    for row in existing.facts or []:
        if isinstance(row, dict) and row.get("label") == "stage_evidence":
            return row.get("value")
    return existing.status if existing.status in _STAGE_RANK else None


def _advance(prior: str | None, candidate: str) -> str:
    if prior is None or prior not in _STAGE_RANK:
        return candidate
    if _STAGE_RANK[candidate] >= _STAGE_RANK[prior]:
        return candidate
    return prior


def parse_open_offer(rows: list, now_iso: str, lookup_fn=None) -> tuple:
    """(events, notes). Deterministic grouping/classification only - never a network call."""
    try:
        today = dt.datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = dt.date.today()

    by_symbol: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("desc", "")).strip() != TARGET_DESC:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        company = _clean(row.get("sm_name"))
        text = str(row.get("attchmntText") or "")
        an_date = _parse_date(str(row.get("an_dt") or "").split(" ")[0])
        d = by_symbol.setdefault(symbol, {"company": None, "stage": None, "acquirer": None,
                                          "source_text": "", "dates": []})
        if company and not d["company"]:
            d["company"] = company
        stage = _stage_for_text(text)
        d["stage"] = _advance(d["stage"], stage)
        m = _ACQUIRER_RE.search(text)
        if m and not d["acquirer"]:
            d["acquirer"] = m.group(1).strip()
        if text:
            d["source_text"] = text.strip()
        if an_date:
            d["dates"].append(an_date)

    events, notes = [], []
    for symbol, d in by_symbol.items():
        if not d["company"]:
            notes.append(f"dropped row {symbol}: no company name stated")
            continue
        existing = lookup_fn(OPEN_OFFER, symbol) if lookup_fn else None
        final_stage = _advance(_prior_stage(existing), d["stage"])
        acquirer = d["acquirer"] or (
            next((row.get("value") for row in (existing.facts or [])
                 if isinstance(row, dict) and row.get("label") == "acquirer"), None)
            if existing else None)
        if existing is not None:
            event_key = existing.event_key
        else:
            natural_id = hashlib.sha256(f"{symbol}|{OPEN_OFFER}|{today.isoformat()}".encode()
                                       ).hexdigest()[:16]
            event_key = f"{OPEN_OFFER}:{symbol}:{natural_id}"
        data_as_of = (max(d["dates"]) if d["dates"] else today).isoformat()
        facts = [{"label": "stage_evidence", "value": final_stage}]
        if acquirer:
            facts.append({"label": "acquirer", "value": acquirer})
        events.append(MarketEvent(
            schema_version=SCHEMA_VERSION, family=OPEN_OFFER, event_key=event_key, symbol=symbol,
            company=d["company"], status=final_stage, sub_type=None, data_as_of=data_as_of,
            source_name=S.SRC_NSE_SAST_ANNOUNCEMENTS, source_reference=ANNOUNCEMENTS_URL,
            source_text=d["source_text"], retrieved_at=now_iso or "", status_capture=SUCCESS,
            facts=facts, parser_version=PARSER_VERSION))
    return events, notes


def fetch_open_offer(now_iso: str, lookup_fn=None, nse=None,
                     lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> FamilyFetchResult:
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
    from_date = today - dt.timedelta(days=lookback_days)
    url = (f"/api/corporate-announcements?index=equities"
          f"&from_date={from_date.strftime('%d-%m-%Y')}&to_date={today.strftime('%d-%m-%Y')}")
    try:
        payload = nse.get(url)
    except Exception as exc:
        return FamilyFetchResult(status=SOURCE_UNAVAILABLE, events=[],
                                 connectivity=classify_exception(exc),
                                 reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    if not isinstance(payload, list):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="corporate-announcements payload is not a list")
    if payload and not any(isinstance(r, dict) and "desc" in r for r in payload):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="corporate-announcements rows missing 'desc' key - "
                                        "shape change")

    events, notes = parse_open_offer(payload, now_iso, lookup_fn=lookup_fn)
    return FamilyFetchResult(status=SUCCESS, events=events, connectivity="REACHABLE",
                             reason="; ".join(notes)[:300])


fetch_open_offer.needs_lookup = True

__all__ = ["ANNOUNCEMENTS_URL", "PARSER_VERSION", "TARGET_DESC", "DEFAULT_LOOKBACK_DAYS",
           "parse_open_offer", "fetch_open_offer"]

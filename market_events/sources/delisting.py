"""DELISTING adapter: NSE's general corporate-announcements feed
(`/api/corporate-announcements?index=equities`, the SAME feed BUYBACK/OPEN_OFFER already
read), filtered to `desc in {"Delisting", "Voluntary Delisting"}` - an explicit, NSE-
assigned classification rather than a guessed endpoint (confirmed live via this project's
own `market.NSE()` client: a 365-day pull on 2026-10-07 returned 17 real rows under these
two `desc` values). See docs/MARKET_EVENTS_ENGINE.md.

A second, TEXT-level guard (`"delist" in attchmntText.lower()`) is required alongside the
`desc` match: two of those 17 rows (KEI Industries 21-Jan-2026, Fedders Electric 05-Nov-2025)
carry `desc == "Voluntary Delisting"` but an `attchmntText` that is a plain, unrelated board-
meeting disclosure with no delisting wording at all - proof NSE's own category tag is
occasionally mismatched. A row passing `desc` but failing this text check is dropped, never
kept on tag alone.

VOLUNTARY vs COMPULSORY is read only from explicit text ("voluntary" / "compulsor(y|ily)"),
never inferred from circumstance. A meaningful third bucket exists in the live data: NCLT
Resolution-Plan-driven delistings (Jaiprakash Associates, Future Supply Chain Solutions,
Fedders Electric, Rolta India - all "Delisting of Equity shares... pursuant to Resolution
plan approved by... NCLT... under the Insolvency and Bankruptcy Code") state neither word -
these stay `delisting_type=UNKNOWN` with a `mechanism=NCLT_RESOLUTION_PLAN` fact rather than
being forced into either bucket.

Several real rows describe a delisting from an OTHER exchange (BSE / Calcutta Stock Exchange),
reported by NSE to its own members as an FYI - not a statement about the symbol's own NSE
listing (e.g. Atcom Technologies/Visesh Infotecnics "compulsorily delisted by BSE Limited";
IZMO/Jay Bharat Maruti/ITC "Voluntary Delisting from the Calcutta Stock Exchange"). An
`other_exchange` fact is populated only when the text explicitly names that other exchange;
its absence is never read as "this is an NSE delisting" - it is simply not stated either way.

Lifecycle: `WITHDRAWN` on explicit reversal text ("withdrawal of delisting" / "restore the
listing" - e.g. Gammon India, Era Infra, both reversing a PRIOR delisting via a SAT order/BSE
notice); else `SCHEDULED` when the text's own "w.e.f. <date>" is still in the future at
capture time, `COMPLETED` once that date has passed; else (no parseable effective date -
e.g. Jindal Photo's and Hitech Corp's bare "has informed the Exchange about Voluntary
Delisting") `ANNOUNCED`. Floor price, exit/discovered price, bidding dates and approval
dates are never stated in this feed's text and stay absent - not guessed, not sourced
elsewhere (NSE's XLSX-based voluntary-delisting-status rosters and "Orders of Delisting
Committee" page were investigated and intentionally left out of this pass - XLSX/static-
table only, no JSON API, live reachability unverified from this environment - see
docs/MARKET_EVENTS_ENGINE.md).

Event identity reuses the SAME `needs_lookup`/`lookup_fn` reconciliation EARNINGS/BUYBACK/
OPEN_OFFER already use: a symbol's latest NON-TERMINAL delisting event is reused across
multiple filings of one cycle (real examples: Hitech Corporation filed two identical same-day
Voluntary Delisting notices; Fedders Electric's Nov-2025 board-meeting disclosure and its
Feb-2026 final NCLT delisting notice are the SAME cycle). Once a cycle reaches a terminal
status (COMPLETED/WITHDRAWN), the next filing for that symbol mints a fresh `event_key` - this
also means a WITHDRAWN notice arriving after a prior COMPLETED record (Gammon India, Era
Infra) becomes its OWN event rather than mutating the completed one; both are real, distinct
historical facts about the symbol, and this avoids new generic reconciliation machinery.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from core import sources as S

from ..models import (ANNOUNCED, COMPLETED, DELISTING, PARSE_ERROR, SCHEDULED, SCHEMA_VERSION,
                      SOURCE_UNAVAILABLE, SUCCESS, WITHDRAWN, FamilyFetchResult, MarketEvent)

ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
PARSER_VERSION = "delisting-nse-announcements-1.0"
TARGET_DESC = frozenset({"Delisting", "Voluntary Delisting"})
DEFAULT_LOOKBACK_DAYS = 10

# Lifecycle rank - higher outranks lower; a later, less-advanced re-seen filing never
# downgrades a cycle (mirrors open_offer.py's _STAGE_RANK/_advance exactly).
_STAGE_RANK = {ANNOUNCED: 0, SCHEDULED: 1, COMPLETED: 2, WITHDRAWN: 2}

_WITHDRAWN_RE = re.compile(r"withdrawal of delisting|restore the listing", re.I)
_VOLUNTARY_RE = re.compile(r"\bvoluntary\b", re.I)
_COMPULSORY_RE = re.compile(r"compulsor(?:y|ily)", re.I)
_NCLT_RE = re.compile(r"\bNCLT\b|National Company Law Tribunal|Insolvency (?:and )?"
                      r"Bankruptcy Code|Resolution [Pp]lan", re.I)
_EFFECTIVE_DATE_RE = re.compile(r"w\.e\.f\.?\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})", re.I)
_OTHER_EXCHANGE_PATTERNS = (
    (re.compile(r"\bBSE Limited\b", re.I), "BSE"),
    (re.compile(r"Calcutta Stock Exchange", re.I), "CSE"),
    (re.compile(r"Metropolitan Stock Exchange", re.I), "MSE"),
)


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


def _parse_effective_date(text: str) -> dt.date | None:
    m = _EFFECTIVE_DATE_RE.search(text)
    if not m:
        return None
    s = m.group(1).replace(",", "").strip()
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _classify_type(text: str, desc: str) -> str:
    if _VOLUNTARY_RE.search(text):
        return "VOLUNTARY"
    if _COMPULSORY_RE.search(text):
        return "COMPULSORY"
    if desc == "Voluntary Delisting":
        # The category itself is an explicit NSE classification even when the filing's own
        # prose happens not to repeat the word (e.g. terse same-day re-filings).
        return "VOLUNTARY"
    return "UNKNOWN"


def _mechanism(text: str) -> str | None:
    return "NCLT_RESOLUTION_PLAN" if _NCLT_RE.search(text) else None


def _other_exchange(text: str) -> str | None:
    for pat, name in _OTHER_EXCHANGE_PATTERNS:
        if pat.search(text):
            return name
    return None


def _status_for(text: str, today: dt.date) -> str:
    if _WITHDRAWN_RE.search(text):
        return WITHDRAWN
    eff = _parse_effective_date(text)
    if eff is not None:
        return COMPLETED if eff <= today else SCHEDULED
    return ANNOUNCED


def _prior_stage(existing) -> str | None:
    if existing is None:
        return None
    for row in existing.facts or []:
        if isinstance(row, dict) and row.get("label") == "stage_evidence":
            return row.get("value")
    return existing.status if existing.status in _STAGE_RANK else None


def _prior_fact(existing, label: str):
    if existing is None:
        return None
    for row in existing.facts or []:
        if isinstance(row, dict) and row.get("label") == label:
            return row.get("value")
    return None


def _advance(prior: str | None, candidate: str) -> str:
    if prior is None or prior not in _STAGE_RANK:
        return candidate
    if _STAGE_RANK[candidate] >= _STAGE_RANK[prior]:
        return candidate
    return prior


def parse_delisting(rows: list, now_iso: str, lookup_fn=None) -> tuple:
    """(events, notes). Deterministic grouping/classification only - never a network call."""
    try:
        today = dt.datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = dt.date.today()

    by_symbol: dict = {}
    notes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("desc", "")).strip()
        if desc not in TARGET_DESC:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        text = str(row.get("attchmntText") or "")
        if "delist" not in text.lower():
            notes.append(f"dropped row {symbol}: desc={desc!r} but attchmntText doesn't "
                        f"mention delisting - likely a mistagged category")
            continue
        company = _clean(row.get("sm_name"))
        an_date = _parse_date(str(row.get("an_dt") or "").split(" ")[0])
        d = by_symbol.setdefault(symbol, {"company": None, "stage": None, "dtype": None,
                                          "mechanism": None, "other_exchange": None,
                                          "source_text": "", "dates": []})
        if company and not d["company"]:
            d["company"] = company
        d["stage"] = _advance(d["stage"], _status_for(text, today))
        dtype_candidate = _classify_type(text, desc)
        if d["dtype"] in (None, "UNKNOWN"):
            d["dtype"] = dtype_candidate
        mech = _mechanism(text)
        if mech and not d["mechanism"]:
            d["mechanism"] = mech
        exch = _other_exchange(text)
        if exch and not d["other_exchange"]:
            d["other_exchange"] = exch
        if text:
            d["source_text"] = text.strip()
        if an_date:
            d["dates"].append(an_date)

    events = []
    for symbol, d in by_symbol.items():
        if not d["company"]:
            notes.append(f"dropped row {symbol}: no company name stated")
            continue
        existing = lookup_fn(DELISTING, symbol) if lookup_fn else None
        final_stage = _advance(_prior_stage(existing), d["stage"])
        dtype = d["dtype"]
        if dtype in (None, "UNKNOWN"):
            prior_type = _prior_fact(existing, "delisting_type")
            if prior_type and prior_type != "UNKNOWN":
                dtype = prior_type
        dtype = dtype or "UNKNOWN"
        mechanism = d["mechanism"] or _prior_fact(existing, "mechanism")
        other_exchange = d["other_exchange"] or _prior_fact(existing, "other_exchange")

        if existing is not None:
            event_key = existing.event_key
        else:
            natural_id = hashlib.sha256(f"{symbol}|{DELISTING}|{today.isoformat()}".encode()
                                       ).hexdigest()[:16]
            event_key = f"{DELISTING}:{symbol}:{natural_id}"

        data_as_of = (max(d["dates"]) if d["dates"] else today).isoformat()
        facts = [{"label": "stage_evidence", "value": final_stage},
                 {"label": "delisting_type", "value": dtype}]
        if mechanism:
            facts.append({"label": "mechanism", "value": mechanism})
        if other_exchange:
            facts.append({"label": "other_exchange", "value": other_exchange})

        events.append(MarketEvent(
            schema_version=SCHEMA_VERSION, family=DELISTING, event_key=event_key, symbol=symbol,
            company=d["company"], status=final_stage, sub_type=None, data_as_of=data_as_of,
            source_name=S.SRC_NSE_DELISTING_ANNOUNCEMENTS, source_reference=ANNOUNCEMENTS_URL,
            source_text=d["source_text"], retrieved_at=now_iso or "", status_capture=SUCCESS,
            facts=facts, parser_version=PARSER_VERSION))
    return events, notes


def fetch_delisting(now_iso: str, lookup_fn=None, nse=None,
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

    events, notes = parse_delisting(payload, now_iso, lookup_fn=lookup_fn)
    return FamilyFetchResult(status=SUCCESS, events=events, connectivity="REACHABLE",
                             reason="; ".join(notes)[:300])


fetch_delisting.needs_lookup = True

__all__ = ["ANNOUNCEMENTS_URL", "PARSER_VERSION", "TARGET_DESC", "DEFAULT_LOOKBACK_DAYS",
           "parse_delisting", "fetch_delisting"]

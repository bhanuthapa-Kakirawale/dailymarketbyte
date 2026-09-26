"""IPO sources: the exchange's issue lists and the SEBI-filed offer documents. Gemini is never an
IPO source; grey-market sites are never read.

    NSE (website API, undocumented - parsers tested on synthetic, structurally identical rows)
        /api/ipo-current-issue           companyName, symbol, series (EQ/SME), issueStartDate,
                                         issueEndDate, issuePrice ("Rs.258 to Rs.272"),
                                         issueSize (SHARES offered - not rupees), noOfTime
        /api/all-upcoming-issues?category=ipo   same shape, no bid data
        /api/public-past-issues          ipoStartDate/ipoEndDate, listingDate, issuePrice,
                                         priceRange, securityType
      These rows carry NO update timestamp: bid multiples read from them have no DATA AS OF and
      are therefore NOT published (recorded as a note). Issue size in shares is never converted
      into rupees (that would be a derived, unofficial figure).

    SEBI offer documents: `data/ipo_offer_documents.json`, entered by hand from the RHP/DRHP
      filed on sebi.gov.in. Every figure carries its document and page reference; the loader
      fails closed on anything incomplete or off-host.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from urllib.parse import urlparse

from core import sources as S

from .models import BoardType, FinancialRow, IPOEvent, IPOStatus, RiskFact, Subscription

NSE_IPO_PAGE = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
_BAND = re.compile(r"Rs\.?\s*([\d,.]+)\s*(?:to|-)\s*Rs\.?\s*([\d,.]+)", re.I)
_SINGLE = re.compile(r"^\s*(?:Rs\.?\s*)?([\d,.]+)\s*$")
OFFICIAL_DOC_HOSTS = ("sebi.gov.in", "www.sebi.gov.in", "nseindia.com", "www.nseindia.com",
                      "nsearchives.nseindia.com", "bseindia.com", "www.bseindia.com")
DOC_TYPES = ("DRHP", "RHP", "PROSPECTUS", "CORRIGENDUM", "ADDENDUM")
DEFAULT_DOC_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "data", "ipo_offer_documents.json")


def _d(txt) -> dt.date | None:
    s = str(txt or "").strip()
    if not s or s == "-":
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s.title(), fmt).date()
        except ValueError:
            continue
    return None


def _band(txt):
    m = _BAND.search(str(txt or ""))
    if m:
        lo, hi = (float(v.replace(",", "")) for v in m.groups())
        return (lo, hi) if lo <= hi else (None, None)
    m = _SINGLE.match(str(txt or ""))
    if m:
        v = float(m.group(1).replace(",", ""))
        return v, v
    return None, None


def _board(row) -> BoardType | None:
    s = str(row.get("series") or row.get("securityType") or "").strip().upper()
    return {"EQ": BoardType.MAINBOARD, "SME": BoardType.SME, "SM": BoardType.SME,
            "ST": BoardType.SME}.get(s)


def parse_nse_issues(rows, list_name: str, data_as_of: dt.date, retrieved_at: str | None = None):
    """(events, notes). `list_name`: CURRENT / UPCOMING / PAST. A row missing its company,
    board or dates is dropped with a note - never completed by guesswork."""
    events, notes = {}, []
    if not isinstance(rows, list):
        return [], [f"{list_name}: payload is not a list"]
    for r in rows:
        name = str(r.get("companyName") or r.get("company") or "").strip()
        board = _board(r)
        start = _d(r.get("issueStartDate") or r.get("ipoStartDate"))
        end = _d(r.get("issueEndDate") or r.get("ipoEndDate"))
        if not name or board is None or start is None or end is None or end < start:
            notes.append(f"{list_name}: dropped row {name or '?'} (missing/invalid company, "
                         "board or dates)")
            continue
        sym = str(r.get("symbol") or "").strip() or None
        key = sym or name
        lo, hi = _band(r.get("issuePrice") if list_name != "PAST" else r.get("priceRange"))
        listing = _d(r.get("listingDate"))
        status = (IPOStatus.LISTED if listing and listing <= data_as_of else
                  IPOStatus.LISTING if listing else
                  IPOStatus.OPEN if start <= data_as_of <= end else
                  IPOStatus.UPCOMING if data_as_of < start else IPOStatus.CLOSED)
        ev = events.get(key) or IPOEvent(
            company_name=name, board_type=board, status=status, source_name=S.SRC_NSE_IPO,
            source_reference=NSE_IPO_PAGE, data_as_of=data_as_of, symbol=sym,
            issue_open_date=start, issue_close_date=end, listing_date=listing,
            price_band_low=lo, price_band_high=hi, retrieved_at=retrieved_at)
        if r.get("noOfTime") not in (None, "", "-"):
            # bid multiple WITHOUT an exchange timestamp: kept for audit, never published
            ev.notes.append(f"NSE bid multiple {r.get('noOfTime')} ({r.get('category')}) has no "
                            "update timestamp in this list - not published")
        events[key] = ev
    return list(events.values()), notes


# --------------------------------------------------------------------------- offer documents
def load_offer_documents(path: str = DEFAULT_DOC_FILE) -> tuple:
    """(entries, audit). Fails closed per entry: an off-host source, an unknown document type,
    a figure without its page reference -> that entry is rejected (recorded), never partially
    used. A missing file means no document facts - never invented ones."""
    audit = {"path": path, "status": "OK", "rejected": []}
    if not os.path.exists(path):
        audit["status"] = "UNAVAILABLE"
        return [], audit
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        audit["status"] = f"INVALID: {type(exc).__name__}"
        return [], audit
    good = []
    for e in data.get("entries") or []:
        why = _reject_reason(e)
        if why:
            audit["rejected"].append({"company_name": e.get("company_name"), "reason": why})
        else:
            good.append(e)
    return good, audit


def _reject_reason(e: dict) -> str | None:
    for k in ("company_name", "document_type", "document_date", "source_url"):
        if not e.get(k):
            return f"missing {k}"
    if e["document_type"] not in DOC_TYPES:
        return f"unknown document type {e['document_type']}"
    host = urlparse(e["source_url"]).netloc.lower()
    if host not in OFFICIAL_DOC_HOSTS:
        return f"source host {host!r} is not an official filing host"
    for sec in ("financials", "risk_facts", "issue_structure"):
        for item in e.get(sec) or []:
            if not item.get("page_reference"):
                return f"{sec} item without page_reference"
    return None


def apply_offer_document(ev: IPOEvent, entry: dict) -> IPOEvent:
    """Merge SEBI-document figures into an event (never overwriting an exchange date/band)."""
    doc = f"{entry['document_type']} dated {entry['document_date']}"
    ev.official_document_reference = f"{doc} ({entry['source_url']})"
    for item in entry.get("issue_structure") or []:
        k, v = item.get("key"), item.get("value_crore")
        if k in ("fresh_issue_crore", "ofs_crore", "issue_size_crore") and v is not None:
            setattr(ev, k, float(v))
        if k == "lot_size" and item.get("value") is not None:
            ev.lot_size = int(item["value"])
    ev.objects_of_issue = list(entry.get("objects_of_issue") or [])
    ev.financials = [FinancialRow(f["label"], f["period"], float(f["value_crore"]),
                                  entry["document_type"], f["page_reference"])
                     for f in entry.get("financials") or []]
    ev.risk_facts = [RiskFact(r["category"], r["text"], entry["document_type"],
                              r["page_reference"]) for r in entry.get("risk_facts") or []]
    return ev


def fetch_nse_issues(data_as_of: dt.date, now_iso: str, nse=None) -> tuple:
    """Production-only fetch through the existing NSE client; fails closed per list."""
    events, notes = [], []
    try:
        import market
        nse = nse or market.NSE()
    except Exception as exc:
        return [], [f"NSE client unavailable: {exc}"]
    for path, name in (("/api/ipo-current-issue", "CURRENT"),
                       ("/api/all-upcoming-issues?category=ipo", "UPCOMING")):
        try:
            evs, n = parse_nse_issues(nse.get(path), name, data_as_of, now_iso)
            events += evs
            notes += n
        except Exception as exc:
            notes.append(f"{name}: UNAVAILABLE ({type(exc).__name__})")
    return dedupe(events), notes


def dedupe(events) -> list:
    """One event per issue: the exchange lists the same IPO in more than one list (current AND
    upcoming). Keyed by symbol, else company name; the first (current-issue) reading wins."""
    seen, out = set(), []
    for e in events:
        key = (e.symbol or e.company_name).upper()
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


__all__ = ["parse_nse_issues", "dedupe", "load_offer_documents", "apply_offer_document",
           "fetch_nse_issues", "NSE_IPO_PAGE", "OFFICIAL_DOC_HOSTS", "DEFAULT_DOC_FILE"]

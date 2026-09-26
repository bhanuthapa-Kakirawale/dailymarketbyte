"""IPO WATCH rules: validation, deterministic selection, public facts and the display models.

Selection (never "popularity", never a model): an IPO qualifies only with a dated event today -
OPENS / CLOSES / LISTS / ALLOTMENT - (or, in POST, its listing session was the session). Order:
    1. today's event kind (LISTS, CLOSES, OPENS, ALLOTMENT)
    2. Mainboard before SME (broad audience)
    3. larger OFFICIAL issue size (SEBI document), unknown last
    4. company name
One IPO -> an IPO WATCH card; several -> one compact PRIMARY MARKET board. Never "Top IPOs".
"""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

from config import fmt_in
from core import sources as S
from presentation.provenance_label import ProvenanceLabel, fmt_date, fmt_datetime
from publication.classification import (ContentClass, Orientation, Origin, PublishableFact,
                                        RightsStatus, Scope)
from publication.rights import rights_for

from .models import BoardType, IPOEvent

EVENT_ORDER = ("LISTS_TODAY", "CLOSES_TODAY", "OPENS_TODAY", "ALLOTMENT_TODAY")
EVENT_CHIP = {"LISTS_TODAY": "LISTS TODAY", "CLOSES_TODAY": "CLOSES TODAY",
              "OPENS_TODAY": "OPENS TODAY", "ALLOTMENT_TODAY": "ALLOTMENT TODAY",
              "LISTED": "LISTED"}
# POST describes a session that is over: the same dated events, in the past tense
EVENT_CHIP_POST = {"LISTS_TODAY": "LISTED", "CLOSES_TODAY": "BIDDING CLOSED",
                   "OPENS_TODAY": "BIDDING OPENED", "ALLOTMENT_TODAY": "ALLOTMENT",
                   "LISTED": "LISTED"}


# "<Two> IPOs ..." - the same dated events as a plural sentence (PRE present, POST past)
PLURAL = {False: {"LISTS_TODAY": "list today", "CLOSES_TODAY": "close today",
                  "OPENS_TODAY": "open today", "ALLOTMENT_TODAY": "have allotment today",
                  "LISTED": "listed"},
          True: {"LISTS_TODAY": "listed", "CLOSES_TODAY": "closed for bidding",
                 "OPENS_TODAY": "opened for bidding", "ALLOTMENT_TODAY": "had allotment",
                 "LISTED": "listed"}}


def chip_for(kind: str, mode: str) -> str:
    return (EVENT_CHIP_POST if mode == "POST" else EVENT_CHIP)[kind]


MAX_BOARD = 4
try:                       # the renderer's rupee sign ("Rs " when the font lacks the glyph)
    from video import RS
except Exception:          # pragma: no cover
    RS = "Rs "


def _rs(v: float, dec: int = 0) -> str:
    return f"{RS}{fmt_in(v, dec)}"


def todays_event(ipo: IPOEvent, day: dt.date) -> str | None:
    if ipo.listing_date == day:
        return "LISTS_TODAY"
    if ipo.issue_close_date == day:
        return "CLOSES_TODAY"
    if ipo.issue_open_date == day:
        return "OPENS_TODAY"
    if ipo.allotment_date == day:
        return "ALLOTMENT_TODAY"
    return None


# the dated primary-market events derivable from a stored snapshot, named for the audit
# (selection below keeps its own display kinds; nothing here judges an issue)
DERIVED_EVENT = {"OPENS_TODAY": "OPENS_TODAY", "CLOSES_TODAY": "CLOSES_TODAY",
                 "LISTS_TODAY": "LISTING_TODAY", "ALLOTMENT_TODAY": "ALLOTMENT_EVENT"}


def derive_events(ipos, day: dt.date) -> list:
    """Every dated event the exchange's own dates put on `day`, AFTER capture: OPENS_TODAY /
    CLOSES_TODAY / LISTING_TODAY / ALLOTMENT_EVENT, plus SUBSCRIPTION_UPDATE only for bid data
    carrying the exchange's own timestamp (the NSE issue lists carry none, so never from them).
    No classification of any kind (good / popular / attractive) - dates and sources only."""
    out = []
    for ipo in ipos:
        for when, kind in ((ipo.issue_open_date, "OPENS_TODAY"),
                           (ipo.issue_close_date, "CLOSES_TODAY"),
                           (ipo.listing_date, "LISTS_TODAY"),
                           (ipo.allotment_date, "ALLOTMENT_TODAY")):
            if when == day:
                out.append({"event_type": DERIVED_EVENT[kind], "company": ipo.company_name,
                            "symbol": ipo.symbol, "board": ipo.board_type.value,
                            "date": day.isoformat(), "source_reference": ipo.source_reference})
        sub = ipo.subscription
        if sub is not None and sub.as_of is not None and sub.as_of.date() == day:
            out.append({"event_type": "SUBSCRIPTION_UPDATE", "company": ipo.company_name,
                        "symbol": ipo.symbol, "board": ipo.board_type.value,
                        "date": day.isoformat(), "as_of": sub.as_of.isoformat(),
                        "source_reference": sub.source_reference})
    return out


def validate(ipo: IPOEvent) -> IPOEvent:
    """Drop (and note) anything that is not an official, dated, self-consistent fact."""
    ipo = replace(ipo, notes=list(ipo.notes))
    if (ipo.price_band_low is not None and ipo.price_band_high is not None
            and ipo.price_band_low > ipo.price_band_high):
        ipo.notes.append("price band inverted - omitted")
        ipo.price_band_low = ipo.price_band_high = None
    if ipo.issue_open_date and ipo.issue_close_date and ipo.issue_close_date < ipo.issue_open_date:
        ipo.notes.append("issue dates inverted - IPO not published")
        ipo.validation_status = "REJECTED"
        return ipo
    sub = ipo.subscription
    if sub is not None and (sub.as_of is None or not sub.source_reference):
        ipo.notes.append("subscription without an exchange timestamp/reference - omitted")
        ipo.subscription = None
    if ipo.listing is not None and (ipo.listing.session is None or ipo.listing_date is None
                                    or ipo.listing.session < ipo.listing_date):
        ipo.notes.append("listing outcome before the listing session - omitted")
        ipo.listing = None
    ipo.validation_status = "VALIDATED" if ipo.source_reference and ipo.company_name else "REJECTED"
    return ipo


def select_ipos(ipos, day: dt.date, include_listed_session: bool = False) -> tuple:
    """(chosen [(ipo, event_kind)], omitted [{company, reason}])."""
    from .sources import dedupe
    chosen, omitted = [], []
    for ipo in dedupe(ipos):
        ipo = validate(ipo)
        if ipo.validation_status != "VALIDATED":
            omitted.append({"company": ipo.company_name, "reason": "; ".join(ipo.notes)})
            continue
        if include_listed_session and ipo.listing and ipo.listing.session == day:
            kind = "LISTED"             # POST: the listing session is over - historical facts
        else:
            kind = todays_event(ipo, day)
        if kind is None:
            omitted.append({"company": ipo.company_name, "reason": f"no dated event on {day}"})
            continue
        chosen.append((ipo, kind))
    rank = {k: i for i, k in enumerate(("LISTED",) + EVENT_ORDER)}
    chosen.sort(key=lambda p: (rank[p[1]], p[0].board_type is not BoardType.MAINBOARD,
                               -(p[0].issue_size_crore or -1), p[0].company_name))
    for ipo, kind in chosen[MAX_BOARD:]:
        omitted.append({"company": ipo.company_name, "reason": f"board limit {MAX_BOARD}"})
    return chosen[:MAX_BOARD], omitted


# --------------------------------------------------------------------------- rows (display)
def fact_rows(ipo: IPOEvent, kind: str) -> list:
    """[{label, value, source_name, reference, as_of}] - each row one official fact."""
    rows = []
    ref, src = ipo.source_reference, ipo.source_name
    doc_ref = ipo.official_document_reference or ""
    if kind == "LISTED" and ipo.listing is not None:
        # after the listing session: the historical listing facts ARE the story
        L = ipo.listing
        rows.append({"label": "ISSUE PRICE", "value": _rs(L.issue_price),
                     "source_name": L.source_name, "reference": L.source_reference})
        for label, price in (("LISTING PRICE", L.listing_price), ("CLOSE", L.close_price)):
            ch = L.change_pct(price)
            if price is not None and ch is not None:
                rows.append({"label": label, "value": f"{_rs(price, 2)} ({ch:+.2f}% vs issue)",
                             "source_name": L.source_name, "reference": L.source_reference})
        return rows
    if ipo.price_band_low is not None and ipo.price_band_high is not None:
        v = (_rs(ipo.price_band_low) if ipo.price_band_low == ipo.price_band_high
             else f"{_rs(ipo.price_band_low)} - {_rs(ipo.price_band_high)}")
        rows.append({"label": "PRICE BAND", "value": v, "source_name": src, "reference": ref})
    if ipo.issue_open_date and ipo.issue_close_date:
        a, b = ipo.issue_open_date, ipo.issue_close_date
        rows.append({"label": "BIDDING", "value": f"{a:%d %b} - {b:%d %b}".upper(),
                     "source_name": src, "reference": ref})
    if ipo.lot_size:
        rows.append({"label": "LOT SIZE", "value": f"{ipo.lot_size} shares",
                     "source_name": S.SRC_SEBI_OFFER_DOC, "reference": doc_ref})
    if ipo.fresh_issue_crore is not None or ipo.ofs_crore is not None:
        parts = []
        if ipo.fresh_issue_crore is not None:
            parts.append(f"Fresh {_rs(ipo.fresh_issue_crore)} cr")
        if ipo.ofs_crore is not None:
            parts.append(f"OFS {_rs(ipo.ofs_crore)} cr")
        rows.append({"label": "ISSUE", "value": " · ".join(parts),
                     "source_name": S.SRC_SEBI_OFFER_DOC, "reference": doc_ref})
    sub = ipo.subscription
    if sub is not None and sub.total is not None:
        rows.append({"label": "TOTAL BIDS", "value": f"{sub.total:.2f}×",
                     "source_name": sub.source_name, "reference": sub.source_reference,
                     "as_of": sub.as_of})
        if sub.retail is not None:
            rows.append({"label": "RETAIL BIDS", "value": f"{sub.retail:.2f}×",
                         "source_name": sub.source_name, "reference": sub.source_reference,
                         "as_of": sub.as_of})
    return rows[:5]


def _provenance(ipo: IPOEvent, rows: list) -> ProvenanceLabel:
    labels = []
    for r in rows:
        lab = {S.SRC_NSE_IPO: "NSE", S.SRC_SEBI_OFFER_DOC: "SEBI offer document"}.get(
            r["source_name"], "NSE")
        if lab not in labels:
            labels.append(lab)
    as_ofs = [r["as_of"] for r in rows if r.get("as_of")]
    roles = ()
    if len(labels) > 1:
        roles = tuple((("ISSUE DATA" if lab == "NSE" else "OFFER DOCUMENT"),
                       ("NSE" if lab == "NSE" else "SEBI filing")) for lab in labels)
    as_of = fmt_datetime(max(as_ofs)) if as_ofs else fmt_date(ipo.data_as_of)
    return ProvenanceLabel(source=" · ".join(labels) or "NSE", data_as_of=as_of, roles=roles)


def build_card(ipo: IPOEvent, kind: str, mode: str = "PRE") -> dict:
    rows = fact_rows(ipo, kind)
    prov = _provenance(ipo, rows)
    return {"name": ipo.company_name, "board": ipo.board_type.value, "chip": chip_for(kind, mode),
            "rows": [{"label": r["label"], "value": r["value"]} for r in rows],
            "provenance": prov.to_dict(), "provenance_lines": prov.lines(), "kind": kind}


def build_model(chosen, mode: str = "PRE") -> dict | None:
    if not chosen:
        return None
    if len(chosen) == 1:
        ipo, kind = chosen[0]
        card = build_card(ipo, kind, mode)
        return {"layout": "CARD", "headline": f"{ipo.company_name}: IPO {card['chip'].lower()}",
                "card": card, "provenance_lines": card["provenance_lines"],
                "sources": sorted({r["source_name"] for r in fact_rows(ipo, kind)
                                   if r.get("source_name")} | {ipo.source_name})}
    word = {2: "Two", 3: "Three", 4: "Four"}[len(chosen)]
    rows = [{"name": ipo.company_name, "board": ipo.board_type.value, "chip": chip_for(k, mode)}
            for ipo, k in chosen]
    kinds = {k for _, k in chosen}
    when = "today" if mode != "POST" else "in the session"
    headline = (f"{word} IPOs {PLURAL[mode == 'POST'][next(iter(kinds))]}" if len(kinds) == 1
                else f"{word} primary-market events {when}")
    prov = ProvenanceLabel(source="NSE", data_as_of=fmt_date(chosen[0][0].data_as_of))
    return {"layout": "BOARD", "headline": headline, "rows": rows,
            "sources": sorted({ipo.source_name for ipo, _ in chosen}),
            "provenance": prov.to_dict(), "provenance_lines": prov.lines()}


def ipo_facts(chosen, mode: str = "PRE") -> list:
    """PublishableFacts: IPO scope, official origin (exchange / SEBI filing), IPO_EVENT."""
    out = []
    for ipo, kind in chosen:
        origin = Origin.OFFICIAL_EXCHANGE
        texts = [ipo.company_name, chip_for(kind, mode)] + [f"{r['label']} {r['value']}"
                                                       for r in fact_rows(ipo, kind)]
        for i, text in enumerate(texts):
            src = S.SRC_NSE_IPO
            out.append(PublishableFact(
                fact_id=f"ipo.{ipo.symbol or ipo.company_name}.{i}", text=text, scope=Scope.IPO,
                origin=origin, content_class=ContentClass.IPO_EVENT,
                orientation=Orientation.SCHEDULED_EVENT if kind != "LISTED" else Orientation.HISTORICAL,
                source_name=src, source_label="NSE", source_reference=ipo.source_reference,
                official_document_reference=ipo.official_document_reference,
                data_as_of=ipo.data_as_of, retrieved_at=ipo.retrieved_at,
                security=ipo.company_name,
                publication_rights_status=rights_for(src).status if src else RightsStatus.UNKNOWN,
                verification_status=ipo.validation_status, section="IPO_WATCH"))
        for f in ipo.financials:
            out.append(PublishableFact(
                fact_id=f"ipo.{ipo.symbol or ipo.company_name}.fin.{f.label}.{f.period}",
                text=f"{f.label} {f.period} {_rs(f.value_crore)} cr", scope=Scope.IPO,
                origin=Origin.OFFICIAL_COMPANY, content_class=ContentClass.FINANCIAL_STATISTIC,
                orientation=Orientation.HISTORICAL, source_name=S.SRC_SEBI_OFFER_DOC,
                source_label="SEBI offer document",
                official_document_reference=f"{ipo.official_document_reference} {f.page_reference}",
                data_as_of=ipo.data_as_of, security=ipo.company_name,
                publication_rights_status=rights_for(S.SRC_SEBI_OFFER_DOC).status,
                section="IPO_WATCH"))
    return out


def ipo_audit(chosen, omitted) -> dict:
    return {"ipo_content_present": bool(chosen),
            "ipo_companies": [ipo.company_name for ipo, _ in chosen],
            "ipo_events": {ipo.company_name: k for ipo, k in chosen},
            "ipo_sources": sorted({ipo.source_reference for ipo, _ in chosen}
                                  | {ipo.official_document_reference for ipo, _ in chosen
                                     if ipo.official_document_reference}),
            "subscription_data_as_of": {ipo.company_name: ipo.subscription.as_of.isoformat()
                                        for ipo, _ in chosen
                                        if ipo.subscription and ipo.subscription.as_of},
            "official_facts_used": {ipo.company_name: [r["label"] for r in fact_rows(ipo, k)]
                                    for ipo, k in chosen},
            "notes": {ipo.company_name: ipo.notes for ipo, _ in chosen if ipo.notes},
            "omitted": omitted}


__all__ = ["todays_event", "derive_events", "DERIVED_EVENT", "validate", "select_ipos", "fact_rows", "build_card", "build_model",
           "ipo_facts", "chip_for", "ipo_audit", "EVENT_CHIP", "MAX_BOARD"]

"""The public-intelligence sections shared by POST and PRE: UNDER THE SURFACE (Market Structure),
EXCHANGE WATCH and IPO WATCH - each admitted fact-by-fact through the publication gate BEFORE a
scene spec exists, each with its visible provenance.

    PublicIntelligence    the inputs (already acquired + validated upstream; nothing here fetches)
    plan_public_sections  -> PublicSections (models, reasons, omitted, audit blocks)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from presentation.provenance_label import ProvenanceLabel, fmt_date, session_label
from publication.classification import Scope
from publication.classify import report_fact


@dataclass
class PublicIntelligence:
    structure: object | None = None          # market_structure.MarketStructureSnapshot
    exchange_events: list = field(default_factory=list)   # validated ExchangeEvent
    exchange_sources: list = field(default_factory=list)  # SourceResult (audit)
    ipos: list = field(default_factory=list)              # IPOEvent
    known_securities: dict = field(default_factory=dict)  # symbol -> company (universe)
    universe_symbols: set = field(default_factory=set)
    synthetic: bool = False
    notes: list = field(default_factory=list)


@dataclass
class PublicSections:
    structure: list = field(default_factory=list)         # [(insight, provenance lines)]
    exchange: dict | None = None
    ipo: dict | None = None
    reasons: dict = field(default_factory=dict)
    omitted: list = field(default_factory=list)
    audit: dict = field(default_factory=dict)


def plan_public_sections(gate, intel: PublicIntelligence | None, day: dt.date, mode: str,
                         nifty_pct: float | None = None, max_structure: int = 2,
                         include_ipo_listed: bool = False) -> PublicSections:
    import market_structure as ms
    from exchange_watch import build_model, exchange_facts, select_events
    from ipo_watch import build_model as ipo_model, ipo_audit, ipo_facts, select_ipos
    from market_structure.store import SOURCE_LABEL, structure_facts

    out = PublicSections()
    intel = intel or PublicIntelligence()

    # ------------------------------------------------------------ UNDER THE SURFACE
    snap = intel.structure
    insights, reasons = ms.select_insights(snap, nifty_pct, limit=max_structure)
    out.reasons.update({f"STRUCTURE.{k}": v for k, v in reasons.items()})
    session = dt.date.fromisoformat(snap.session_date) if snap is not None else day
    for ins in insights:
        facts = structure_facts(ins, snap, session)
        if all(gate.admit(f) for f in facts):
            out.structure.append((ins, session_label(SOURCE_LABEL, session).lines()))
        else:
            out.omitted.append({"section": "UNDER THE SURFACE", "item": ins.kind,
                                "reason": "publication gate refused a statement"})
    out.audit["market_structure"] = (
        {"present": bool(out.structure), "universe": snap.universe_label,
         "constituent_count": snap.constituent_count,
         "coverage": {k: {"coverage_pct": m.coverage_pct, "status": m.status,
                          "denominator_text": m.denominator_text}
                      for k, m in snap.metrics.items()},
         "sector_mapping": snap.sector_mapping, "universe_source": snap.universe_source,
         "shown": [i.to_dict() for i, _ in out.structure]}
        if snap is not None else {"present": False, "reason": "no snapshot"})

    # ------------------------------------------------------------ EXCHANGE WATCH
    chosen, ex_omit = select_events(intel.exchange_events, intel.universe_symbols)
    admitted = []
    for e in chosen:
        if intel.known_securities.get(e.symbol) and not e.company:
            from dataclasses import replace
            e = replace(e, company=intel.known_securities[e.symbol])
        if all(gate.admit(f) for f in exchange_facts([e])):
            admitted.append(e)
        else:
            ex_omit.append({"event_id": e.event_id, "reason": "publication gate refused"})
    out.exchange = build_model(admitted, mode)
    out.reasons["EXCHANGE_WATCH"] = (f"included: {len(admitted)} official exchange event(s)"
                                     if admitted else "omitted: no validated official exchange "
                                                      "event for this date")
    out.omitted += [{"section": "EXCHANGE WATCH", **o} for o in ex_omit]
    out.audit["exchange_watch"] = {
        "present": bool(admitted), "events": [e.to_dict() for e in admitted],
        "sources": [s.to_dict() if hasattr(s, "to_dict") else s for s in intel.exchange_sources],
        "omitted": ex_omit}

    # ------------------------------------------------------------ IPO WATCH
    ipos, ipo_omit = select_ipos(intel.ipos, day, include_listed_session=include_ipo_listed)
    ok = []
    for pair in ipos:
        if all(gate.admit(f) for f in ipo_facts([pair], mode)):
            ok.append(pair)
        else:
            ipo_omit.append({"company": pair[0].company_name, "reason": "publication gate refused"})
    out.ipo = ipo_model(ok, mode)
    out.reasons["IPO_WATCH"] = (f"included: {len(ok)} IPO event(s) dated {day}" if ok else
                                f"omitted: no official IPO event dated {day}")
    out.omitted += [{"section": "IPO WATCH", **o} for o in ipo_omit]
    out.audit["ipo"] = ipo_audit(ok, ipo_omit)
    return out


# --------------------------------------------------------------------------- existing sections
def section_provenance(report, fact_ids, session: dt.date, kind: str = "SESSION") -> dict:
    """SOURCE / DATA AS OF for a POST/PRE section built from canonical report facts: the non-AI
    sources that actually back the fact, and the session it describes (index closes are as of
    the 3:30 PM IST close; flows/global cues are dated by session only)."""
    f = report_fact(report, fact_ids, "", Scope.INDEX, "", "prov")
    src = f.source_label or "NSE"
    label = (session_label(src, session) if kind == "SESSION"
             else ProvenanceLabel(source=src, data_as_of=fmt_date(session)))
    lines = label.lines()
    return {"source": lines[0], "as_of": lines[1]}


def lines_to_texts(lines) -> dict:
    return {"source": lines[0], "as_of": lines[1]}


__all__ = ["PublicIntelligence", "PublicSections", "plan_public_sections", "section_provenance",
           "lines_to_texts"]


# --------------------------------------------------------------------------- loading
def load_public_intelligence(structure_session: dt.date | None, list_date: dt.date,
                             out_dir: str, fetch: bool = False, now_iso: str | None = None,
                             fo_ban_fn=None, surveillance_fn=None, ipo_fn=None,
                             offer_doc_path: str | None = None) -> PublicIntelligence:
    """Assemble the inputs for the public sections. Nothing is fetched unless `fetch` (a
    production job); otherwise only stored artifacts are read: the Market Structure snapshot of
    `structure_session` and the exchange events stored for `list_date`.

    `list_date`: the trading date the Short is FOR (PRE: today; POST: the recap session's next
    trading date for the F&O ban file, which the exchange publishes for the next trade date)."""
    import os

    import market_structure as ms
    from exchange_watch import (fetch_fo_ban, fetch_surveillance, load_previous, mark_changes,
                                save_events, validate)
    from exchange_watch.watch import store_path
    from ipo_watch import apply_offer_document, fetch_nse_issues, load_offer_documents
    from market_structure.store import artifact_path

    intel = PublicIntelligence()
    if structure_session is not None:
        path = artifact_path(out_dir, structure_session)
        if os.path.exists(path):
            try:
                snap, uni, _ = ms.load_snapshot(path)
                intel.structure = snap
                intel.known_securities = uni.companies()
                intel.universe_symbols = uni.symbols()
            except Exception as exc:
                intel.notes.append(f"market structure artifact unusable: {type(exc).__name__}: {exc}")
        else:
            intel.notes.append(f"no market structure artifact for {structure_session}")

    if fetch:
        results = [(fo_ban_fn or fetch_fo_ban)(now_iso)] + list((surveillance_fn or
                                                                 fetch_surveillance)(now_iso))
        intel.exchange_sources = results
        raw = [e for r in results if r.status == "OK" for e in r.events]
        valid, rejected = validate(raw, list_date)
        valid = mark_changes(valid, load_previous(out_dir, list_date))
        save_events(valid, out_dir, list_date, results)
        intel.exchange_events = valid
        intel.notes += [f"exchange event rejected: {r['event_id']} ({r['reason']})"
                        for r in rejected[:20]]
        # an issue list is "as of" the day it was read (IST), not the date the Short is for
        from config import now_ist
        ipos, notes = (ipo_fn or fetch_nse_issues)(now_ist().date(), now_iso)
        intel.notes += notes
        docs, audit = load_offer_documents(offer_doc_path) if offer_doc_path else load_offer_documents()
        by_key = {(d.get("symbol") or d["company_name"]).upper(): d for d in docs}
        for ipo in ipos:
            d = by_key.get((ipo.symbol or ipo.company_name).upper())
            if d:
                apply_offer_document(ipo, d)
        intel.ipos = ipos
    else:
        p = store_path(out_dir, list_date)
        if os.path.exists(p):
            import json

            from exchange_watch import ExchangeEvent
            with open(p, encoding="utf-8") as fh:
                intel.exchange_events = [ExchangeEvent.from_dict(e) for e in json.load(fh)["events"]]
    return intel


__all__ += ["load_public_intelligence"]

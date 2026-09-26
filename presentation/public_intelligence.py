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
    # how each input was obtained: PERSISTED_SNAPSHOT / CAPTURED_THIS_RUN /
    # SNAPSHOT_NOT_CAPTURED / HISTORICAL_SNAPSHOT_UNAVAILABLE / VALIDATION_FAILED / SYNTHETIC
    # (NOT_AVAILABLE = never loaded) - separates "nothing happened" from "we could not read it"
    # from "it was never captured"
    exchange_status: str = "NOT_AVAILABLE"
    ipo_status: str = "NOT_AVAILABLE"
    structure_status: str = "NOT_AVAILABLE"
    # durable-state provenance: which stored snapshot each section was read from (file,
    # revision, checksum, status, connectivity) - what makes a video reproducible
    structure_ref: dict | None = None
    exchange_snapshots: dict = field(default_factory=dict)
    ipo_snapshot: dict | None = None
    capture: dict | None = None

    def inputs(self) -> dict:
        return {"market_structure": {"source": self.structure_status, **(self.structure_ref or {})},
                "exchange_watch": {"source": self.exchange_status,
                                   "snapshots": self.exchange_snapshots},
                "ipo_watch": {"source": self.ipo_status, "snapshot": self.ipo_snapshot},
                "capture": self.capture}


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
    from market_structure.store import SOURCE_LABEL, SOURCE_ROLES, structure_facts

    out = PublicSections()
    intel = intel or PublicIntelligence()
    sections = out.audit.setdefault("omitted_sections", {})

    # ------------------------------------------------------------ UNDER THE SURFACE
    snap = intel.structure
    insights, reasons = ms.select_insights(snap, nifty_pct, limit=max_structure)
    out.reasons.update({f"STRUCTURE.{k}": v for k, v in reasons.items()})
    session = dt.date.fromisoformat(snap.session_date) if snap is not None else day
    for ins in insights:
        facts = structure_facts(ins, snap, session)
        if all(gate.admit(f) for f in facts):
            out.structure.append((ins, session_label(SOURCE_LABEL, session,
                                                     SOURCE_ROLES).lines()))
        else:
            out.omitted.append({"section": "UNDER THE SURFACE", "item": ins.kind,
                                "reason": "publication gate refused a statement"})
    sections["MARKET_STRUCTURE"] = _structure_omission(snap, insights, out, intel,
                                                       max_structure)
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
    sections["EXCHANGE_WATCH"] = _exchange_omission(intel, chosen, admitted, ex_omit)
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
    from ipo_watch.watch import derive_events
    out.audit["ipo"]["derived_events"] = derive_events(intel.ipos, day)
    sections["IPO_WATCH"] = _ipo_omission(intel, ipos, ok, ipo_omit)
    out.audit["inputs"] = intel.inputs()
    return out


# --------------------------------------------------------------------------- omission codes
def _rights_refused(omitted) -> bool:
    return any("publication gate refused" in str(o.get("reason", "")) for o in omitted)


# How an input was obtained -> the section's omission code when nothing could be read. A source
# that was never queried is never SOURCE_UNAVAILABLE (that code means a request really failed).
_NO_INPUT = {"SNAPSHOT_NOT_CAPTURED": "SNAPSHOT_NOT_CAPTURED",
             "HISTORICAL_SNAPSHOT_UNAVAILABLE": "HISTORICAL_SNAPSHOT_UNAVAILABLE",
             "NOT_AVAILABLE": "SNAPSHOT_NOT_CAPTURED", None: "SNAPSHOT_NOT_CAPTURED"}
_FAILURE_ORDER = ("SOURCE_UNAVAILABLE", "PARSE_ERROR", "VALIDATION_FAILED")


def _status_block(code: str, detail: str, rendered: bool = False, input_source=None,
                  snapshot_status=None, connectivity=None) -> dict:
    """One optional section's verdict, keeping connectivity / snapshot / editorial apart."""
    return {"rendered": rendered, "code": code, "detail": detail,
            "input_source": input_source, "snapshot_status": snapshot_status,
            "connectivity_status": connectivity, "editorial_status": code}


def _snapshot_failure(snaps: dict) -> tuple:
    """(code, detail, connectivity) for the worst failure among attempted snapshots, or None."""
    failed = {k: v for k, v in snaps.items() if v.get("status") in _FAILURE_ORDER}
    if not failed:
        return None
    code = next(c for c in _FAILURE_ORDER if any(v["status"] == c for v in failed.values()))
    conn = sorted({v.get("connectivity_status") or "?" for v in failed.values()})
    detail = "; ".join(f"{k}: {v['status']} ({v.get('connectivity_status')}) "
                       f"{(v.get('reason') or '')[:120]}" for k, v in failed.items())
    return code, detail, "/".join(conn)


def _structure_omission(snap, insights, out, intel, limit) -> dict:
    src = intel.structure_status
    if out.structure:
        return _status_block("RENDERED", f"{len(out.structure)} scene(s)", True, src, "SUCCESS")
    if limit == 0:
        return _status_block("NOT_IN_PRODUCT", "this Short does not carry Market Structure",
                             input_source=src)
    if snap is None:
        code = "VALIDATION_FAILED" if src == "VALIDATION_FAILED" else _NO_INPUT.get(
            src, "SNAPSHOT_NOT_CAPTURED")
        return _status_block(code, "; ".join(intel.notes) or
                             "no Market Structure snapshot for the session", input_source=src,
                             snapshot_status=code)
    usable = [m for m in snap.metrics.values() if m.status in ("PUBLISHABLE", "PARTIAL")]
    if not usable:
        return _status_block("INSUFFICIENT_COVERAGE", ", ".join(
            f"{k} {m.coverage_pct}%" for k, m in snap.metrics.items()), input_source=src,
            snapshot_status="SUCCESS")
    if insights:
        return _status_block("RIGHTS_BLOCKED", "the publication gate refused the insight's "
                             "statements", input_source=src, snapshot_status="SUCCESS")
    return _status_block("NO_MEANINGFUL_OBSERVATION", "no breadth / unusual-volume / range count "
                         "met its threshold", input_source=src, snapshot_status="SUCCESS")


def _exchange_omission(intel, chosen, admitted, omitted) -> dict:
    src = intel.exchange_status
    snaps = intel.exchange_snapshots or {}
    if admitted:
        return _status_block("RENDERED", f"{len(admitted)} event(s)", True, src, "SUCCESS",
                             "REACHABLE")
    if src in _NO_INPUT and not intel.exchange_events and not intel.exchange_sources:
        code = _NO_INPUT[src]
        return _status_block(code, "; ".join(n for n in intel.notes if "exchange" in n.lower())
                             or "no official exchange snapshot for this session",
                             input_source=src, snapshot_status=code, connectivity="NOT_ATTEMPTED")
    legacy_failed = [s for s in intel.exchange_sources if getattr(s, "status", "OK") not in
                     ("OK", "EMPTY")]
    fail = _snapshot_failure(snaps)
    validated = [k for k, v in snaps.items() if v.get("status") in ("SUCCESS", "NO_DATA")]
    if (fail and not validated) or (intel.exchange_sources and
                                    len(legacy_failed) == len(intel.exchange_sources)):
        code, detail, conn = fail or ("SOURCE_UNAVAILABLE", "; ".join(
            f"{s.source_name}: {s.status} {s.reason}" for s in legacy_failed),
            "/".join(sorted({getattr(s, "connectivity", "?") for s in legacy_failed})))
        return _status_block(code, detail, input_source=src, snapshot_status=code,
                             connectivity=conn)
    partial = f"; partial: {fail[1]}" if fail else ""
    if chosen and _rights_refused(omitted):
        return _status_block("RIGHTS_BLOCKED", "the publication gate refused every selected "
                             "event" + partial, input_source=src, snapshot_status="SUCCESS")
    from exchange_watch.watch import NO_NEW_EVENT_REASON
    if intel.exchange_events and not chosen and any(o.get("reason") == NO_NEW_EVENT_REASON
                                                    for o in omitted):
        return _status_block("NO_NEW_EVENT", "valid official lists, no change vs the previous "
                             "session's snapshot" + partial, input_source=src,
                             snapshot_status="SUCCESS", connectivity="REACHABLE")
    if intel.exchange_events and not chosen:
        return _status_block("NO_ELIGIBLE_EVENT", "official events exist but none qualifies "
                             "(e.g. surveillance entries outside the index universe)" + partial,
                             input_source=src, snapshot_status="SUCCESS")
    return _status_block("NO_ELIGIBLE_EVENT", "the official lists were read and carry no event "
                         "for this date" + partial, input_source=src, snapshot_status="SUCCESS",
                         connectivity="REACHABLE")


def _ipo_omission(intel, chosen, ok, omitted) -> dict:
    src = intel.ipo_status
    snap = intel.ipo_snapshot or {}
    if ok:
        return _status_block("RENDERED", f"{len(ok)} IPO event(s)", True, src, "SUCCESS",
                             "REACHABLE")
    if snap.get("status") in _FAILURE_ORDER:
        return _status_block(snap["status"], (snap.get("reason") or "")[:300], input_source=src,
                             snapshot_status=snap["status"],
                             connectivity=snap.get("connectivity_status"))
    if src in _NO_INPUT and not intel.ipos:
        code = _NO_INPUT[src]
        return _status_block(code, "no NSE issue-list snapshot for this session",
                             input_source=src, snapshot_status=code, connectivity="NOT_ATTEMPTED")
    if chosen and _rights_refused(omitted):
        return _status_block("RIGHTS_BLOCKED", "the publication gate refused every IPO fact",
                             input_source=src, snapshot_status="SUCCESS")
    return _status_block("NO_ELIGIBLE_IPO_EVENT", "no IPO opens / closes / lists / has allotment "
                         "on this date", input_source=src,
                         snapshot_status=snap.get("status") or "SUCCESS",
                         connectivity=snap.get("connectivity_status") or "REACHABLE")


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
                             offer_doc_path: str | None = None, *,
                             snapshot_session: dt.date | None = None, replay: bool = False,
                             now: dt.datetime | None = None,
                             capture_mode: str = "POST_FALLBACK", calendar=None
                             ) -> PublicIntelligence:
    """Assemble the public sections' inputs FROM DURABLE STATE (Acquisition -> Durable state ->
    Publication). Nothing here decides what the market did; it reads what was captured:

      Market Structure   the snapshot the REPORT job persisted for `structure_session` - never
                         recomputed here
      Exchange / IPO     the official snapshots of `snapshot_session` (default:
                         structure_session) - Exchange Watch changes vs the previous session's
                         snapshot, IPO events from the stored issue lists

    `fetch` (a live scheduled run) may capture a MISSING or failed snapshot, but only inside
    that session's capture window (official_snapshots.service) - a fallback, recorded as
    CAPTURED_THIS_RUN. `replay` never acquires: a snapshot that was not preserved is
    HISTORICAL_SNAPSHOT_UNAVAILABLE, never today's page relabelled.

    `list_date`: kept for callers; the service derives the expected list date from the
    calendar (the session after `snapshot_session`)."""
    import hashlib
    import os

    import market_structure as ms
    from market_structure.store import artifact_path
    from official_snapshots import (ASM, FNO_BAN, GSM, IPO, OfficialDailySnapshotService,
                                    exchange_events, summarize)
    from official_snapshots.models import (CAPTURED_THIS_RUN, HISTORICAL_SNAPSHOT_UNAVAILABLE,
                                           PERSISTED_SNAPSHOT, SNAPSHOT_NOT_CAPTURED, VALIDATED)

    from config import now_ist
    now = now or now_ist()
    intel = PublicIntelligence()
    svc = OfficialDailySnapshotService(out_dir, fo_ban_fn, surveillance_fn, ipo_fn, calendar)
    session = snapshot_session or structure_session
    historical = replay
    if session is not None and not replay:
        _, code, _ = svc.capture_allowed(session, now)
        historical = code == "HISTORICAL_SESSION"
    missing = HISTORICAL_SNAPSHOT_UNAVAILABLE if historical else SNAPSHOT_NOT_CAPTURED

    # ------------------------------------------------------------ Market Structure
    if structure_session is not None:
        path = artifact_path(out_dir, structure_session)
        if os.path.exists(path):
            try:
                snap, uni, _ = ms.load_snapshot(path)
                intel.structure = snap
                intel.known_securities = uni.companies()
                intel.universe_symbols = uni.symbols()
                intel.structure_status = PERSISTED_SNAPSHOT
                with open(path, "rb") as fh:
                    intel.structure_ref = {"file": os.path.relpath(path, out_dir),
                                           "sha256": hashlib.sha256(fh.read()).hexdigest(),
                                           "session_date": structure_session.isoformat()}
            except Exception as exc:
                intel.structure_status = "VALIDATION_FAILED"
                intel.notes.append(f"market structure snapshot unusable: {type(exc).__name__}: "
                                   f"{exc}")
        else:
            intel.structure_status = missing
            intel.notes.append(f"no persisted Market Structure snapshot for {structure_session}")

    # ------------------------------------------------------------ official snapshots
    if session is None:
        intel.exchange_status = intel.ipo_status = SNAPSHOT_NOT_CAPTURED
        return intel
    captured = set()
    if fetch and not replay:
        summary = svc.ensure(session, now, capture_mode)
        captured = {c["kind"] for c in summary["captured"]}
        intel.capture = summary
    current = svc.load(session)
    prev_session = svc.calendar.previous_session(session)
    previous = svc.load(prev_session)

    def ref(kind):
        snap, name = current[kind]
        return {"kind": kind, "session_date": session.isoformat(), "file": name,
                "revision": snap.revision, "status": snap.status,
                "connectivity_status": snap.connectivity_status, "reason": snap.reason,
                "source_date": snap.source_date, "record_count": snap.record_count,
                "checksum": snap.checksum, "capture_mode": snap.capture_mode,
                "retrieved_at": snap.retrieved_at}

    exch_kinds = [k for k in (FNO_BAN, ASM, GSM) if k in current]
    if exch_kinds:
        intel.exchange_status = (CAPTURED_THIS_RUN if captured & set(exch_kinds)
                                 else PERSISTED_SNAPSHOT)
        intel.exchange_snapshots = {k: ref(k) for k in exch_kinds}
        events = []
        for k in exch_kinds:
            snap = current[k][0]
            if snap.status in VALIDATED:
                prev = previous.get(k, (None, None))[0]
                evs = exchange_events(k, snap, prev)
                intel.exchange_snapshots[k]["baseline"] = (
                    {"session_date": prev_session.isoformat(), "file": previous[k][1],
                     "checksum": prev.checksum} if prev is not None and prev.validated
                    else None)
                intel.exchange_snapshots[k]["changes"] = summarize(evs)
                events += evs
        intel.exchange_events = events
    else:
        intel.exchange_status = missing
        intel.notes.append(f"no official exchange snapshot for {session}")

    if IPO in current:
        from ipo_watch import IPOEvent, apply_offer_document, load_offer_documents
        snap = current[IPO][0]
        intel.ipo_status = CAPTURED_THIS_RUN if IPO in captured else PERSISTED_SNAPSHOT
        intel.ipo_snapshot = ref(IPO)
        if snap.status in VALIDATED:
            ipos = [IPOEvent.from_snapshot_record(r) for r in snap.records]
            docs, _ = (load_offer_documents(offer_doc_path) if offer_doc_path
                       else load_offer_documents())
            by_key = {(d.get("symbol") or d["company_name"]).upper(): d for d in docs}
            for ipo in ipos:
                d = by_key.get((ipo.symbol or ipo.company_name).upper())
                if d:
                    apply_offer_document(ipo, d)
            intel.ipos = ipos
    else:
        intel.ipo_status = missing
        intel.notes.append(f"no official IPO snapshot for {session}")
    return intel


__all__ += ["load_public_intelligence"]

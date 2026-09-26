"""PRE under a publication profile - applied to the brief BEFORE the planner runs, so a blocked
fact can never become a section, a watch card or a hook.

    PUBLIC_UNREGISTERED   the previous session's Radar stock watch is our own technical analysis
                          of named securities: removed (recorded). EXCHANGE / IPO WATCH come
                          from official events only. Market facts (overnight cues, setup, flows,
                          VIX, sectors) are classified for the audit; an AI-only one is refused.
    PRIVATE_ANALYTICS     the stock watch stays exactly as in PRE V1.
"""
from __future__ import annotations

from core import sources as S
from publication import PublicationGate
from publication.classification import (ContentClass, Orientation, Origin, PublishableFact,
                                        Scope)
from publication.classify import SOURCE_LABELS, stock_watch_fact, strictest_rights

from .public_intelligence import plan_public_sections


def _market_fact(fid, text, source_names, as_of, scope=Scope.INDEX, tags=()):
    names = [n for n in source_names if n] or [S.SRC_YAHOO]
    ai = any(S.source_metadata(n).source_type.value == "AI" for n in names) and \
        all(S.source_metadata(n).source_type.value == "AI" for n in names)
    return PublishableFact(
        fact_id=fid, text=text, scope=scope, origin=Origin.AI if ai else Origin.MARKET_DATA,
        content_class=ContentClass.MARKET_AGGREGATE, orientation=Orientation.HISTORICAL,
        source_name=names[0], source_label=" · ".join(dict.fromkeys(SOURCE_LABELS.get(n, n)
                                                                   for n in names)),
        data_as_of=as_of, publication_rights_status=strictest_rights(names), section="PRE",
        tags=frozenset(tags))


def _report_sources(brief, fact_ids) -> list:
    out = []
    for fid in fact_ids or []:
        for o in (brief.fact_provenance.get(fid) or {}).get("observations") or []:
            if o.get("source_type") != "AI" and o.get("source") not in out:
                out.append(o["source"])
    return out


def apply_publication_profile(brief, profile=None) -> PublicationGate:
    intel = brief.public_intelligence
    known = dict(getattr(intel, "known_securities", None) or {})
    gate = PublicationGate(profile or brief.publication_profile, known)
    brief.publication_profile = gate.profile.value

    kept = []
    for s in brief.stock_facts:
        f = stock_watch_fact(s["symbol"], f"{brief.prev_weekday}: {s.get('takeaway', '')}",
                             brief.previous_session)
        if gate.admit(f):
            kept.append(s)
        else:
            brief.notes.append(f"stock watch {s['symbol']} withheld by publication profile "
                               f"{gate.profile.value}: named-security technical analysis")
            brief.public_omitted.append({"section": "STOCK WATCH", "item": s["symbol"],
                                         "reason": f"publication profile {gate.profile.value}: "
                                                   "Radar analysis of a named security stays "
                                                   "PRIVATE"})
    brief.stock_facts = kept

    # GIFT Nifty (NSE IX): rights RESTRICTED while docs/NSEIX_RIGHTS_REVIEW.md is OPEN. Defence
    # in depth on top of operations.gift_policy: in public output it is shown ONLY when that
    # policy explicitly allowed publication, whatever built the brief.
    if brief.gift is not None and gate.public and not (brief.gift_policy or {}).get(
            "publication_allowed"):
        gate.admit(_market_fact("pre.gift", "GIFT Nifty", [S.SRC_NSEIX_LIVE], brief.pre_date,
                                Scope.INDEX, tags=("LIVE",)))
        brief.gift = None
        brief.gift_policy = dict(brief.gift_policy or {}, withheld=True,
                                 reason="publication profile: NSE IX rights RESTRICTED and the "
                                        "GIFT publication policy did not allow display")
        brief.public_omitted.append({"section": "GIFT NIFTY", "item": "GIFT NIFTY",
                                     "reason": "withheld: NSE IX display rights not approved"})

    # market facts: classified for the audit (and an AI-only one refused)
    nifty_src = _report_sources(brief, brief.nifty.get("fact_ids"))
    gate.admit(_market_fact("pre.setup.nifty", f"Nifty 50 closed {brief.nifty.get('pct', 0):+.2f}%",
                            nifty_src or [S.SRC_YAHOO], brief.previous_session))
    for q in brief.global_cues:
        if getattr(q, "fresh", False):
            gate.admit(_market_fact(f"pre.cue.{q.key}", f"{q.name} {q.change_pct:+.2f}%",
                                    [q.source], q.market_date or brief.pre_date, Scope.MARKET))
    if brief.vix is not None and getattr(brief.vix, "fresh", False):
        gate.admit(_market_fact("pre.vix", f"India VIX {brief.vix.value:.2f}", [brief.vix.source],
                                brief.vix.session))

    ps = plan_public_sections(gate, intel, brief.pre_date, "PRE", max_structure=0)
    brief.exchange_watch, brief.ipo_watch = ps.exchange, ps.ipo
    brief.public_audit = ps.audit
    brief.public_omitted += ps.omitted
    return gate


__all__ = ["apply_publication_profile"]

"""PRE-MARKET provenance audit: for every freshness-sensitive fact the PRE Short could show,
who said it, when it was retrieved, what market moment it describes, how fresh it was, and
whether it reached the screen.

    US indices / Asian indices   PreMarketQuote (providers.premarket, Yahoo)
    GIFT Nifty                   PreMarketQuote (providers.gift_nifty, NSE IX + DSP file)
    India VIX                    VixReading (Yahoo, date-checked + cross-checked vs the report)
    previous-session Nifty/FII-DII/sectors   canonical report facts (brief.fact_provenance)
    events                       ScheduledEvent (official schedule / exchange rule)
    news headlines               report.events - never shown (recorded for completeness)

The one hard rule enforced here: NO DISPLAYED MARKET FACT MAY HAVE AN LLM AS ITS PROVENANCE.
`ai_violations` lists any displayed record whose only source is an AI source; render_pre
blocks the Short on a non-empty list (it would be a bug upstream - the brief builder already
drops AI-only flows/sectors and no provider reads a model). Pure: reads the brief and plan.
"""
from __future__ import annotations

from core.sources import GROUP_GEMINI, source_metadata

PROVENANCE_AUDIT_VERSION = "pre-prov-1.0"
AI_TYPES = frozenset({"AI"})


def _stype(source: str, given: str | None = None) -> str:
    return given or source_metadata(source).source_type.value


def _quote_record(q, category: str, displayed: bool) -> dict:
    rec = {"category": category, "fact": f"{category.lower()}.{q.key.lower()}", "name": q.name,
           "displayed": displayed, "value": q.value, "change_pct": q.change_pct,
           "source": q.source, "source_type": _stype(q.source, q.source_type),
           "independence_group": q.independence_group,
           "source_reference": getattr(q, "source_reference", "") or q.ticker,
           "retrieved_at": q.retrieved_at.isoformat() if q.retrieved_at else None,
           "market_date": q.market_date.isoformat() if q.market_date else None,
           "market_timestamp": q.market_timestamp.isoformat() if q.market_timestamp else None,
           "reference_date": q.reference_date.isoformat() if q.reference_date else None,
           "freshness": q.freshness.value, "freshness_reason": q.freshness_reason,
           "validation_status": q.validation_status}
    prov = getattr(q, "provenance", None) or {}
    if prov:
        rec["provenance"] = {k: prov.get(k) for k in (
            "venue", "instrument", "contract_expiry", "last_trade_timestamp", "settlement_price",
            "settlement_date", "settlement_url", "consistency", "implied_reference",
            "judged_at", "change_definition") if k in prov}
    return rec


def _fact_records(brief, fact_ids, category: str, displayed: bool) -> list:
    out = []
    for fid in fact_ids:
        fp = (brief.fact_provenance or {}).get(fid)
        if fp is None:
            out.append({"category": category, "fact": fid, "displayed": displayed,
                        "source": None, "source_type": None, "observations": [],
                        "note": "fact provenance not recorded on the brief (synthetic brief?)"})
            continue
        obs = fp.get("observations") or []
        types = sorted({o.get("source_type") for o in obs if o.get("source_type")})
        out.append({"category": category, "fact": fid, "displayed": displayed,
                    "market_date": fp.get("market_date"),
                    "validation_status": fp.get("validation_status"),
                    "source": ", ".join(sorted({o.get("source") for o in obs if o.get("source")})),
                    "source_type": "AI" if types == ["AI"] else ",".join(types),
                    "observations": obs})
    return out


def pre_provenance_audit(brief, plan) -> dict:
    """Every freshness-sensitive PRE fact with its provenance and whether it was shown."""
    records = []
    shown_cues = {c.name for c in (plan.overnight.cues if plan.overnight else [])}
    watch_cats = {w.category for w in plan.watch}
    for q in brief.global_cues:
        records.append(_quote_record(q, "US_INDEX" if q.region == "US" else "ASIA_INDEX",
                                     q.name in shown_cues))
    if brief.gift is not None:
        records.append(_quote_record(brief.gift, "GIFT", bool(plan.show_gift_nifty)))
    else:
        gp = getattr(brief, "gift_policy", None) or {}
        note = ("withheld by the GIFT publication policy - " + str(gp.get("reason"))
                if gp.get("withheld") else
                "not fetched - " + str(gp.get("reason")) if gp.get("fetched") is False else
                "no GIFT reading acquired - omitted")
        records.append({"category": "GIFT", "fact": "gift.gift", "name": "GIFT NIFTY",
                        "displayed": False, "source": None, "source_type": None,
                        "note": note})
    v = brief.vix
    if v is not None:
        records.append({"category": "INDIA_VIX", "fact": "india_vix", "name": "INDIA VIX",
                        "displayed": bool(plan.show_vix or "VIX" in watch_cats),
                        "value": v.value, "change_pct": v.change_pct, "source": v.source,
                        "source_type": _stype(v.source),
                        "independence_group": source_metadata(v.source).independence_group,
                        "market_date": v.session.isoformat(),
                        "reference_date": v.previous_session.isoformat() if v.previous_session else None,
                        "freshness": v.freshness.value, "freshness_reason": v.freshness_reason,
                        "validation_status": v.validation_status, "crosscheck": v.crosscheck})
    else:
        records.append({"category": "INDIA_VIX", "fact": "india_vix", "displayed": False,
                        "source": None, "source_type": None, "note": "no VIX reading"})
    nifty_ids = list((brief.nifty or {}).get("fact_ids") or [])
    records += _fact_records(brief, nifty_ids, "PREV_NIFTY", True)
    flow_ids = [f for f in (brief.fact_provenance or {}) if "net-cash" in f]
    flows_shown = bool(plan.show_flows or "FLOWS" in watch_cats)
    if brief.flows:
        records += _fact_records(brief, flow_ids, "FII_DII", flows_shown) or [{
            "category": "FII_DII", "fact": "flows", "displayed": flows_shown,
            "source": (brief.flows or {}).get("source"), "source_type": None,
            "note": "flows carry no fact ids (synthetic brief?)"}]
    else:
        records.append({"category": "FII_DII", "fact": "flows", "displayed": False,
                        "source": None, "source_type": None,
                        "note": "no non-AI FII/DII for the previous session - omitted"})
    sector_ids = [f for f in (brief.fact_provenance or {}) if f.startswith("fact_sector-")]
    records += _fact_records(brief, sector_ids, "SECTOR",
                             bool(plan.show_sector_context or "SECTOR" in watch_cats))
    shown_event = plan.event.title if plan.event else None
    for e in brief.events:
        records.append({"category": "EVENT", "fact": e.event_id or e.event_name,
                        "name": e.event_name, "displayed": e.event_name == shown_event,
                        "event_date": e.event_date.isoformat(),
                        "event_time": e.event_time.strftime("%H:%M") if e.event_time else None,
                        "source": e.source, "source_reference": e.source_reference,
                        "source_type": "RULE" if e.validation_status.value == "RULE_DERIVED"
                        else "OFFICIAL", "validation_status": e.validation_status.value,
                        "verified_on": e.verified_on.isoformat() if e.verified_on else None,
                        "retrieved_at": e.retrieved_at or None})
    for n in brief.news_events:
        records.append({"category": "NEWS_HEADLINE", "fact": n.get("text"), "displayed": False,
                        "source": "google_news_rss", "source_type": "NEWS",
                        "note": "a headline is never today's event"})
    violations = [r for r in records if r.get("displayed") and (
        r.get("source_type") in AI_TYPES or r.get("independence_group") == GROUP_GEMINI
        or (r.get("source") or "") == "gemini")]
    return {"version": PROVENANCE_AUDIT_VERSION, "pre_date": brief.pre_date.isoformat(),
            "synthetic": brief.synthetic, "records": records,
            "displayed": [r["fact"] for r in records if r.get("displayed")],
            "ai_violations": violations, "ok": not violations}


__all__ = ["pre_provenance_audit", "PROVENANCE_AUDIT_VERSION"]

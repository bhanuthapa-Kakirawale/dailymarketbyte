"""The PRE-MARKET storyboard: the PreSectionPlan laid out as scenes, in the plan's order.

    PreMarketBrief ──> PreEditorialPlanner ──> PreSectionPlan ──> build_pre_storyboard ──> Storyboard
                                                     │                    │
                            hooks.pre_market_sheet <─┘      Dynamic Hook Engine (approved)

Like the POST storyboard this selects nothing and computes nothing: every scene packages ONE
model the planner already built, and every string it emits is declared in `texts` so
`Storyboard.public_text()` - the content scan and the PRE language guard - see exactly what
the viewer sees. The same `Storyboard` / `Composer` / chrome render it: PRE is the morning
sibling of POST, not a second product.
"""
from __future__ import annotations

import datetime as dt

from .storyboard import SceneSpec, Storyboard, dynamic_hook_spec

KICKER = "BEFORE THE BELL"


# --------------------------------------------------------------------------- provenance
def _labels(names) -> str:
    from publication.classify import SOURCE_LABELS
    return " · ".join(dict.fromkeys(SOURCE_LABELS.get(n, n) for n in names if n)) or "NSE"


def _prov(source: str, as_of: str, label: str = "DATA AS OF", fetched: str | None = None) -> dict:
    from presentation.provenance_label import ProvenanceLabel
    lines = ProvenanceLabel(source=source, data_as_of=as_of, as_of_label=label,
                            fetched=fetched).lines()
    return {"source": lines[0], "as_of": lines[1]}


def _report_names(brief, fact_ids):
    out = []
    for fid in fact_ids or []:
        for o in (brief.fact_provenance.get(fid) or {}).get("observations") or []:
            if o.get("source_type") != "AI" and o.get("source") not in out:
                out.append(o["source"])
    return out


def pre_provenance(brief, plan) -> dict:
    """Section key -> {source, as_of} for every factual PRE scene."""
    from presentation.provenance_label import fmt_date, fmt_session_close, fmt_time
    prev = brief.previous_session
    out = {}
    nifty_src = _labels(_report_names(brief, brief.nifty.get("fact_ids")) or ["yahoo_finance"])
    out["SETUP"] = _prov(nifty_src, fmt_session_close(prev))
    out["SECTORS"] = _prov(nifty_src, fmt_session_close(prev))
    flow_ids = [f for f in brief.fact_provenance if "FII" in f.upper() or "DII" in f.upper()]
    out["FLOWS"] = _prov(_labels(_report_names(brief, flow_ids) or ["nse_website"]), fmt_date(prev))
    if brief.vix is not None:
        out["VIX"] = _prov(_labels([brief.vix.source]), fmt_session_close(brief.vix.session))
    ov = plan.overnight
    if ov is not None:
        names = [c.source for c in ov.cues] + ([ov.gift.source] if ov.gift else [])
        closes = sorted({c.market_date for c in ov.cues if not c.market_timestamp and c.market_date})
        parts = [f"US CLOSE {fmt_date(dt.date.fromisoformat(d))}"
                 for d in closes[-1:]]
        # a LIVE reading: DATA AS OF = its own market time (the end of its last completed bar),
        # FETCHED = when this system actually retrieved it - never the cutoff standing in for
        # either (a reconstruction of a past morning is fetched later than its cutoff)
        shown = {c.name for c in ov.cues}
        live = [q for q in brief.global_cues if q.name in shown and q.market_timestamp]
        if ov.gift is not None and brief.gift is not None and brief.gift.market_timestamp:
            live.append(brief.gift)
        fetched = None
        if live:
            parts.append(fmt_time(max(q.market_timestamp for q in live)))
            got = max(q.retrieved_at for q in live)
            fetched = (fmt_time(got) if got.date() == brief.pre_date
                       else f"{fmt_date(got.date())} {fmt_time(got)}")
        out["OVERNIGHT"] = _prov(_labels(names), " · ".join(parts) or fmt_date(brief.pre_date),
                                 fetched=fetched)
    if plan.event is not None:
        src = plan.event.source_label.removeprefix("Source: ")
        out["EVENT"] = _prov(src, fmt_date(brief.pre_date), label="EVENT DATE")
    watch_names = (_report_names(brief, brief.nifty.get("fact_ids")) or ["yahoo_finance"]) +         ([c.source for c in ov.cues] if ov else [])
    out["WATCH"] = _prov(_labels(watch_names),
                         f"{fmt_session_close(prev)}" + (f" · {fmt_time(brief.as_of)}" if ov else ""))
    out["STOCK_WATCH"] = _prov("Daily Market Byte Radar (private)", fmt_session_close(prev))
    return out


def _overnight(ov, dur, prev_label):
    cues = [{"name": c.name, "value": c.value, "when": c.when} for c in ov.cues]
    gift = None
    if ov.gift is not None:
        gift = {"name": "GIFT NIFTY", "time": f"AT {ov.gift.time_label}",
                "reference": ov.gift.reference, "value": ov.gift.value}
    texts = {"headline": ov.headline, "takeaway": ov.takeaway, "cues": cues}
    if gift:
        texts["gift"] = gift
    lead = ov.cues[0] if ov.cues else None
    return SceneSpec(
        kind="PRE_OVERNIGHT", section="OVERNIGHT", duration=dur, headline=ov.headline,
        takeaway=ov.takeaway, texts=texts,
        data={"cues": [{"positive": c.positive, "numeric": c.change_pct, "role": c.role}
                       for c in ov.cues],
              "gift": {"positive": ov.gift.positive} if ov.gift else None},
        freeze={"t": round(dur - 0.6, 2),
                "what": ov.headline if lead else (ov.gift.sentence if ov.gift else ""),
                "where": "the night card (the one dominant overnight cue, with its US-close day or "
                         "IST time), supporting cue cards below"
                         + ("; the timestamped GIFT Nifty strip" if ov.gift else ""),
                "why": ov.takeaway or "what changed overnight", "mode": "PRE_OVERNIGHT"})


def _setup(su, dur, labels):
    if su.kind == "CHART":
        st = su.structure
        m = st["model"]
        return SceneSpec(
            kind="NIFTY", section="SETUP", duration=dur, headline=m["takeaway"],
            takeaway=m["takeaway"], texts=st["texts"], data=m,
            freeze={"t": round(dur - 0.6, 2), "what": m["takeaway"],
                    "where": "the ringed last Nifty candle against its one reference line, under "
                             f"the '{labels['SETUP']}' chip",
                    "why": "a structural event in the previous session - labelled with its day, "
                           "no implication for today", "mode": m["event_family"]})
    pm = su.pulse
    sup = pm.get("support") or {}
    texts = {"headline": pm["headline"], "label": pm["label"], "value": pm["value"],
             "change": pm["change"], "points": pm.get("points", ""),
             "support": sup.get("text", ""), "low_label": sup.get("low_label", ""),
             "high_label": sup.get("high_label", ""), "open_label": sup.get("open_label", ""),
             "close_label": sup.get("close_label", "")}
    return SceneSpec(
        kind="PULSE", section="SETUP", duration=dur, headline=pm["headline"], texts=texts,
        data={"positive": pm["positive"], "support": sup},
        freeze={"t": round(dur - 0.6, 2), "what": f"{pm['headline']}: {pm['value']} ({pm['change']})",
                "where": "the previous close and change, then the day's path inside its range",
                "why": sup.get("text") or "where Nifty finished"})


def _vix(v, dur):
    texts = {"headline": v.headline, "note": v.note, "label": f"INDIA VIX · {v.session_label} CLOSE",
             "value": v.value, "change": v.change,
             "change_label": f"vs {v.previous_value} on {v.previous_label}",
             "previous_label": v.previous_label, "previous_value": v.previous_value,
             "session_label": v.session_label}
    return SceneSpec(
        kind="PRE_VIX", section="VIX", duration=dur, headline=v.headline, subline=v.note,
        texts=texts, data={"previous": float(v.previous_value), "latest": float(v.value)},
        freeze={"t": round(dur - 0.5, 2), "what": v.headline,
                "where": "the VIX close and change; the two closes as bars from zero",
                "why": "a large volatility-index move on the previous session - stated, not "
                       "interpreted", "mode": "PRE_VIX"})


def _flows(f, dur):
    return SceneSpec(
        kind="FLOWS", section="FLOWS", duration=dur, headline=f.headline, subline=f.subline,
        texts={"bars": f.bars},
        freeze={"t": round(dur - 0.6, 2), "what": f.headline,
                "where": "opposing bars from the centre line - left is selling, right is buying",
                "why": "previous-session institutional flows, labelled with their day"})


def _sectors(sm, dur):
    texts = {"headline": sm["headline"], "tone": sm["tone"], "leader_tag": sm["leader_tag"],
             "laggard_tag": sm["laggard_tag"], "strip_title": sm["strip_title"],
             "rows": [{"name": r["name"], "value": r["value"]} for r in sm["rows"]]}
    return SceneSpec(
        kind="SECTORS", section="SECTORS", duration=dur, headline=sm["headline"],
        subline=sm["tone"], texts=texts, data={"rows": sm["rows"]},
        freeze={"t": round(dur - 0.6, 2), "what": sm["headline"],
                "where": "leader and laggard cards; every sector in the heat strip",
                "why": "the previous session's sector spread"})


def _event(e, dur):
    texts = {"headline": "On today's calendar", "tag": e.tag, "title": e.title,
             "time": e.time_label, "day": e.day, "month": e.month, "weekday": e.weekday,
             "source": e.source_label}
    return SceneSpec(
        kind="PRE_EVENT", section="EVENT", duration=dur, headline="On today's calendar", texts=texts,
        data={"importance": e.importance, "validation_status": e.validation_status},
        freeze={"t": round(dur - 0.5, 2), "what": f"{e.title} ({e.time_label})",
                "where": "the taped calendar page and the event card with its time",
                "why": f"a verified scheduled event ({e.validation_status})", "mode": "PRE_EVENT"})


def _stocks(sw, dur, prev_day):
    # every card names the PREVIOUS session twice: under the move ("THURSDAY'S CLOSE") and at
    # the start of its sentence ("Thursday: ...") - it can never read as a live pre-open signal
    items = [{"symbol": it["symbol"], "change": it["change"], "line": it["line"],
              "day": f"{prev_day.upper()}'S CLOSE"} for it in sw.items]
    return SceneSpec(
        kind="PRE_STOCKS", section="STOCK_WATCH", duration=dur, headline=sw.headline,
        subline=sw.subline, texts={"headline": sw.headline, "subline": sw.subline, "items": items},
        data={"items": [{"positive": it["positive"], "closes": it.get("closes") or []}
                        for it in sw.items]},
        freeze={"t": round(dur - 0.5, 2), "what": "; ".join(f"{i['symbol']} {i['change']}" for i in items),
                "where": "one card per stock: name, move, its recent closes, the Radar sentence",
                "why": "previous-session Radar stories carried forward - facts, not calls",
                "mode": "PRE_STOCKS"})


def _watch(items, headline, sub, dur):
    return SceneSpec(
        kind="PRE_WATCH", section="WATCH", duration=dur, headline=headline, subline=sub,
        texts={"headline": headline, "subline": sub,
               "items": [{"tag": w.tag, "title": w.title, "note": w.note} for w in items]},
        data={"positive": [w.positive for w in items], "categories": [w.category for w in items]},
        freeze={"t": round(dur - 0.5, 2), "what": "; ".join(w.title for w in items),
                "where": "numbered attention cards", "why": "reference points for the open, "
                "each a fact with its day or time - never an action", "mode": "PRE_WATCH"})


def _closing(line):
    return SceneSpec(kind="CLOSING", section="CLOSING", duration=2.6,
                     texts={"cta": "SUBSCRIBE", "note": line}, dim_background=False,
                     freeze={"t": 2.1, "what": "sign-off: Daily Market Byte",
                             "where": "brand lockup at the centre", "why": line})


def _public_exchange(model):
    from .public_storyboard import exchange_spec
    spec = exchange_spec(model, "PRE")
    spec.section = "EXCHANGE"
    return spec


def _public_ipo(model):
    from .public_storyboard import ipo_spec
    return ipo_spec(model)


def build_pre_storyboard(brief, plan, dynamic_hook: bool = True, hook_ai: bool = False,
                         hook_client=None, sources: dict | None = None,
                         gate=None) -> Storyboard:
    """`hook_ai` lets the approved hook engine ask Gemini to choose among its deterministic
    PRE candidates (off by default; any failure falls back to the deterministic hook)."""
    d = plan.durations
    builders = {
        "OVERNIGHT": lambda: _overnight(plan.overnight, d["OVERNIGHT"],
                                        brief.previous_session.strftime("%a").upper()),
        "SETUP": lambda: _setup(plan.setup, d["SETUP"], plan.labels),
        "VIX": lambda: _vix(plan.vix, d["VIX"]),
        "FLOWS": lambda: _flows(plan.flows, d["FLOWS"]),
        "SECTORS": lambda: _sectors(plan.sectors, d["SECTORS"]),
        "EVENT": lambda: _event(plan.event, d["EVENT"]),
        "STOCK_WATCH": lambda: _stocks(plan.stock_watch, d["STOCK_WATCH"], brief.prev_weekday),
        "WATCH": lambda: _watch(plan.watch, plan.watch_headline, plan.watch_subline, d["WATCH"]),
        "EXCHANGE": lambda: _public_exchange(plan.exchange),
        "IPO": lambda: _public_ipo(plan.ipo),
    }
    prov = pre_provenance(brief, plan)
    main = []
    for k in plan.order:
        spec = builders[k]()
        if k in prov and "provenance" not in spec.texts:
            spec.texts["provenance"] = prov[k]
        main.append(spec)
    hook_record = None
    scenes = []
    if dynamic_hook:
        from hooks import plan_hook, pre_market_sheet
        from hooks.sheet_pre import pre_market_inputs_from_brief
        sheet = pre_market_sheet(pre_market_inputs_from_brief(brief, plan))
        if gate is not None:
            from publication.public_hooks import restrict_sheet
            restrict_sheet(sheet, gate)
        kwargs = {"client": hook_client} if hook_client is not None else {}
        hp = plan_hook(sheet, use_ai=hook_ai, **kwargs)
        scenes.append(dynamic_hook_spec(hp, sheet))
        hook_record = hp.to_dict()
    scenes += main
    scenes.append(_closing(plan.closing_line))
    return Storyboard(session_date=brief.pre_date,
                      date_label=brief.pre_date.strftime("%a %d %b %Y").upper(), kicker=KICKER,
                      scenes=scenes, sources=dict(sources or brief.sources),
                      omitted=list(plan.omitted) + [
                          {"section": k, "reason": plan.reasons.get(k, "")}
                          for k in ("VIX", "FLOWS", "SECTORS", "EVENT", "STOCK_WATCH", "OVERNIGHT",
                                    "EXCHANGE", "IPO")
                          if k not in plan.order] + list(brief.public_omitted),
                      hook_plan=hook_record, section_labels=dict(plan.labels),
                      pre_plan=plan.to_dict(),
                      publication_profile=getattr(brief, "publication_profile",
                                                  "PUBLIC_UNREGISTERED"),
                      publication=gate.to_dict() if gate is not None else None,
                      public_audit=dict(brief.public_audit), gate=gate)


__all__ = ["build_pre_storyboard", "KICKER"]

"""PRE_MARKET fact sheet: what a trader needs before the bell - overnight global cues, GIFT
Nifty, today's named events, and the previous session's setup.

The full PRE_MARKET video does not exist yet (Phase 1 builds only its hook). `PreMarketInputs`
is the contract that video's data layer will fill; `pre_market_inputs_from_plan` fills it
today from the validated report's own GLOBAL / EVENTS / Nifty scenes.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import policy
from .models import BeatKind, HookFactSheet, HookMode
from .sheet_common import beat, count_fact, fact, move_claims, pct, price, session_fact, sign, slug
from .sheet_post import _global_aliases, _plan_scene

HIGH_IMPACT_TAGS = {"RBI", "FED", "BUDGET", "POLICY", "GDP", "CPI", "INFLATION", "RESULTS",
                    "F&O"}
SECTION_CHIP = {"GLOBAL": "Global cues", "GIFT": "GIFT Nifty", "EVENTS": "Events",
                "PREV": "Last session", "SECTORS": "Sectors", "RADAR": "Radar",
                "FLOWS": "FII/DII", "VIX": "India VIX", "STOCKS": "Stocks", "WATCH": "Watch list"}


@dataclass
class PreMarketInputs:
    """Validated pre-open inputs. Every number here must already be validated upstream -
    GIFT Nifty in particular only when it passed the hide-if-unverified check."""
    session_date: dt.date                 # the session about to open
    previous_session: dt.date
    nifty_close: float | None = None
    nifty_pct: float | None = None
    nifty_series: list | None = None
    # {"value", "change_pct" (vs its own previous settlement - PRE), "time_label"?}; the legacy
    # {"vs_close_pct"} key (vs Nifty's close) is still read for older fixtures
    gift_nifty: dict | None = None
    # {"name", "pct", "value"?, "fact_id"?, "when"? ("overnight" | "this morning"),
    #  "note"? (the tile's timing label, e.g. "US CLOSE · MON" / "AT 7:45 AM IST")}
    global_cues: list = field(default_factory=list)
    events: list = field(default_factory=list)        # {"tag", "title", "when"?, "impact"?}
    sections: list = field(default_factory=list)
    refs: dict = field(default_factory=dict)


def pre_market_inputs_from_plan(plan, pres, session_date: dt.date) -> PreMarketInputs:
    """PRE_MARKET inputs from an existing validated report: the previous session (Nifty), the
    overnight cues and the events the editorial plan already selected."""
    m = pres.m
    glob, ev = _plan_scene(plan, "GLOBAL"), _plan_scene(plan, "EVENTS")
    cues = [{"name": it.title, "pct": it.numeric, "value": it.value,
             "fact_id": (it.source_fact_ids or [None])[0]}
            for it in (glob.items if glob else []) if it.numeric is not None]
    events = [{"tag": it.label or "EVENT", "title": it.title, "when": "", "impact": ""}
              for it in (ev.items if ev else [])]
    df = m.get("chart_df")
    sections = (["GLOBAL"] if cues else []) + (["EVENTS"] if events else []) + ["PREV"]
    return PreMarketInputs(
        session_date=session_date, previous_session=pres.session_date,
        nifty_close=m.get("close"), nifty_pct=m.get("pct"),
        nifty_series=[float(v) for v in df.tail(40)["Close"]] if df is not None else None,
        global_cues=cues, events=events, sections=sections)


def pre_market_sheet(inp: PreMarketInputs) -> HookFactSheet:
    facts = [session_fact(inp.session_date, "session about to open")]
    beats = []
    prev_wd = inp.previous_session.strftime("%A")
    facts.append(fact("prev.day", "SESSION", prev_wd,
                      f"The previous session was {inp.previous_session:%A %d %B}.",
                      prev_wd, aliases=(prev_wd[:3],), anchor_free=True))

    if inp.nifty_pct is not None:
        p = float(inp.nifty_pct)
        facts.append(fact("nifty.move", "INDEX_MOVE", "NIFTY 50",
                          f"Nifty 50 closed {pct(p)} in the previous session ({prev_wd}).",
                          pct(p), value=p, polarity=sign(p), aliases=("Nifty 50", "Nifty"),
                          claims=move_claims(p, policy.QUIET_INDEX_PCT, policy.BIG_INDEX_PCT),
                          refs=tuple(inp.refs.get("nifty", ()))))
    if inp.nifty_close is not None:
        facts.append(fact("nifty.close", "INDEX_LEVEL", "NIFTY 50",
                          f"Nifty 50's previous close: {price(inp.nifty_close, 2)}.",
                          price(inp.nifty_close, 2), value=float(inp.nifty_close),
                          aliases=("Nifty 50", "Nifty"), extra_numbers=(price(inp.nifty_close),)))
    if inp.nifty_series and inp.nifty_pct is not None:
        beats.append(beat("PREV_NIFTY", BeatKind.LINE,
                          f"Nifty 50 line to the previous close ({pct(inp.nifty_pct)})",
                          ("nifty.move",),
                          {"series": list(inp.nifty_series), "label": f"NIFTY 50 · {prev_wd.upper()}",
                           "value": price(inp.nifty_close, 2) if inp.nifty_close else "",
                           "change": pct(inp.nifty_pct), "positive": inp.nifty_pct >= 0}))

    g = inp.gift_nifty
    own = bool(g) and g.get("change_pct") is not None     # GIFT's own move since settlement
    if g and (own or g.get("vs_close_pct") is not None):
        gp = float(g["change_pct"] if own else g["vs_close_pct"])
        disp = (f"{abs(gp):.2f}% {'higher' if gp >= 0 else 'lower'}" if own else
                f"{abs(gp):.2f}% {'above' if gp >= 0 else 'below'} Nifty's close")
        tl = g.get("time_label") or ""     # freshness-sensitive: the reading's own IST time
        facts.append(fact("gift.gap", "GIFT_NIFTY", "GIFT NIFTY",
                          f"GIFT Nifty was trading {disp}"
                          + (" than its previous settlement" if own else "")
                          + (f" at {price(g['value'])}" if g.get("value") else "")
                          + (f" at {tl} IST" if tl else "") + ".",
                          disp, value=gp, polarity=sign(gp), aliases=("GIFT Nifty",),
                          claims=move_claims(gp, policy.QUIET_INDEX_PCT, policy.BIG_INDEX_PCT),
                          extra_numbers=((price(g["value"]),) if g.get("value") else ())
                          + ((tl.split(" ")[0],) if tl else ())))
        beats.append(beat("GIFT_NIFTY", BeatKind.CUE, f"GIFT Nifty tile: {disp}", ("gift.gap",),
                          {"name": "GIFT NIFTY", "value": pct(gp), "positive": gp >= 0,
                           "note": f"AT {tl} IST" if tl else
                          ("VS SETTLEMENT" if own else "VS NIFTY CLOSE")}))

    cues = sorted((c for c in inp.global_cues if c.get("pct") is not None),
                  key=lambda c: -abs(c["pct"]))
    cue_when, cue_time = {}, {}
    for i, c in enumerate(cues):
        fid = f"global.{slug(c['name'])}"
        claims = move_claims(c["pct"], None, policy.BIG_GLOBAL_PCT)
        if i == 0 and len(cues) > 1:
            claims.add("extreme_rank")
        ctime = c.get("time_label") or ""      # a LIVE reading carries its own IST time
        facts.append(fact(fid, "GLOBAL_CUE", c["name"],
                          f"{c['name']} {pct(c['pct'])} {c.get('when') or 'overnight'}"
                          + (" - the largest move among the overnight cues" if "extreme_rank" in claims else "")
                          + (f" (reading at {ctime} IST)" if ctime else "")
                          + ".", pct(c["pct"]), value=float(c["pct"]), polarity=sign(c["pct"]),
                          aliases=_global_aliases(c["name"]), claims=claims,
                          refs=(c["fact_id"],) if c.get("fact_id") else (),
                          extra_numbers=(ctime.split(" ")[0],) if ctime else ()))
        beats.append(beat(f"CUE:{slug(c['name'])}", BeatKind.CUE,
                          f"{c['name']} {pct(c['pct'])} overnight tile", (fid,),
                          {"name": c["name"], "value": pct(c["pct"]), "positive": c["pct"] >= 0,
                           "note": c.get("note") or "OVERNIGHT"}))
        cue_when[fid] = (c.get("when") or "overnight", c.get("note") or "OVERNIGHT")
        if ctime:
            cue_time[fid] = ctime

    for i, e in enumerate(inp.events):
        fid = f"event.{i}"
        tag = (e.get("tag") or "EVENT").upper()
        when = e.get("when") or ""
        high = tag in HIGH_IMPACT_TAGS or (e.get("impact") or "").upper() == "HIGH"
        facts.append(fact(fid, "EVENT", tag,
                          f"Scheduled today: {e['title']}" + (f", {when}" if when else "") + ".",
                          e["title"] + (f", {when}" if when else ""),
                          aliases=(e["title"],), claims={"high_impact"} if high else set(),
                          anchor_free=False))
        beats.append(beat(f"EVENT:{i}", BeatKind.EVENT, f"calendar card: {tag} - {e['title']}", (fid,),
                          {"tag": tag, "title": e["title"], "when": when,
                           "day": str(inp.session_date.day),
                           "month": inp.session_date.strftime("%b").upper()}))
    if inp.events:
        facts.append(count_fact("events.count", len(inp.events), "events today",
                                f"{len(inp.events)} named events are scheduled today.",
                                units=("events", "event")))

    labels = [SECTION_CHIP.get(s, s.title()) for s in inp.sections]
    return HookFactSheet(mode=HookMode.PRE_MARKET, session_date=inp.session_date, facts=facts,
                         beats=beats, sections=list(inp.sections), section_labels=labels,
                         metadata={"previous_session": inp.previous_session.isoformat(),
                                   "session_label": inp.session_date.strftime("%A"),
                                   "cue_when": cue_when, "cue_time": cue_time,
                                   "gift_time": (g or {}).get("time_label") or ""})


# PRE plan section -> the hook's section vocabulary (what the summary line may promise).
PRE_SECTION_KEYS = {"OVERNIGHT": "GLOBAL", "SETUP": "PREV", "FLOWS": "FLOWS", "VIX": "VIX",
                    "SECTORS": "SECTORS", "EVENT": "EVENTS", "STOCK_WATCH": "STOCKS",
                    "WATCH": "WATCH"}


def pre_market_inputs_from_brief(brief, plan) -> PreMarketInputs:
    """PRE_MARKET hook inputs from the PRE brief + its section plan: only what the Short will
    actually show (the plan's selected overnight cues, its GIFT strip, its one event)."""
    n = brief.nifty
    df = n.get("chart_df")
    cues = []
    ov = plan.overnight
    for c in (ov.cues if ov else []):
        cues.append({"name": c.name, "pct": c.change_pct, "value": c.value,
                     "when": "overnight" if c.kind == "SESSION_CLOSE" else "this morning",
                     "note": c.when,
                     "time_label": (c.when.replace("AT ", "").replace(" IST", "")
                                    if c.kind != "SESSION_CLOSE" else "")})
    gift = None
    if ov is not None and ov.gift is not None and brief.gift is not None:
        gift = {"value": brief.gift.value, "change_pct": brief.gift.change_pct,
                "time_label": ov.gift.time_label.replace(" IST", "")}
    events = []
    if plan.event is not None:
        e = plan.event
        # weekly expiry is routine: it may be a briefing item, never an EVENT_LED hook
        high = e.importance == "HIGH"
        events.append({"tag": e.event_type if high else "SCHEDULED", "title": e.title,
                       "when": "", "impact": "HIGH" if high else ""})
    sections = []
    for k in plan.order:
        sk = PRE_SECTION_KEYS.get(k)
        if sk and sk not in sections:
            sections.append(sk)
        if k == "OVERNIGHT" and gift is not None:
            sections.append("GIFT")
    return PreMarketInputs(
        session_date=brief.pre_date, previous_session=brief.previous_session,
        nifty_close=n.get("close"), nifty_pct=n.get("pct"),
        nifty_series=([float(v) for v in df.tail(40)["Close"]]
                      if df is not None and len(df) else None),
        gift_nifty=gift, global_cues=cues, events=events, sections=sections,
        refs={"nifty": tuple(n.get("fact_ids") or ())})


__all__ = ["PreMarketInputs", "pre_market_inputs_from_plan", "pre_market_inputs_from_brief",
           "pre_market_sheet", "HIGH_IMPACT_TAGS", "PRE_SECTION_KEYS"]

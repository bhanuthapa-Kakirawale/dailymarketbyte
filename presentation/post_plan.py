"""POST-MARKET editorial planner (Phase 3): which sections earn screen time, and what each says.

    validated report presentation + editorial plan + published Radar stories
            -> plan_post_sections() -> PostSectionPlan -> storyboard -> renderer

The storyboard only lays out what this plan decided; the renderer only draws. No model is
involved: every rule below is a deterministic threshold on validated values, so the same report
always produces the same Short, and every decision carries a written reason.

The POST Short answers "what actually happened today?" in one line of story:

    HOOK -> MARKET (how Nifty finished) -> SECTORS (where strength/weakness sat)
         -> [optional context that adds a NEW fact] -> RADAR (3 stories) -> CLOSE

The MARKET PULSE (the headline) is the one required section. Every other section must first
pass its VALUE TEST (it adds a fact the headline does not carry), and then - Editorial Planner
V3 - compete with every other qualifying story, the public-intelligence sections included
(UNDER THE SURFACE, MARKET EVENTS, IPO WATCH, EXCHANGE WATCH), in ONE deterministic arbitration
(`presentation.editorial_arbiter`, POST_POLICY): materiality tier, POST relevance, redundancy,
one story per family, slots and the runtime budget. A section is never shown merely because
data exists, and never stretched to fill time; every decision is in `editorial["trace"]`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from config import fmt_in
from editorial.config import MIN_SHORT_DURATION
from intelligence.flow_materiality import is_material

from . import editorial_arbiter as ea
from .editorial_candidates import (exchange_candidate, ipo_candidate, market_events_candidate,
                                   structure_candidate)

POST_PLAN_VERSION = "post-4.0"

# --------------------------------------------------------------------------- policy
FLAT_PCT = 0.10              # |Nifty| below this: "almost unchanged"
SLIGHT_PCT = 0.60            # below this: "slightly higher/lower"
SHARP_PCT = 1.25             # at/above this: "sharply higher/lower"
NEAR_EXTREME = 0.25          # close in the top/bottom quarter of the day's range
STRUCTURE_WINDOW = 20        # Nifty's 20-day range / 20-day average
CHART_SESSIONS = 24
# POST freeze: 4% / 6 pts fired on ~93% of real Nifty 100 sessions (decision replay), so Movers
# was a fixture, not an option. 7% / 12 pts leaves it for sessions whose single-stock moves are
# a story of their own.
MOVERS_MIN_PCT = 7.0         # a single non-Radar move this large is its own story
MOVERS_MIN_SPREAD = 12.0     # or top gain vs top fall this far apart
GLOBAL_MIN_PCT = 1.5         # a global move this large ...
GLOBAL_NIFTY_MIN_PCT = 1.0   # ... on a day Nifty itself moved at least this much, same side
FLOW_MIN_CRORE = 1000.0
HIGH_IMPACT_TAGS = {"RBI", "FED", "BUDGET", "POLICY"}

# Sector materiality (V3): the value-test thresholds PRE has always used for its own sector
# scene, plus one MATERIAL line - a rotation this wide is a story of its own.
SECTOR_NOTABLE_SPREAD = 1.0  # leader - laggard, pts
SECTOR_NOTABLE_PCT = 1.5     # or any one sector index moved this much
SECTOR_MATERIAL = 2.5        # spread or single-sector move for MATERIAL

# Editorial Planner V3 - the POST edition policy ("what changed in the completed session").
# Narrative order: HEADLINE -> WHY (sectors, internals, flows) -> WHAT ELSE (events) -> Radar.
SECTION_ORDER = ("PULSE", "NIFTY", "SECTORS", "STRUCTURE", "FLOWS", "MOVERS", "GLOBAL", "EVENT",
                 "MARKET_EVENTS", "IPO", "EXCHANGE", "RADAR")
OPTIONAL_SLOTS = 4           # a busy day must not turn the Short back into a dashboard
POLICY_EVENT = "POLICY"      # family of a high-impact policy day (RBI/FED/BUDGET)
HOOK_ESTIMATE_S = 5.0        # what the storyboard's runtime ceiling already assumes
CLOSING_S = 2.6
MIN_RUNTIME = MIN_SHORT_DURATION  # the readability-QA floor (editorial.config)
# Scene seconds (mirrors daily_video.storyboard's builders; a test pins them)
DUR = {"PULSE": 5.4, "MOVERS": 4.6, "GLOBAL": 4.8, "EVENT": 4.2, "FLOWS": 5.5,
       "SECTORS_BASE": 5.6, "SECTORS_PER_EXTRA": 0.1, "NIFTY": 6.0}
POST_RELEVANCE = {ea.SECTORS: 2, ea.BREADTH: 2, ea.FLOWS: 2, ea.INDEX: 2, POLICY_EVENT: 2,
                  ea.MOVERS: 1, ea.CALENDAR: 1, ea.IPO: 1, ea.EXCHANGE: 1, ea.GLOBAL: 1}


def post_policy(max_runtime: float = 62.0) -> ea.EditionPolicy:
    return ea.EditionPolicy(name="POST", relevance=POST_RELEVANCE, play_order=SECTION_ORDER,
                            anchor=("PULSE", "NIFTY"), optional_slots=OPTIONAL_SLOTS,
                            max_runtime=max_runtime, fixed_cost_s=HOOK_ESTIMATE_S + CLOSING_S,
                            min_runtime=MIN_RUNTIME)


@dataclass
class PostSectionPlan:
    version: str
    show_market_pulse: bool
    show_nifty_structure: bool
    show_sectors: bool
    show_movers: bool
    show_global_context: bool
    show_flows: bool
    show_special_event: bool
    show_radar: bool
    order: list                        # section keys, in play order
    reasons: dict                      # section key -> why it is in / out
    pulse: dict | None = None
    structure: dict | None = None      # RadarStoryModel-shaped dict for the Nifty chart story
    sectors: dict | None = None
    movers: dict | None = None
    global_context: dict | None = None
    special_event: dict | None = None
    notes: list = field(default_factory=list)
    # Editorial Planner V3: the arbitration record ({version, policy, order, lead, trace}) and
    # the selected public-intelligence models the storyboard lays out (STRUCTURE: [(insight,
    # provenance lines)], MARKET_EVENTS / IPO / EXCHANGE: their display models)
    editorial: dict | None = None
    public: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        public = self.public
        self.public = {}
        try:
            d = asdict(self)
        finally:
            self.public = public
        if d.get("structure"):
            d["structure"] = {"model": {k: v for k, v in d["structure"]["model"].items()
                                        if k not in ("candles", "ma_series")},
                              "texts": d["structure"]["texts"]}
        return d


def _scene(plan, kind):
    return next((s for s in getattr(plan, "scenes", []) if s.scene_type.value == kind), None)


def _pct(v, dec=2):
    return f"{v:+.2f}%" if dec == 2 else f"{v:+.{dec}f}%"


def direction_phrase(p: float) -> str:
    """How Nifty finished, in plain words - graded, never dramatised."""
    a = abs(p)
    if a < FLAT_PCT:
        return "Nifty closed almost unchanged"
    word = "higher" if p > 0 else "lower"
    if a < SLIGHT_PCT:
        return f"Nifty closed slightly {word}"
    if a < SHARP_PCT:
        return f"Nifty closed {word}"
    return f"Nifty closed sharply {word}"


def session_support(o, h, l, c) -> dict:
    """The ONE supporting fact for the market pulse: where the close sat in the day's range,
    else which side of the open it finished on."""
    if None in (o, h, l, c) or h <= l:
        return {"kind": "NONE", "text": ""}
    pos = (c - l) / (h - l)
    if pos >= 1 - NEAR_EXTREME:
        text, loc = "Closed near the day's high", "HIGH"
    elif pos <= NEAR_EXTREME:
        text, loc = "Closed near the day's low", "LOW"
    elif c > o:
        text, loc = "Closed above where it opened", "MID_UP"
    elif c < o:
        text, loc = "Closed below where it opened", "MID_DOWN"
    else:
        text, loc = "Closed where it opened", "MID"
    return {"kind": "SESSION_RANGE", "location": loc, "text": text, "open": float(o),
            "high": float(h), "low": float(l), "close": float(c), "low_label": "DAY LOW",
            "high_label": "DAY HIGH", "open_label": "OPEN", "close_label": "CLOSE"}


def structural_event(df) -> dict | None:
    """A NEW structural event in today's session - a 20-day range break, else a cross of the
    20-day average. A state that simply continued (below the average yesterday and today)
    is not an event and does not earn a chart."""
    if df is None or len(df) < STRUCTURE_WINDOW + 2 or "ema20" not in df.columns:
        return None
    c = [float(v) for v in df["Close"]]
    e = [float(v) for v in df["ema20"]]
    prior = df.iloc[-(STRUCTURE_WINDOW + 1):-1]
    hi, lo = float(prior["High"].max()), float(prior["Low"].min())
    if c[-1] > hi:
        return {"family": "RANGE_UP", "level": hi, "label": f"{STRUCTURE_WINDOW}-DAY HIGH",
                "sentence": "Nifty closed above its 20-day high", "callout": "Broke out here"}
    if c[-1] < lo:
        return {"family": "RANGE_DOWN", "level": lo, "label": f"{STRUCTURE_WINDOW}-DAY LOW",
                "sentence": "Nifty closed below its 20-day low", "callout": "Fell out here"}
    if c[-2] <= e[-2] and c[-1] > e[-1]:
        return {"family": "MA_UP", "level": e[-1], "label": "20-DAY AVG",
                "sentence": "Nifty moved above its 20-day average", "callout": "Crossed above here"}
    if c[-2] >= e[-2] and c[-1] < e[-1]:
        return {"family": "MA_DOWN", "level": e[-1], "label": "20-DAY AVG",
                "sentence": "Nifty moved below its 20-day average", "callout": "Crossed below here"}
    return None


def _structure_model(m, ev):
    """The Nifty chart story, in the SAME contract the Radar story scene draws (so it shares
    Radar's chart grammar) - one reference line, one event, one sentence."""
    from .radar_story import RadarStoryModel
    df = m["chart_df"].tail(CHART_SESSIONS)
    k = len(df)
    band = ma = None
    if ev["family"].startswith("RANGE"):
        full = m["chart_df"]
        prior = full.iloc[-(STRUCTURE_WINDOW + 1):-1]
        band = {"i0": max(0, k - 1 - STRUCTURE_WINDOW), "i1": k - 2,
                "low": float(prior["Low"].min()), "high": float(prior["High"].max())}
    else:
        ma = [float(v) for v in df["ema20"]]
    # No supporting strip here: the day's range is already the Market Pulse's supporting fact,
    # and repeating it would fail the section value test. The chart carries only its event.
    support = {"kind": "NONE"}
    model = RadarStoryModel(
        symbol="NIFTY 50", display_name="", display_change=_pct(m["pct"]),
        change_positive=m["pct"] >= 0, story_index=0, story_count=0, event_type=ev["family"],
        event_family=ev["family"], event_callout=ev["callout"], reference_label=ev["label"],
        reference_level=float(ev["level"]), reference_display=fmt_in(ev["level"], 1),
        latest_close=float(m["close"]),
        latest_close_display=fmt_in(m["close"], 1), close_label="CLOSE",
        candles={"open": [float(v) for v in df["Open"]], "high": [float(v) for v in df["High"]],
                 "low": [float(v) for v in df["Low"]], "close": [float(v) for v in df["Close"]]},
        band=band, ma_series=ma, support=support,
        takeaway=ev["sentence"] + ".", duration=6.0)
    return {"model": model.to_dict(), "texts": model.strings()}


def plan_post_sections(pres, plan, radar_stories=(), universe="Nifty 100", public=None,
                       admit=None, symbol_sectors=None,
                       max_runtime: float = 62.0) -> PostSectionPlan:
    """`radar_stories`: the PUBLISHED Radar stories [(presentation_story, story)] - the planner
    needs them only to keep Movers from repeating a Radar story.

    V3 inputs (all optional, so the planner still runs on the report alone):
      public          presentation.public_intelligence.PublicSections - the gate-admitted
                      UNDER THE SURFACE / MARKET EVENTS / IPO / EXCHANGE models, which now
                      compete for the same slots instead of being appended
      admit(key, post) -> bool   the publication gate's verdict on a report-backed section,
                      asked BEFORE arbitration so a refused section never takes a slot
      symbol_sectors  {symbol: Market Structure sector} - lets same-sector movers be
                      recognised as a repeat of the sector story"""
    m = pres.m
    reasons, notes = {}, []
    radar_syms = {sp["instrument"] for sp, _ in radar_stories}

    # ---------------------------------------------------------------- MARKET (core)
    pulse = None
    if m.get("close") is not None and m.get("pct") is not None:
        sup = session_support(m.get("open"), m.get("high"), m.get("low"), m.get("close"))
        pulse = {"headline": direction_phrase(m["pct"]), "label": "NIFTY 50",
                 "value": fmt_in(m["close"], 2), "change": _pct(m["pct"]),
                 "points": (f"{m['chg']:+.1f} pts" if m.get("chg") is not None else ""),
                 "positive": m["pct"] >= 0, "support": sup}
        reasons["PULSE"] = ("core: how Nifty finished - one primary fact (close and change) + one "
                            f"support ({sup['text'] or 'none available'})")
    else:
        reasons["PULSE"] = "omitted: no validated Nifty close/change in the report"

    ev = structural_event(m.get("chart_df"))
    structure = None
    if ev and pulse:
        structure = _structure_model(m, ev)
        reasons["NIFTY"] = f"included: a new structural event today - {ev['sentence'].lower()}"
    else:
        state = ""
        df = m.get("chart_df")
        if df is not None and "ema20" in df.columns and len(df) >= 2:
            below = [float(c) < float(e) for c, e in zip(df["Close"].tail(2), df["ema20"].tail(2))]
            if below[0] == below[1]:
                state = (f" (it was {'below' if below[1] else 'above'} its 20-day average on both "
                         f"the previous and the latest session - a continuing state, and it stayed "
                         f"inside its prior 20-day range)")
        reasons["NIFTY"] = ("omitted: no new structural event today, so no technical chart"
                            + state)

    # ---------------------------------------------------------------- SECTORS (core)
    secs = sorted((s for s in (pres.sec or []) if s.get("pct") is not None), key=lambda s: -s["pct"])
    sectors = None
    if secs:
        rows = [{"name": s["name"], "value": _pct(s["pct"]), "numeric": float(s["pct"]),
                 "positive": s["pct"] >= 0} for s in secs]
        up = sum(1 for s in secs if s["pct"] > 0)
        down = sum(1 for s in secs if s["pct"] < 0)
        n = len(secs)
        lead_tag, lag_tag = "LEADER", "LAGGARD"
        if n > 1 and down == n:
            # "IT led" on a day every sector fell reads as "IT rose" - say what happened instead
            # one line (the sector board's layout); the FELL LEAST card names the leader
            headline = f"All {n} tracked sector indices fell"
            lead_tag, lag_tag = "FELL LEAST", "FELL MOST"
        elif n > 1 and up == n:
            headline = f"All {n} tracked sector indices rose"
            lag_tag = "ROSE LEAST"
        elif n > 1:
            headline = f"{rows[0]['name']} led, {rows[-1]['name']} lagged"
        else:
            headline = f"{rows[0]['name']} {rows[0]['value']}"
        tone = (f"{up} of {n} tracked indices closed higher" if n > 1
                else "The only sector index in the report")
        sectors = {"headline": headline, "tone": tone, "rows": rows, "leader_tag": lead_tag,
                   "laggard_tag": lag_tag, "strip_title": "ALL SECTORS, STRONGEST FIRST"}
        reasons["SECTORS"] = (f"core: where strength and weakness sat - leader {rows[0]['name']} "
                              f"{rows[0]['value']}, laggard {rows[-1]['name']} {rows[-1]['value']}, "
                              f"{up}/{n} up")
    else:
        reasons["SECTORS"] = "omitted: no validated sector indices in the report"

    # ---------------------------------------------------------------- MOVERS (optional)
    g, lo = _scene(plan, "GAINERS"), _scene(plan, "LOSERS")
    top_g = g.items[0] if g and g.items else None
    top_l = lo.items[0] if lo and lo.items else None
    movers = None
    cands = [it for it in (top_g, top_l) if it is not None and it.numeric is not None]
    distinct = [it for it in cands if it.title not in radar_syms]
    big = [it for it in distinct if abs(it.numeric) >= MOVERS_MIN_PCT]
    spread = (top_g.numeric - top_l.numeric) if (top_g and top_l and top_g.numeric is not None
                                                 and top_l.numeric is not None) else 0.0
    wide = spread >= MOVERS_MIN_SPREAD and len(distinct) == 2
    if big or wide:
        show = distinct if wide else big
        movers = {"headline": "Biggest single-stock moves", "subline": universe,
                  "cards": [{"tag": "TOP GAIN" if it.numeric >= 0 else "TOP FALL", "name": it.title,
                             "value": it.value, "numeric": float(it.numeric),
                             "positive": it.numeric >= 0} for it in show]}
        reasons["MOVERS"] = ("included: " + (f"top gain and top fall {spread:.1f} pts apart"
                                              if wide else
                                              ", ".join(f"{it.title} {it.value}" for it in big) +
                                              f" - at least {MOVERS_MIN_PCT:.0f}% and not a Radar story"))
    else:
        why = []
        if cands:
            why.append("largest moves " + ", ".join(f"{it.title} {it.value}" for it in cands)
                       + f" are below {MOVERS_MIN_PCT:.0f}%")
            why.append(f"top gain vs top fall spread {spread:.1f} pts is below {MOVERS_MIN_SPREAD:.0f}")
        if len(distinct) < len(cands):
            why.append("a top mover is already a Radar story")
        gated = next((o for o in getattr(plan, "omitted", []) or []
                      if isinstance(o, dict) and o.get("reason") == "universe_coverage"), None)
        if gated is not None:
            reasons["MOVERS"] = ("omitted: universe coverage gate (" + gated.get("status", "")
                                 + ") - " + gated.get("detail", ""))
        else:
            reasons["MOVERS"] = ("omitted: no distinct stock-level fact beyond Radar - "
                                 + ("; ".join(why) if why else "no mover data"))

    # ---------------------------------------------------------------- FLOWS (optional)
    fl = _scene(plan, "FLOWS")
    flows_ok = False
    if fl is not None and len(fl.items) >= 2:
        materiality = (fl.metadata or {}).get("materiality") or {}
        has_history = materiality and all(
            ctx.get("magnitude_state") != "INSUFFICIENT_HISTORY" for ctx in materiality.values())
        if has_history:
            material = [ctx for ctx in materiality.values() if is_material(ctx)]
            flows_ok = bool(material)
            if flows_ok:
                bits = []
                for ctx in material:
                    if ctx["magnitude_state"] in ("LARGE_NET_BUY", "LARGE_NET_SELL"):
                        bits.append(f"{ctx['subject']} flow is {ctx['multiple_of_median']:.1f}x "
                                   f"the prior-10-session median")
                    elif ctx["streak_state"] == "DIRECTION_REVERSES":
                        bits.append(f"{ctx['subject']} reverses a "
                                   f"{ctx['prior_run_length']}-session run")
                    else:
                        bits.append(f"{ctx['subject']} streak reaches "
                                   f"{ctx['streak_length']} recorded sessions")
                reasons["FLOWS"] = "included: " + "; ".join(bits)
            else:
                reasons["FLOWS"] = ("omitted: within normal range of the prior 10 sessions "
                                    "(no large move, streak milestone, or reversal)")
        else:
            # legacy fallback: fewer than 10 prior eligible sessions of history to judge
            # materiality against, so the original fixed-threshold rule still applies.
            vals = [it.numeric for it in fl.items[:2] if it.numeric is not None]
            flows_ok = bool(vals) and max(abs(v) for v in vals) >= FLOW_MIN_CRORE
            reasons["FLOWS"] = (
                "INSUFFICIENT_HISTORY: included on the legacy rule - institutional net flow "
                f"of at least Rs {fmt_in(FLOW_MIN_CRORE, 0)} cr" if flows_ok else
                f"INSUFFICIENT_HISTORY: omitted on the legacy rule - net flows below Rs "
                f"{fmt_in(FLOW_MIN_CRORE, 0)} cr")
    else:
        reasons["FLOWS"] = "omitted: no validated FII/DII flow facts in the report - not fabricated"

    # ---------------------------------------------------------------- GLOBAL (optional, rare)
    glob = _scene(plan, "GLOBAL")
    global_context = None
    cues = [it for it in (glob.items if glob else []) if it.numeric is not None]
    p = m.get("pct") or 0.0
    strong = [it for it in cues if abs(it.numeric) >= GLOBAL_MIN_PCT and
              (it.numeric > 0) == (p > 0) and abs(p) >= GLOBAL_NIFTY_MIN_PCT]
    if strong:
        lead = max(strong, key=lambda it: abs(it.numeric))
        global_context = {"headline": "Global context", "lead": {"name": lead.title, "value": lead.value,
                                                                 "positive": lead.numeric >= 0},
                          "others": [{"name": it.title, "value": it.value, "positive": it.numeric >= 0}
                                     for it in cues if it is not lead][:3],
                          "note": "Moves in global markets on the same day, shown for context"}
        reasons["GLOBAL"] = (f"included: {lead.title} {lead.value} on a day Nifty moved "
                             f"{_pct(p)} the same way")
    else:
        biggest = max((abs(it.numeric) for it in cues), default=0.0)
        reasons["GLOBAL"] = ("omitted: overnight/global cues are a PRE-market topic; the largest "
                             f"global move ({biggest:.2f}%) does not meet the POST threshold "
                             f"(>= {GLOBAL_MIN_PCT}% on a >= {GLOBAL_NIFTY_MIN_PCT}% Nifty day, same side)")

    # ---------------------------------------------------------------- EVENT (optional, rare)
    evs = _scene(plan, "EVENTS")
    special = None
    hi = [it for it in (evs.items if evs else []) if (it.label or "").upper() in HIGH_IMPACT_TAGS]
    if hi:
        special = {"tag": hi[0].label.upper(), "title": hi[0].title}
        reasons["EVENT"] = f"included: high-impact scheduled event ({special['tag']})"
    else:
        tags = sorted({(it.label or "EVENT").upper() for it in (evs.items if evs else [])})
        reasons["EVENT"] = ("omitted: " + (f"events tagged {', '.join(tags)} are forward-looking "
                                           "'watch next' items, not post-market facts" if tags
                                           else "no events"))

    # ---------------------------------------------------------------- RADAR (private, locked)
    reasons["RADAR"] = (f"core: {len(radar_stories)} published Radar stories - the analytical close"
                        if radar_stories else "omitted: no published Radar stories")

    post = PostSectionPlan(
        version=POST_PLAN_VERSION, show_market_pulse=False, show_nifty_structure=False,
        show_sectors=False, show_movers=False, show_global_context=False, show_flows=False,
        show_special_event=False, show_radar=False, order=[], reasons=reasons, pulse=pulse,
        structure=structure, sectors=sectors, movers=movers, global_context=global_context,
        special_event=special, notes=notes)

    # ---------------------------------------------------------------- V3 arbitration
    nifty_pct = m.get("pct")
    session = getattr(pres, "session_date", None)
    report = getattr(pres, "report", None)
    cands = []
    if pulse is not None and admit is not None and not admit("PULSE", post):
        # the headline itself refused by the gate (e.g. synthetic data): it never renders, so it
        # must not be budgeted either - the storyboard records the refusal
        pulse = None
    if pulse is not None:
        cands.append(ea.EditorialCandidate(
            "POST.PULSE", "PULSE", ea.STRUCTURE_ANCHOR, ea.MATERIAL, "the headline: how Nifty "
            "finished", DUR["PULSE"], role=ea.REQUIRED, topics=frozenset({"index_direction"}),
            as_of=session))
    if radar_stories:
        # PRIVATE_ANALYTICS only (the public gate refuses every Radar story before this point);
        # the locked Radar section stays outside the public runtime budget, as before V3.
        cands.append(ea.EditorialCandidate(
            "POST.RADAR", "RADAR", "RADAR", ea.MATERIAL, reasons["RADAR"], 0.0,
            role=ea.REQUIRED, as_of=session))
    if structure is not None:
        fam = structure["model"]["event_family"]
        cands.append(ea.EditorialCandidate(
            "POST.NIFTY", "NIFTY", ea.INDEX,
            ea.MATERIAL if fam.startswith("RANGE") else ea.NOTABLE,
            reasons["NIFTY"].removeprefix("included: "), DUR["NIFTY"],
            topics=frozenset({"nifty_structure"}), as_of=session))
    if sectors is not None:
        tier, why, explains, topics = sector_grade(sectors["rows"], nifty_pct)
        cands.append(ea.EditorialCandidate(
            "POST.SECTORS", "SECTORS", ea.SECTORS, tier, why, sectors_cost(len(sectors["rows"])),
            explains_headline=explains, topics=topics, as_of=session))
    if movers is not None:
        cands.append(ea.EditorialCandidate(
            "POST.MOVERS", "MOVERS", ea.MOVERS, ea.NOTABLE,
            reasons["MOVERS"].removeprefix("included: "), DUR["MOVERS"],
            topics=mover_topics(movers["cards"], symbol_sectors), as_of=session))
    if fl is not None and len(fl.items) >= 2:
        fii = fl.items[0].numeric
        cands.append(ea.EditorialCandidate(
            "POST.FLOWS", "FLOWS", ea.FLOWS, _flows_tier(fl) if flows_ok else 0,
            reasons["FLOWS"].removeprefix("included: "), DUR["FLOWS"],
            quality=_facts_quality(report, _flow_fact_ids(report)),
            explains_headline=bool(fii is not None and nifty_pct
                                   and (fii > 0) == (nifty_pct > 0)),
            topics=frozenset({"flows_nse"}), as_of=session))
    if global_context is not None:
        cands.append(ea.EditorialCandidate(
            "POST.GLOBAL", "GLOBAL", ea.GLOBAL, ea.ROUTINE,
            reasons["GLOBAL"].removeprefix("included: "), DUR["GLOBAL"],
            topics=frozenset({"global"}), as_of=session))
    if special is not None:
        cands.append(ea.EditorialCandidate(
            "POST.EVENT", "EVENT", POLICY_EVENT, ea.MATERIAL,
            reasons["EVENT"].removeprefix("included: "), DUR["EVENT"],
            topics=frozenset({"policy_event"}), as_of=session))
    if public is not None:
        for ins, lines in public.structure:
            cands.append(structure_candidate(ins, nifty_pct, "POST", session, lines))
        if public.market_events:
            cands.append(market_events_candidate(public.market_events, "POST", session))
        if public.ipo:
            cands.append(ipo_candidate(public.ipo, "POST", session))
        if public.exchange:
            cands.append(exchange_candidate(public.exchange, "POST", session))

    if admit is not None:
        cands = [c if (c.role == ea.REQUIRED or c.tier <= 0 or c.section not in ADMITTED_HERE
                       or admit(c.section, post))
                 else ea.with_quality(c, ea.PUBLICATION_BLOCKED) for c in cands]
    decision = ea.arbitrate(cands, post_policy(max_runtime))
    apply_decision(post, decision)
    return post


# Report-backed sections the publication gate is asked about here (the public-intelligence
# sections were admitted fact-by-fact upstream, in presentation.public_intelligence)
ADMITTED_HERE = frozenset({"NIFTY", "SECTORS", "MOVERS", "FLOWS", "GLOBAL", "EVENT"})
_SHOW = {"PULSE": "show_market_pulse", "NIFTY": "show_nifty_structure", "SECTORS": "show_sectors",
         "MOVERS": "show_movers", "GLOBAL": "show_global_context", "FLOWS": "show_flows",
         "EVENT": "show_special_event", "RADAR": "show_radar"}


def apply_decision(post: PostSectionPlan, decision) -> None:
    """Write the arbiter's verdict into the plan: show flags, play order, one reason line per
    competing section, and the selected public-intelligence models."""
    shown = decision.sections_shown()
    for key, attr in _SHOW.items():
        setattr(post, attr, key in shown)
    post.order = list(decision.order)
    for key in sorted({t["section"] for t in decision.trace} - {"PULSE", "RADAR"}):
        post.reasons[key] = decision.section_reason(key, post.reasons.get(key))
    if not post.show_nifty_structure:
        post.structure = None
    if not post.show_sectors:
        post.sectors = None
    if not post.show_movers:
        post.movers = None
    if not post.show_global_context:
        post.global_context = None
    if not post.show_special_event:
        post.special_event = None
    pub = {}
    for cid in decision.selected:
        c = decision.candidates[cid]
        if c.section == "STRUCTURE":
            pub.setdefault("STRUCTURE", []).append(c.payload)
        elif c.section in ("MARKET_EVENTS", "IPO", "EXCHANGE"):
            pub[c.section] = c.payload
    post.public = pub
    post.editorial = decision.to_dict()


# --------------------------------------------------------------------------- V3 grading
def sectors_cost(n: int) -> float:
    return round(DUR["SECTORS_BASE"] + DUR["SECTORS_PER_EXTRA"] * max(0, n - 6), 2)


def sector_grade(rows, nifty_pct) -> tuple:
    """(tier, reason, explains_headline, topics) for a sector board, strongest first. NOTABLE is
    the value test PRE has always applied to its own sector scene; MATERIAL a wide rotation."""
    vals = [float(r["numeric"]) for r in rows]
    spread = vals[0] - vals[-1] if len(vals) > 1 else 0.0
    big = max(abs(v) for v in vals)
    if spread >= SECTOR_MATERIAL or big >= SECTOR_MATERIAL:
        tier = ea.MATERIAL
    elif spread >= SECTOR_NOTABLE_SPREAD or big >= SECTOR_NOTABLE_PCT:
        tier = ea.NOTABLE
    else:
        tier = ea.ROUTINE
    why = (f"sector spread {spread:.2f} pts, largest single move {big:.2f}% "
           f"(leader {rows[0]['name']} {rows[0]['value']}, laggard {rows[-1]['name']} "
           f"{rows[-1]['value']})")
    explains = bool(nifty_pct) and ((nifty_pct > 0 and vals[0] > 0) or
                                    (nifty_pct < 0 and vals[-1] < 0))
    topics = {"sectors_board"} | {t for t in (ea.sector_topic(rows[0]["name"]),
                                              ea.sector_topic(rows[-1]["name"])) if t}
    return tier, why, explains, frozenset(topics)


def mover_topics(cards, symbol_sectors=None) -> frozenset:
    """Each card is the sector story's fact when its stock sits in a mapped sector (so a mover
    list dominated by the leading/lagging sector is a repeat), else its own fact."""
    out = set()
    for c in cards:
        t = ea.sector_topic((symbol_sectors or {}).get(c["name"]))
        out.add(t or f"mover:{c['name']}")
    return frozenset(out)


def _flows_tier(fl) -> int:
    materiality = (fl.metadata or {}).get("materiality") or {}
    states = [ctx for ctx in materiality.values() if is_material(ctx)]
    if any(ctx.get("magnitude_state") in ("LARGE_NET_BUY", "LARGE_NET_SELL") for ctx in states):
        return ea.MATERIAL
    return ea.NOTABLE


def _flow_fact_ids(report) -> list:
    return list(((getattr(report, "institutional_flows", None) or {}).get("fact_ids")) or [])


def _facts_quality(report, fact_ids) -> str:
    """OK; PARTIAL when a fact is backed ONLY by AI observations (never VERIFIED - see
    core.validation); STALE when the report marked it stale."""
    from core.enums import SourceType, ValidationStatus
    if report is None or not hasattr(report, "fact"):
        return ea.OK
    quality = ea.OK
    for fid in fact_ids:
        f = report.fact(fid)
        if f is None:
            continue
        if getattr(f, "validation_status", None) is ValidationStatus.STALE:
            return ea.STALE
        obs = list(getattr(f, "observations", None) or [])
        if obs and all(getattr(o, "source_type", None) is SourceType.AI for o in obs):
            quality = ea.PARTIAL
    return quality


__all__ = ["PostSectionPlan", "plan_post_sections", "direction_phrase", "session_support",
           "structural_event", "POST_PLAN_VERSION", "SECTION_ORDER", "OPTIONAL_SLOTS",
           "post_policy", "sector_grade", "sectors_cost", "mover_topics", "apply_decision", "DUR"]

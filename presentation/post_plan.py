"""POST-MARKET editorial planner (Phase 3): which sections earn screen time, and what each says.

    validated report presentation + editorial plan + published Radar stories
            -> plan_post_sections() -> PostSectionPlan -> storyboard -> renderer

The storyboard only lays out what this plan decided; the renderer only draws. No model is
involved: every rule below is a deterministic threshold on validated values, so the same report
always produces the same Short, and every decision carries a written reason.

The POST Short answers "what actually happened today?" in one line of story:

    HOOK -> MARKET (how Nifty finished) -> SECTORS (where strength/weakness sat)
         -> [optional context that adds a NEW fact] -> RADAR (3 stories) -> CLOSE

Core sections appear whenever their data exists. Optional sections must pass a VALUE TEST:
they appear only when they add a fact that no core section already carries. A section is never
shown merely because data exists, and never stretched to fill time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from config import fmt_in

POST_PLAN_VERSION = "post-3.0"

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

SECTION_ORDER = ("PULSE", "NIFTY", "SECTORS", "MOVERS", "FLOWS", "GLOBAL", "EVENT", "RADAR")
# At most this many optional sections per Short, taken in editorial priority - a busy day
# must not turn the Short back into a dashboard (and the runtime stays <= 65 s).
OPTIONAL_BUDGET = 2
OPTIONAL_PRIORITY = ("NIFTY", "FLOWS", "MOVERS", "EVENT", "GLOBAL")


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

    def to_dict(self) -> dict:
        d = asdict(self)
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


def plan_post_sections(pres, plan, radar_stories=(), universe="Nifty 100") -> PostSectionPlan:
    """`radar_stories`: the PUBLISHED Radar stories [(presentation_story, story)] - the planner
    needs them only to keep Movers from repeating a Radar story."""
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
        vals = [it.numeric for it in fl.items[:2] if it.numeric is not None]
        flows_ok = bool(vals) and max(abs(v) for v in vals) >= FLOW_MIN_CRORE
        reasons["FLOWS"] = ("included: institutional net flow of at least Rs "
                            f"{fmt_in(FLOW_MIN_CRORE, 0)} cr" if flows_ok else
                            f"omitted: net flows below Rs {fmt_in(FLOW_MIN_CRORE, 0)} cr")
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

    # ---------------------------------------------------------------- RADAR (core)
    reasons["RADAR"] = (f"core: {len(radar_stories)} published Radar stories - the analytical close"
                        if radar_stories else "omitted: no published Radar stories")

    show = {"PULSE": pulse is not None, "NIFTY": structure is not None,
            "SECTORS": sectors is not None, "MOVERS": movers is not None, "FLOWS": flows_ok,
            "GLOBAL": global_context is not None, "EVENT": special is not None,
            "RADAR": bool(radar_stories)}
    qualified = [k for k in OPTIONAL_PRIORITY if show[k]]
    for k in qualified[OPTIONAL_BUDGET:]:
        show[k] = False
        reasons[k] = (f"omitted: qualified ({reasons[k].removeprefix('included: ')}), but the "
                      f"Short already carries {OPTIONAL_BUDGET} optional sections of higher "
                      f"priority ({', '.join(qualified[:OPTIONAL_BUDGET])})")
    structure = structure if show["NIFTY"] else None
    movers = movers if show["MOVERS"] else None
    global_context = global_context if show["GLOBAL"] else None
    special = special if show["EVENT"] else None
    order = [k for k in SECTION_ORDER if show[k]]
    return PostSectionPlan(
        version=POST_PLAN_VERSION, show_market_pulse=show["PULSE"],
        show_nifty_structure=show["NIFTY"], show_sectors=show["SECTORS"],
        show_movers=show["MOVERS"], show_global_context=show["GLOBAL"], show_flows=show["FLOWS"],
        show_special_event=show["EVENT"], show_radar=show["RADAR"], order=order, reasons=reasons,
        pulse=pulse, structure=structure, sectors=sectors, movers=movers,
        global_context=global_context, special_event=special, notes=notes)


__all__ = ["PostSectionPlan", "plan_post_sections", "direction_phrase", "session_support",
           "structural_event", "POST_PLAN_VERSION", "SECTION_ORDER", "OPTIONAL_BUDGET",
           "OPTIONAL_PRIORITY"]

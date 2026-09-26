"""The storyboard: what the complete Daily Market Byte video says, in what order, for how long.

    MarketReport ──> editorial ShortsPlan ──┐
                                            ├──> build_storyboard ──> Storyboard ──> composer
    RadarPresentation + DailyRadarResult ───┤      (all viewer text decided here)
    RadarVisualEvidence (local OHLCV) ──────┘

This is a presentation layer, not a renderer and not an analyst. It selects nothing new: main
sections come from the editorial plan's own scenes and items (same selection, same order of
facts), the Radar section from the Radar presentation's own story order. It computes no market
fact - every number it puts on screen is a validated value it only FORMATS (rounding, sign,
Indian digit grouping, a unit). Plain-English sentences are fixed templates keyed by the event
type / direction the detectors already produced. Every string it emits is exposed through
`Storyboard.public_text()` for the content-safety scan.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from config import fmt_in
from presentation.post_plan import plan_post_sections
from presentation.radar_story import build_radar_story_model

SECTION_LABELS = {
    "HOOK": "", "PULSE": "MARKET PULSE", "NIFTY": "NIFTY 50", "FLOWS": "FII / DII",
    "GLOBAL": "GLOBAL CONTEXT", "EVENT": "TODAY'S EVENT",
    "SECTORS": "SECTORS", "MOVERS": "BIGGEST MOVERS", "CONTEXT": "IN CONTEXT",
    "RADAR": "MARKET RADAR", "AHEAD": "LOOK AHEAD", "CLOSING": "",
    "STRUCTURE": "UNDER THE SURFACE", "EXCHANGE": "EXCHANGE WATCH", "IPO": "IPO WATCH",
}
AGENDA_NAMES = {"PULSE": "Market", "NIFTY": "Nifty", "FLOWS": "FII/DII", "SECTORS": "Sectors",
                "MOVERS": "Movers", "RADAR": "Radar", "AHEAD": "Look ahead",
                "STRUCTURE": "Under the surface", "EXCHANGE": "Exchange watch", "IPO": "IPO watch"}

# Primary-event priority: the longer the window a level held for, the bigger the change.
EVENT_PRIORITY = ("BREAK_ABOVE_50D_RANGE", "BREAK_BELOW_50D_RANGE", "BREAK_ABOVE_20D_RANGE",
                  "BREAK_BELOW_20D_RANGE", "CROSS_ABOVE_SMA50", "CROSS_BELOW_SMA50",
                  "CROSS_ABOVE_SMA20", "CROSS_BELOW_SMA20", "RANGE_COMPRESSION")

# One plain sentence per detector event type - the story's Level-1 headline.
EVENT_HEADLINE = {
    "BREAK_ABOVE_20D_RANGE": "Closed above its 20-day high",
    "BREAK_BELOW_20D_RANGE": "Closed below its 20-day low",
    "BREAK_ABOVE_50D_RANGE": "Closed above its 50-day high",
    "BREAK_BELOW_50D_RANGE": "Closed below its 50-day low",
    "CROSS_ABOVE_SMA20": "Moved above its 20-day average",
    "CROSS_BELOW_SMA20": "Slipped below its 20-day average",
    "CROSS_ABOVE_SMA50": "Moved above its 50-day average",
    "CROSS_BELOW_SMA50": "Slipped below its 50-day average",
    "RANGE_COMPRESSION": "Trading range has tightened",
}
EVENT_CALLOUT = {
    "BREAK_ABOVE_20D_RANGE": "Broke out here", "BREAK_ABOVE_50D_RANGE": "Broke out here",
    "BREAK_BELOW_20D_RANGE": "Fell out here", "BREAK_BELOW_50D_RANGE": "Fell out here",
    "CROSS_ABOVE_SMA20": "Crossed above here", "CROSS_ABOVE_SMA50": "Crossed above here",
    "CROSS_BELOW_SMA20": "Crossed below here", "CROSS_BELOW_SMA50": "Crossed below here",
    "RANGE_COMPRESSION": "Range tightened",
}
FAMILY_CHIP = {"STRUCTURE": "Price", "VOLUME": "Volume", "RELATIVE_PERFORMANCE": "vs Nifty"}

MODE_DURATION = {"CONVERGENCE": 8.5, "BREAKOUT": 7.0, "BREAKDOWN": 7.5, "QUIET": 8.0,
                 "MIXED": 7.5, "TEXT": 6.0}


def event_window(event: str) -> int | None:
    return 50 if "50" in event else (20 if "20" in event else None)


def event_direction(event: str) -> int:
    return 1 if ("ABOVE" in event) else (-1 if "BELOW" in event else 0)


def primary_event(events: list) -> str | None:
    for e in EVENT_PRIORITY:
        if e in (events or []):
            return e
    return None


def choose_mode(story_pres: dict, story: dict, evidence) -> str:
    """Pick the storytelling mechanism from the story's own data - never from its symbol."""
    if evidence is None:
        return "TEXT"
    if story_pres.get("visual_type") == "QUIET_SIGNAL_CARD" or story_pres.get("quiet_signal"):
        return "QUIET"
    if story.get("direction") == "MIXED" or story_pres.get("visual_type") == "MIXED_SIGNAL_CARD":
        return "MIXED"
    if (story.get("independent_signal_count") or 0) >= 3:
        return "CONVERGENCE"
    ev = primary_event((story.get("technical_context") or {}).get("events"))
    if ev and event_direction(ev) < 0:
        return "BREAKDOWN"
    return "BREAKOUT"


# --------------------------------------------------------------------------- formatting only
def pct(v, dec=2):
    return f"{v:+.{dec}f}%"


def price(v, dec=0):
    return fmt_in(v, dec)


def signed_price(v, dec=1):
    return ("+" if v >= 0 else "-") + fmt_in(abs(v), dec)


def pts(v):
    return f"{v:+.1f} pts"


@dataclass
class SceneSpec:
    kind: str
    section: str
    duration: float
    headline: str = ""
    subline: str = ""
    takeaway: str = ""
    texts: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    freeze: dict = field(default_factory=dict)
    counter: str | None = None
    dim_background: bool = True
    self_animated: bool = False     # the scene animates its own entry (no composer fade-in)

    def public_text(self, prefix: str) -> dict:
        out = {}
        for name in ("headline", "subline", "takeaway"):
            v = getattr(self, name)
            if v:
                out[f"{prefix}.{name}"] = v
        for k, v in self.texts.items():
            if isinstance(v, str) and v:
                out[f"{prefix}.{k}"] = v
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, str) and vv:
                        out[f"{prefix}.{k}.{kk}"] = vv
            elif isinstance(v, (list, tuple)):
                for i, s in enumerate(v):
                    if isinstance(s, str) and s:
                        out[f"{prefix}.{k}.{i}"] = s
                    elif isinstance(s, dict):
                        for kk, vv in s.items():
                            if isinstance(vv, str) and vv:
                                out[f"{prefix}.{k}.{i}.{kk}"] = vv
        return out


@dataclass
class Storyboard:
    session_date: dt.date
    date_label: str
    kicker: str
    scenes: list
    sources: dict = field(default_factory=dict)
    omitted: list = field(default_factory=list)
    hook_plan: dict | None = None   # the Dynamic Hook Engine's audit record, when it ran
    post_plan: dict | None = None   # the POST editorial plan: sections in/out, with reasons
    radar_guard: list = field(default_factory=list)   # move-guard verdict per Radar story
    # Per-video chip labels (PRE: "FRIDAY'S SETUP", "FII / DII · THU"); falls back to
    # SECTION_LABELS, so a POST storyboard - which never sets it - is unchanged.
    section_labels: dict = field(default_factory=dict)
    pre_plan: dict | None = None    # the PRE editorial plan, when this is a PRE Short
    # publication boundary: the profile this storyboard was built under, the gate's record of
    # every fact considered/allowed/blocked, and the audit blocks of the public sections
    publication_profile: str = "PUBLIC_UNREGISTERED"
    publication: dict | None = None
    public_audit: dict = field(default_factory=dict)
    gate: object | None = None
    # displayed-claim trace (publication.claims): every visible number -> fact(s) / derivation
    claims: list = field(default_factory=list)
    claim_texts: dict = field(default_factory=dict)

    @property
    def total_duration(self) -> float:
        return round(sum(s.duration for s in self.scenes), 3)

    def starts(self) -> list:
        out, t = [], 0.0
        for s in self.scenes:
            out.append(t)
            t += s.duration
        return out

    def sections(self) -> list:
        """Contiguous runs of scenes sharing a section key: `(key, label, start, end)`."""
        out = []
        for s, t0 in zip(self.scenes, self.starts()):
            if out and out[-1][0] == s.section:
                k, lab, a, _ = out[-1]
                out[-1] = (k, lab, a, t0 + s.duration)
            else:
                label = self.section_labels.get(s.section,
                                                SECTION_LABELS.get(s.section, s.section))
                out.append((s.section, label, t0, t0 + s.duration))
        return out

    def public_text(self) -> dict:
        out = {}
        for i, s in enumerate(self.scenes):
            out.update(s.public_text(f"{i:02d}_{s.kind.lower()}"))
        for i, (_, label, _, _) in enumerate(self.sections()):
            if label:
                out[f"section.{i}"] = label
        return out

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date.isoformat(), "date_label": self.date_label,
            "total_duration": self.total_duration, "sources": self.sources,
            "omitted": self.omitted, "hook_plan": self.hook_plan, "post_plan": self.post_plan,
            "radar_guard": self.radar_guard,
            "publication_profile": self.publication_profile, "publication": self.publication,
            **({"pre_plan": self.pre_plan, "section_labels": self.section_labels}
               if self.pre_plan is not None else {}),
            "scenes": [{"kind": s.kind, "section": s.section, "duration": s.duration,
                        "headline": s.headline, "subline": s.subline, "takeaway": s.takeaway,
                        "texts": s.texts, "counter": s.counter, "freeze": s.freeze}
                       for s in self.scenes],
        }


# --------------------------------------------------------------------------- main sections
def _plan_scene(plan, kind):
    return next((s for s in plan.scenes if s.scene_type.value == kind), None)


def _hook(plan, sections_present, radar_count):
    hook = _plan_scene(plan, "HOOK")
    label = (hook.primary_text if hook else "") or "NIFTY"
    value = (hook.primary_value if hook else "") or ""
    agenda = " • ".join(AGENDA_NAMES[k] for k in sections_present if k in AGENDA_NAMES)
    texts = {"kicker": "YOUR DAILY MARKET BRIEFING", "label": label, "value": value,
             "agenda": agenda}
    if radar_count:
        texts["teaser"] = f"+ {radar_count} stocks where something changed underneath"
    pos = hook.primary_positive if hook else None
    return SceneSpec(
        kind="HOOK", section="HOOK", duration=4.2, headline=value,
        subline=(hook.secondary_text if hook else ""), texts=texts,
        data={"positive": pos}, dim_background=False,
        freeze={"t": 3.6, "what": f"{label} {value} - the session's headline number",
                "where": "the large figure at the centre of the screen",
                "why": "it answers 'how did the market do?' before anything else; the agenda "
                       "line says what the next minute covers"})


# Main-section scenes (Phase 3). Each packages ONE model the POST planner already built
# (`presentation.post_plan`) - the storyboard decides no significance and computes nothing.
def _pulse_scene(pm):
    sup = pm.get("support") or {}
    texts = {"headline": pm["headline"], "label": pm["label"], "value": pm["value"],
             "change": pm["change"], "points": pm.get("points", ""),
             "support": sup.get("text", ""), "low_label": sup.get("low_label", ""),
             "high_label": sup.get("high_label", ""), "open_label": sup.get("open_label", ""),
             "close_label": sup.get("close_label", "")}
    return SceneSpec(
        kind="PULSE", section="PULSE", duration=5.4, headline=pm["headline"], texts=texts,
        data={"positive": pm["positive"], "support": sup},
        freeze={"t": 4.8, "what": f"{pm['headline']}: {pm['value']} ({pm['change']})",
                "where": "the large close and change figure; below it the day's path from open "
                         "to close inside the low-high range",
                "why": sup.get("text") or "how the session finished"})


def _structure_scene(st):
    m = st["model"]
    return SceneSpec(
        kind="NIFTY", section="NIFTY", duration=m["duration"], headline=m["takeaway"],
        takeaway=m["takeaway"], texts=st["texts"], data=m,
        freeze={"t": round(m["duration"] - 0.6, 2), "what": m["takeaway"],
                "where": "the ringed last Nifty candle against its one reference line",
                "why": "a new structural event in today's session", "mode": m["event_family"]})


def _sectors_scene(sm):
    n = len(sm["rows"])
    texts = {"headline": sm["headline"], "tone": sm["tone"], "leader_tag": sm["leader_tag"],
             "laggard_tag": sm["laggard_tag"], "strip_title": sm["strip_title"],
             "rows": [{"name": r["name"], "value": r["value"]} for r in sm["rows"]]}
    dur = 5.6 + 0.1 * max(0, n - 6)
    return SceneSpec(
        kind="SECTORS", section="SECTORS", duration=round(dur, 2), headline=sm["headline"],
        subline=sm["tone"], texts=texts, data={"rows": sm["rows"]},
        freeze={"t": round(dur - 0.6, 2), "what": sm["headline"],
                "where": "the large leader card and the laggard card; every sector ranked in the "
                         "heat strip underneath",
                "why": sm["tone"]})


def _movers_scene(mm):
    texts = {"headline": mm["headline"], "subline": mm.get("subline", ""),
             "cards": [{"tag": c["tag"], "name": c["name"], "value": c["value"]} for c in mm["cards"]]}
    return SceneSpec(
        kind="MOVERS", section="MOVERS", duration=4.6, headline=mm["headline"], texts=texts,
        data={"cards": mm["cards"]},
        freeze={"t": 4.0, "what": "; ".join(f"{c['name']} {c['value']}" for c in mm["cards"]),
                "where": "the one or two single-stock cards", "why": "a stock-level move large "
                "enough to be its own fact, not already a Radar story"})


def _global_scene(gm):
    texts = {"headline": gm["headline"], "note": gm.get("note", ""),
             "lead": {"name": gm["lead"]["name"], "value": gm["lead"]["value"]},
             "others": [{"name": o["name"], "value": o["value"]} for o in gm.get("others", [])]}
    return SceneSpec(
        kind="GLOBAL", section="GLOBAL", duration=4.8, headline=gm["headline"], texts=texts,
        data={"lead": gm["lead"], "others": gm.get("others", [])},
        freeze={"t": 4.2, "what": f"{gm['lead']['name']} {gm['lead']['value']}",
                "where": "the large global tile", "why": gm.get("note", "")})


def _event_scene(em):
    return SceneSpec(
        kind="EVENT", section="EVENT", duration=4.2, headline="Today's scheduled event",
        texts={"headline": "Today's scheduled event", "tag": em["tag"], "title": em["title"]},
        freeze={"t": 3.6, "what": em["title"], "where": "the event card",
                "why": "a high-impact scheduled event on the session"})


def _flows(plan):
    scene = _plan_scene(plan, "FLOWS")
    if scene is None or len(scene.items) < 2:
        return None
    bars = [{"name": it.title, "value": it.value, "tag": it.label, "numeric": it.numeric,
             "positive": it.positive} for it in scene.items]
    fii, dii = bars[0], bars[1]
    # net cash-market flows: net sellers / buyers - never gross "sold" / "bought"
    side = lambda b: "net buyers" if b["positive"] else "net sellers"
    headline = (f"FIIs and DIIs were both {side(fii)}" if fii["positive"] == dii["positive"]
                else f"FIIs were {side(fii)}; DIIs were {side(dii)}")
    sub = scene.secondary_text or "Net cash-market flows"
    if "provisional" not in sub.lower():
        sub = f"{sub} (provisional)"
    return SceneSpec(
        kind="FLOWS", section="FLOWS", duration=5.5, headline=headline,
        subline=sub,
        texts={"bars": bars},
        freeze={"t": 4.8, "what": headline,
                "where": "opposing bars from the centre line - left is selling, right is buying",
                "why": "shows who supplied and who absorbed the selling"})


def _closing(plan, radar_closing):
    """2.6 s sign-off (Phase 3): brand, one light CTA, the signals-not-advice line."""
    outro = _plan_scene(plan, "OUTRO")
    texts = {"cta": (outro.primary_text if outro else "") or "SUBSCRIBE",
             "note": radar_closing or ""}
    return SceneSpec(kind="CLOSING", section="CLOSING", duration=2.6, texts=texts,
                     dim_background=False,
                     freeze={"t": 2.1, "what": "sign-off: Daily Market Byte",
                             "where": "brand lockup at the centre",
                             "why": "brand recall; restates that signals are not advice"})


# --------------------------------------------------------------------------- radar section
def _radar_intro(stories):
    n = len(stories)
    return SceneSpec(
        kind="RADAR_INTRO", section="RADAR", duration=3.8, headline="Beyond the biggest movers",
        subline="What changed underneath?",
        texts={"count": f"{n} stocks flagged by Market Radar",
               "names": [s["instrument"] for s in stories]},
        freeze={"t": 3.3, "what": f"Market Radar section: {n} stocks flagged",
                "where": "radar sweep with one blip per flagged stock",
                "why": "marks the switch from 'what moved' to 'what changed underneath'"})


RADAR_PUBLISH_LIMIT = 3      # Phase 2: the POST Short publishes the selector's first three


def _radar_story(i, n, sp, story, evidence):
    """One Radar stock story from its presentation model (`presentation.radar_story`): the
    model decides every value and string; this only packages it as a scene."""
    m = build_radar_story_model(sp, story, evidence, i, n)
    where = {
        "RANGE_UP": "the ringed last candle above the shaded prior range; its level and the close "
                    "tagged on the right",
        "RANGE_DOWN": "the ringed last candle below the shaded prior range; its level and the "
                      "close tagged on the right",
        "MA_UP": "the ringed last candle above the cyan average line",
        "MA_DOWN": "the ringed last candle below the cyan average line",
        "COMPRESSION": "the ringed last candle",
        "VOLUME": "the amber session bar towering over the dashed prior-20-session average; "
                  "candles above are context only (no chart event claimed)",
        "SESSION": "the close's position in the day's range; candles above are context only "
                   "(no chart event claimed)",
        "TEXT": "the text card (no local chart evidence available)",
    }[m.event_family]
    support = m.support.get("label", "")
    return SceneSpec(
        kind="RADAR_STORY", section="RADAR", duration=m.duration, headline=m.takeaway,
        takeaway=m.takeaway, texts=m.strings(), data=m.to_dict(), counter=f"{i} / {n}",
        freeze={"t": round(m.duration - 0.6, 2), "what": f"{m.symbol}: {m.takeaway}",
                "where": where + (f"; supporting fact: {support}" if support else ""),
                "why": m.takeaway, "mode": m.event_family})


# --------------------------------------------------------------------------- dynamic hook
HOOK_WHERE = {
    "DEPTH_LOLLIPOP": "the lollipop chart: Nifty's dot on the 0% line, the flagged stocks' dots "
                      "far above and below it on the same % scale",
    "HEADLINE_NUMBER": "the giant % figure over the candle strip, last candle ringed",
    "VERSUS_SPLIT": "two panels with opposite bars and the VS badge between them",
    "SIGNAL_STACK_CHART": "the event marker on the price line and the amber volume bar under it",
    "OVERNIGHT_BOARD": "the large overnight tile (moon icon) and the smaller cue tiles",
    "EVENT_CALENDAR": "the calendar page and the event card beside it",
    "NUMBERED_LIST": "the three numbered rows",
}


# Editorial stamps on the teaser collage: FIXED per (mode, archetype) - never model-written,
# declared like every other string and scanned. Each is true whenever its archetype is chosen:
# QUIET needs a "quiet" index claim to be selected; CONTRAST's two sides are the same session
# (POST) or the same stock (CUSTOM). Index = beat position; "last" = the final beat.
HOOK_STAMPS = {
    ("POST_MARKET", "QUIET_MARKET_HIDDEN_ACTION"): {0: "QUIET DAY?", "last": "BUT LOOK UNDERNEATH"},
    ("POST_MARKET", "CONTRAST"): {"last": "SAME SESSION"},
    ("CUSTOM_SINGLE_STOCK", "CONTRAST"): {"last": "SAME STOCK"},
    ("CUSTOM_SINGLE_STOCK", "QUIET_MARKET_HIDDEN_ACTION"): {"last": "BUT LOOK UNDERNEATH"},
}


def hook_stamps(mode: str, archetype: str, n_beats: int) -> list:
    table = HOOK_STAMPS.get((mode, archetype), {})
    out = [table.get(i, "") for i in range(n_beats)]
    if n_beats >= 2 and table.get("last"):
        out[-1] = table["last"]
    return out


def dynamic_hook_spec(hook_plan, sheet) -> SceneSpec:
    """The Dynamic Hook Engine's plan as one self-animated scene: 2-3 teaser beats, then the
    settled hook. Every string any beat or hero draws is declared in `texts` so the content
    scan sees exactly what the viewer sees."""
    beats = []
    beat_strings = []
    for bid in hook_plan.teaser_beats:
        b = sheet.beat(bid)
        if b is None:
            continue
        beats.append({"id": bid, "kind": b.kind.value, "payload": b.payload})
        beat_strings.extend(b.strings)
    tm = hook_plan.timing
    # "Inside: a, b and c." renders as an INSIDE chip over the list (both parts declared here).
    head, sep, body = hook_plan.summary_line.partition(": ")
    label = head.upper() if sep and len(head) <= 18 else ""
    texts = {"eyebrow": hook_plan.eyebrow, "curiosity": hook_plan.curiosity_line,
             "summary": hook_plan.summary_line, "summary_label": label,
             "summary_body": body if label else hook_plan.summary_line,
             "agenda": list(hook_plan.agenda),
             "stamps": hook_stamps(hook_plan.mode.value, hook_plan.archetype.value, len(beats)),
             "beat_strings": list(dict.fromkeys(beat_strings)),
             "hero_strings": list(hook_plan.hero_strings)}
    return SceneSpec(
        kind="DYNAMIC_HOOK", section="HOOK", duration=tm.total, headline=hook_plan.curiosity_line,
        subline=hook_plan.summary_line, texts=texts,
        data={"beats": beats, "hero": {"kind": hook_plan.hero_visual.value,
                                       "payload": hook_plan.hero_payload},
              "timing": {"beat_seconds": tm.beat_seconds, "settle_start": tm.settle_start,
                         "total": tm.total},
              "archetype": hook_plan.archetype.value, "source": hook_plan.source.value},
        dim_background=False, self_animated=True,
        freeze={"t": round(tm.total - 0.3, 3), "what": hook_plan.curiosity_line,
                "where": HOOK_WHERE.get(hook_plan.hero_visual.value, "the hero visual"),
                "why": hook_plan.summary_line, "mode": "DYNAMIC_HOOK"})


# --------------------------------------------------------------------------- de-duplication
def pulse_adds_distinct_fact(pulse: dict | None) -> bool:
    """Does the MARKET PULSE carry a fact the Nifty chart scene does not? The chart already
    shows the close, the change and the structural level. The pulse's own extra is where the
    close sat in the day's range - distinct only when it CONTRADICTS the day's direction (an
    intraday reversal: a down day that closed near the high, an up day near the low)."""
    if not pulse:
        return False
    loc = (pulse.get("support") or {}).get("location")
    return (loc == "HIGH" and not pulse.get("positive")) or (loc == "LOW" and pulse.get("positive"))


def pulse_is_redundant(order, pulse, dynamic_hook: bool) -> bool:
    """Candidate for de-duplication: the hook (planned without the pulse) may state Nifty's move,
    the NIFTY chart scene states it again with richer context, and the pulse adds nothing
    distinct. Confirmed only once the hook actually cites `nifty.move`."""
    return (dynamic_hook and "PULSE" in order and "NIFTY" in order
            and not pulse_adds_distinct_fact(pulse))


# --------------------------------------------------------------------------- entry point
POST_MAX_RUNTIME = 62.0
# Dropped first when a public POST would run past POST_MAX_RUNTIME (never a core section).
POST_TRIM_ORDER = ("GLOBAL", "EVENT", "MOVERS", "FLOWS", "NIFTY", "IPO", "EXCHANGE")


def _report_facts(pres, key):
    """Canonical fact ids behind one POST section (for its gate check and its provenance)."""
    r = getattr(pres, "report", None)
    if r is None:
        return []
    if key in ("PULSE", "NIFTY"):
        return list((r.nifty or {}).get("fact_ids") or [])
    if key == "SECTORS":
        return [s.get("fact_id") for s in (r.sectors or []) if s.get("fact_id")]
    if key == "FLOWS":
        return list((r.institutional_flows or {}).get("fact_ids") or [])
    if key == "GLOBAL":
        return [c.get("fact_id") for c in (r.global_cues or []) if c.get("fact_id")]
    return []


def _admit_section(gate, pres, key, texts, session):
    """Classify one existing POST section (report-backed) and ask the gate.
    Returns (allowed, provenance_texts)."""
    from dataclasses import replace

    from presentation.provenance_label import fmt_date
    from presentation.public_intelligence import section_provenance
    from publication.classification import Scope
    from publication.classify import report_event_fact, report_fact
    r = getattr(pres, "report", None)
    texts = [t for t in texts if t]
    if key == "EVENT":
        ev = next((e for e in (getattr(r, "events", None) or [])
                   if e.get("text") == texts[0]), None)
        f = report_event_fact(ev or {"text": texts[0]}, session)
        ok = True
        for i, t in enumerate(texts):
            ok = gate.admit(replace(f, fact_id=f"{f.fact_id}.{i}", text=t)) and ok
        return ok, {"source": f"SOURCE: {f.source_label.upper()}",
                    "as_of": f"EVENT DATE: {fmt_date(session)}"}
    if r is None:
        return True, None
    ids = _report_facts(pres, key)
    scope = {"SECTORS": Scope.SECTOR, "FLOWS": Scope.MARKET,
             "GLOBAL": Scope.MARKET}.get(key, Scope.INDEX)
    ok = True
    for i, t in enumerate(texts):
        ok = gate.admit(report_fact(r, ids, t, scope, key, f"{key.lower()}.{i}", session)) and ok
    kind = "SESSION" if key in ("PULSE", "NIFTY", "SECTORS") else "DATE"
    return ok, section_provenance(r, ids, session, kind)


def build_storyboard(plan, pres, radar_presentation: dict | None = None,
                     radar_result: dict | None = None, visual_evidence: dict | None = None,
                     universe_label: str = "Nifty 100", sources: dict | None = None,
                     dynamic_hook: bool = True, hook_ai: bool = False, hook_client=None,
                     snapshot=None, profile=None, intelligence=None) -> Storyboard:
    """`dynamic_hook` opens with the Dynamic Hook Engine (teaser beats + settled hook) instead
    of the legacy number card. `hook_ai` lets it ask Gemini (`hook_client`, or the configured
    key when None); it is off by default so nothing here reaches the network unless the caller
    asks - and any Gemini failure falls back to the deterministic hook anyway.

    `profile` (default PUBLIC_UNREGISTERED - `publication.resolve_profile`): every candidate fact
    goes through `publication.PublicationGate` BEFORE a scene is built. Public: no Radar stock
    story, no top-mover ranking, no named security except via an official event; the Market
    Structure / Exchange Watch / IPO Watch sections come from `intelligence`
    (presentation.public_intelligence.PublicIntelligence). PRIVATE_ANALYTICS keeps every Radar
    story exactly as before."""
    from presentation.public_intelligence import plan_public_sections
    from publication import PublicationGate
    from publication.classify import mover_fact, radar_story_fact

    from .public_storyboard import exchange_spec, ipo_spec, structure_spec

    known = dict(getattr(intelligence, "known_securities", None) or {})
    gate = PublicationGate(profile, known)
    session = pres.session_date
    stories = []
    radar_closing = ""
    if radar_presentation and radar_presentation.get("status") == "OK":
        by_inst = {s["instrument"]: s for s in (radar_result or {}).get("stories") or []}
        for sc in radar_presentation.get("scenes") or []:
            if sc.get("role") == "STORY" and sc.get("story"):
                sp = sc["story"]
                if sp["instrument"] in by_inst:
                    stories.append((sp, by_inst[sp["instrument"]]))
            elif sc.get("role") == "CLOSING":
                radar_closing = sc.get("headline") or ""

    # POST freeze: every story's move passes the deterministic move guard before publication.
    # A held story is skipped - the selector's next story moves up - and recorded; the
    # selector's ranking itself is untouched.
    radar_audit = []
    if stories:
        from presentation.radar_guard import guard_radar_stories
        stories, radar_audit = guard_radar_stories(stories, visual_evidence, session,
                                                   (getattr(pres, "m", None) or {}).get("prev_date"))

    # Publication takes the selector's own first N, in its own order - no second selector.
    held = stories[RADAR_PUBLISH_LIMIT:]
    stories = stories[:RADAR_PUBLISH_LIMIT]

    # Publication boundary: a Radar story is our own technical analysis of a named security.
    gated_out, kept = [], []
    for sp, story in stories:
        text = " ".join(x for x in (sp["instrument"], sp.get("headline") or "") if x)
        if gate.admit(radar_story_fact(sp["instrument"], text, session)):
            kept.append((sp, story))
        else:
            gated_out.append(sp["instrument"])
    stories = kept
    if gated_out:
        radar_closing = ""

    # POST editorial plan (Phase 3): a deterministic planner decides which sections earn screen
    # time and what each says; the storyboard only lays them out in the plan's order.
    post = plan_post_sections(pres, plan, stories, universe_label)
    gate_reasons = {}
    if post.movers:
        cards = post.movers["cards"]
        admitted = [gate.admit(mover_fact(c["name"], f"{c['name']} {c['value']}", session))
                    for c in cards]
        if not all(admitted):
            gate_reasons["MOVERS"] = (f"omitted: publication profile {gate.profile.value} - "
                                      "named single-stock moves are a security ranking")
    flows_scene = _plan_scene(plan, "FLOWS")
    section_texts = {
        "PULSE": lambda: [post.pulse["headline"],
                          f"NIFTY 50 {post.pulse['value']} {post.pulse['change']}"],
        "NIFTY": lambda: [post.structure["model"]["takeaway"]],
        "SECTORS": lambda: ([post.sectors["headline"], post.sectors["tone"]]
                            + [f"{r['name']} {r['value']}" for r in post.sectors["rows"]]),
        "FLOWS": lambda: [f"{it.title} {it.value}" for it in
                          (flows_scene.items if flows_scene else [])],
        "GLOBAL": lambda: ([f"{post.global_context['lead']['name']} "
                            f"{post.global_context['lead']['value']}"]
                           + [f"{o['name']} {o['value']}"
                              for o in post.global_context.get("others", [])]),
        "EVENT": lambda: [post.special_event["title"], post.special_event["tag"]],
    }
    provenance = {}
    for key in list(post.order):
        if key in gate_reasons or key not in section_texts:
            continue
        ok, prov = _admit_section(gate, pres, key, section_texts[key](), session)
        provenance[key] = prov
        if not ok:
            gate_reasons[key] = (f"omitted: publication profile {gate.profile.value} refused "
                                 "the section's facts (see publication audit)")
    for key, why in gate_reasons.items():
        if key in post.order:
            post.order.remove(key)
        post.reasons[key] = why

    builders = {"PULSE": lambda: _pulse_scene(post.pulse),
                "NIFTY": lambda: _structure_scene(post.structure),
                "SECTORS": lambda: _sectors_scene(post.sectors),
                "MOVERS": lambda: _movers_scene(post.movers),
                "FLOWS": lambda: _flows(plan),
                "GLOBAL": lambda: _global_scene(post.global_context),
                "EVENT": lambda: _event_scene(post.special_event)}
    main = []
    for k in post.order:
        spec = builders[k]() if k in builders else None
        if spec is None:
            continue
        if provenance.get(k):
            spec.texts["provenance"] = provenance[k]
        main.append(spec)

    # Public intelligence sections: EXCHANGE WATCH, IPO WATCH, UNDER THE SURFACE.
    ps = plan_public_sections(gate, intelligence, session, "POST",
                              nifty_pct=(getattr(pres, "m", None) or {}).get("pct"),
                              include_ipo_listed=True)
    if ps.exchange:
        main.append(exchange_spec(ps.exchange, "POST"))
    if ps.ipo:
        main.append(ipo_spec(ps.ipo))
    main += [structure_spec(ins, lines) for ins, lines in ps.structure]

    # Runtime ceiling - content-driven: nothing is stretched; optional sections go first.
    def _total(ms):
        return sum(x.duration for x in ms) + 2.6 + 5.0
    sections_audit = ps.audit.setdefault("omitted_sections", {})
    for key in POST_TRIM_ORDER:
        if _total(main) <= POST_MAX_RUNTIME:
            break
        for x in [x for x in main if x.section == key]:
            main.remove(x)
            post.reasons[key] = f"omitted: POST runtime ceiling {POST_MAX_RUNTIME:.0f}s"
            if key in post.order:
                post.order.remove(key)
            pub_key = {"EXCHANGE": "EXCHANGE_WATCH", "IPO": "IPO_WATCH"}.get(key)
            if pub_key:
                sections_audit[pub_key] = {"rendered": False, "code": "EDITORIAL_CAP",
                                           "detail": f"runtime ceiling {POST_MAX_RUNTIME:.0f}s"}
    while _total(main) > POST_MAX_RUNTIME and sum(1 for x in main if x.kind == "STRUCTURE") > 1:
        last = [x for x in main if x.kind == "STRUCTURE"][-1]
        main.remove(last)
        ps.omitted.append({"section": "UNDER THE SURFACE", "item": last.data["kind"],
                           "reason": f"runtime ceiling {POST_MAX_RUNTIME:.0f}s"})
    present = list(dict.fromkeys(s.section for s in main)) + (["RADAR"] if stories else [])
    # One fact must not take three consecutive scenes (hook -> pulse -> chart): plan the hook
    # without the pulse when it is a de-duplication candidate, so the hook's summary can never
    # promise a scene that is then dropped.
    dedup = pulse_is_redundant(post.order, post.pulse, dynamic_hook)
    hook_present = [k for k in present if not (dedup and k == "PULSE")]

    hook_record = None
    if dynamic_hook:
        from hooks import plan_hook, post_market_sheet
        from publication.public_hooks import add_structure_facts, restrict_sheet
        sheet = post_market_sheet(plan, pres, stories, visual_evidence, hook_present,
                                  universe_label, snapshot)
        restrict_sheet(sheet, gate)
        shown = {x.data["kind"] for x in main if x.kind == "STRUCTURE"}
        add_structure_facts(sheet, [ins for ins, _ in ps.structure if ins.kind in shown])
        kwargs = {"client": hook_client} if hook_client is not None else {}
        hp = plan_hook(sheet, use_ai=hook_ai, **kwargs)
        hook_scene = dynamic_hook_spec(hp, sheet)
        hook_record = hp.to_dict()
        if dedup and "nifty.move" in hp.fact_ids:
            main = [x for x in main if x.section != "PULSE"]
            post.order.remove("PULSE")
            post.reasons["PULSE"] = (
                "omitted: editorial de-duplication - the hook already states Nifty's move and the "
                "Nifty chart repeats it with richer context (close, the structural level); the "
                "pulse adds no distinct fact (" + ((post.pulse.get("support") or {}).get("text")
                                                   or "no support fact") + " agrees with the move)")
        elif dedup:
            post.reasons["PULSE"] = (post.reasons.get("PULSE", "") + "; kept: the hook does not "
                                     "state Nifty's move")
    else:
        hook_scene = _hook(plan, present, len(stories))
    scenes = [hook_scene] + main
    if stories:
        scenes.append(_radar_intro([sp for sp, _ in stories]))
        for i, (sp, story) in enumerate(stories, start=1):
            scenes.append(_radar_story(i, len(stories), sp, story,
                                       (visual_evidence or {}).get(sp["instrument"])))
    # Radar is the last analytical section; the close follows it directly.
    scenes.append(_closing(plan, radar_closing))

    omitted = [{"section": "MARKET RADAR", "item": a["instrument"],
                "reason": f"move guard: {a['status']} - {a['reason']}",
                "price_change_pct": a.get("price_change_pct")}
               for a in radar_audit if not a["publishable"]]
    omitted += [{"section": "MARKET RADAR", "item": sp["instrument"],
                 "reason": f"publication limit: the POST Short shows the selector's first "
                           f"{RADAR_PUBLISH_LIMIT} Radar stories"} for sp, _ in held]
    omitted += [{"section": "MARKET RADAR", "item": sym,
                 "reason": f"publication profile {gate.profile.value}: our own technical "
                           "analysis of a named security stays PRIVATE (it can only count "
                           "anonymously in UNDER THE SURFACE)"} for sym in gated_out]
    for key in ("PULSE", "NIFTY", "MOVERS", "FLOWS", "GLOBAL", "EVENT"):
        if key not in post.order and post.reasons.get(key, "").startswith("omitted"):
            omitted.append({"section": SECTION_LABELS.get(key, key) or key,
                            "reason": post.reasons.get(key, "")})
    omitted += ps.omitted
    for o in getattr(plan, "omitted", []) or []:
        o = o if isinstance(o, dict) else {"item": str(o)}
        if o.get("scene") == "sectors":
            continue    # the heatmap shows every sector in the report - nothing trimmed
        omitted.append({"section": "editorial", **o})

    post_dict = post.to_dict()
    post_dict["public_reasons"] = ps.reasons
    sb = Storyboard(session_date=session, date_label=session.strftime("%a %d %b %Y").upper(),
                    kicker="SESSION RECAP", scenes=scenes, sources=sources or {},
                    omitted=omitted, hook_plan=hook_record, post_plan=post_dict,
                    radar_guard=radar_audit, publication_profile=gate.profile.value,
                    publication=gate.to_dict(), public_audit=ps.audit, gate=gate)
    # every visible number -> the fact(s) / approved derivation it comes from
    from publication.scene_claims import post_claims, storyboard_scene_texts
    sb.claims = post_claims(sb, getattr(pres, "report", None),
                            sheet if dynamic_hook else None)
    sb.claim_texts = storyboard_scene_texts(sb)
    return sb


__all__ = ["SceneSpec", "Storyboard", "build_storyboard", "choose_mode", "primary_event",
           "RADAR_PUBLISH_LIMIT",
           "dynamic_hook_spec", "EVENT_HEADLINE", "EVENT_CALLOUT", "SECTION_LABELS"]

"""Editorial Planner V3: the public-intelligence sections (UNDER THE SURFACE, MARKET EVENTS,
IPO WATCH, EXCHANGE WATCH) as EditorialCandidates - shared by the POST and PRE planners so both
grade the same story the same way (the edition policy, not the tier, is what differs).

Selection only. Every tier reads the label/count/status the upstream engine ALREADY produced
(Market Structure V2's insight, the Market Events family/status, the IPO chip/board); nothing
here recomputes a market value. Cost estimates mirror `daily_video.public_storyboard`'s scene
durations exactly (a test pins them) - this module never imports the renderer.
"""
from __future__ import annotations

import datetime as dt

from .editorial_arbiter import (BREADTH, CALENDAR, EXCHANGE, IPO, MATERIAL, NOTABLE, OK, PARTIAL,
                                ROUTINE, EditorialCandidate)

# Market Structure V2 index-vs-breadth labels (market_structure.editorial._fifty_two_week reason)
FIFTY_TWO_WEEK_DIVERGENT = ("INDEX_UP_BREADTH_WEAK", "INDEX_DOWN_BREADTH_RESILIENT")
FIFTY_TWO_WEEK_ALIGNED = ("INDEX_AND_BREADTH_ALIGNED_STRONG", "INDEX_AND_BREADTH_ALIGNED_WEAK")
INDEX_TOPIC_PCT = 0.60            # an aligned broad move on a Nifty day this big repeats the pulse
FIFTY_TWO_WEEK_NOTABLE = 10       # 2x FIFTY_TWO_WEEK_MIN, when there is no index read
RANGE_NOTABLE = 20                # 2x RANGE_MIN
DIVERGENCE_MIN_NIFTY_PCT = 0.20   # market_structure.editorial.DIVERGENCE_MIN_NIFTY_PCT

ROUTINE_EVENT_TAGS = frozenset({"GOVT AUCTION"})


# --------------------------------------------------------------------------- costs
def structure_cost(ins) -> float:
    return round(5.4 + 0.25 * len(ins.rows) + (0.6 if ins.takeaway else 0.0)
                 + 0.3 * bool(ins.split), 2)


def cards_cost(model) -> float:
    return round(3.6 + 1.7 * len(model["cards"]), 2)


def ipo_cost(model) -> float:
    if model["layout"] == "CARD":
        return round(4.4 + 0.5 * len(model["card"]["rows"]), 2)
    return round(3.8 + 1.0 * len(model["rows"]), 2)


# --------------------------------------------------------------------------- structure
def _split(ins) -> dict:
    return {s["label"]: int(s["value"]) for s in ins.split if str(s["value"]).isdigit()}


def structure_state(ins, nifty_pct) -> str:
    """The insight's editorial reading, from its own fields: DIVERGENT / ALIGNED / NONE."""
    if ins.kind == "BREADTH":
        sp = _split(ins)
        hi, lo = sp.get("HIGHER", 0), sp.get("LOWER", 0)
        if nifty_pct is None or abs(nifty_pct) < DIVERGENCE_MIN_NIFTY_PCT or hi == lo:
            return "NONE"
        return "DIVERGENT" if (nifty_pct > 0) != (hi > lo) else "ALIGNED"
    if ins.kind == "FIFTY_TWO_WEEK":
        if any(t in ins.reason for t in FIFTY_TWO_WEEK_DIVERGENT):
            return "DIVERGENT"
        if any(t in ins.reason for t in FIFTY_TWO_WEEK_ALIGNED):
            return "ALIGNED"
    return "NONE"


def _lead_count(ins) -> int:
    try:
        return int(str(ins.hero_value).split(" /")[0].split(" of")[0])
    except ValueError:
        return 0


def structure_candidate(ins, nifty_pct, edition: str, as_of: dt.date | None,
                        lines=None) -> EditorialCandidate:
    state = structure_state(ins, nifty_pct)
    n = _lead_count(ins)
    explains = tension = False
    if ins.kind == "BREADTH":
        if state == "DIVERGENT":
            tier, topics, why = MATERIAL, {"breadth_divergence"}, "breadth diverged from the index"
            explains = tension = True
        elif nifty_pct is not None and abs(nifty_pct) >= INDEX_TOPIC_PCT:
            tier, topics = ROUTINE, {"index_direction"}
            why = "a broad move in the same direction as the index - restates the headline"
        else:
            tier, topics = NOTABLE, {"breadth_extreme"}
            why = "a broad move larger than the index's own move suggests"
    elif ins.kind == "FIFTY_TWO_WEEK":
        topics = {"52w_extremes"}
        if state == "DIVERGENT":
            tier, why = MATERIAL, "new 52-week extremes against the index direction"
            explains = tension = True
        elif state == "ALIGNED":
            tier, why = NOTABLE, "new 52-week extremes in the index direction"
        elif n >= FIFTY_TWO_WEEK_NOTABLE:
            tier, why = NOTABLE, f"{n} new 52-week extremes (>= {FIFTY_TWO_WEEK_NOTABLE})"
        else:
            tier, why = ROUTINE, f"{n} new 52-week extremes - a low count"
    elif ins.kind == "UNUSUAL_VOLUME":
        topics = {"unusual_volume"}
        tier, why = ((NOTABLE, "unusual volume concentrated in one sector") if ins.takeaway
                     else (ROUTINE, "unusual volume spread across sectors"))
    else:   # RANGE
        topics = {"range_breaks"}
        tier, why = ((NOTABLE, f"{n} 20-day range breaks (>= {RANGE_NOTABLE})")
                     if n >= RANGE_NOTABLE else (ROUTINE, f"{n} 20-day range breaks"))
    quality = OK if ins.coverage_status == "PUBLISHABLE" else PARTIAL
    return EditorialCandidate(
        candidate_id=f"{edition}.STRUCTURE.{ins.kind}", section="STRUCTURE", family=BREADTH,
        tier=tier, reason=f"{ins.kind}: {why} ({ins.reason})", cost_s=structure_cost(ins),
        quality=quality, explains_headline=explains, tension=tension, topics=frozenset(topics),
        as_of=as_of, payload=(ins, lines))


# --------------------------------------------------------------------------- calendar
def market_events_candidate(model, edition: str, as_of: dt.date | None) -> EditorialCandidate:
    tags = [c["tag"] for c in model["cards"]]
    notable = [t for t in tags if t not in ROUTINE_EVENT_TAGS]
    tier = NOTABLE if notable else ROUTINE
    why = (f"{', '.join(dict.fromkeys(notable))} on a tracked security" if notable else
           "routine market-wide schedule only (" + ", ".join(dict.fromkeys(tags)) + ")")
    keys = model.get("event_keys") or []
    return EditorialCandidate(
        candidate_id=f"{edition}.MARKET_EVENTS", section="MARKET_EVENTS", family=CALENDAR,
        tier=tier, reason=f"{len(tags)} market event(s): {why}", cost_s=cards_cost(model),
        topics=frozenset(f"event:{k}" for k in keys), as_of=as_of, payload=model)


IPO_NOTABLE_CHIPS = {"POST": frozenset({"LISTED"}),
                     "PRE": frozenset({"LISTS TODAY", "OPENS TODAY", "LISTED"})}


def ipo_candidate(model, edition: str, as_of: dt.date | None) -> EditorialCandidate:
    rows = ([{"board": model["card"]["board"], "chip": model["card"]["chip"]}]
            if model["layout"] == "CARD" else model["rows"])
    key = [r for r in rows if r["board"] == "MAINBOARD"
           and r["chip"] in IPO_NOTABLE_CHIPS[edition]]
    tier = NOTABLE if key else ROUTINE
    why = (f"{len(key)} mainboard IPO(s) {key[0]['chip'].lower()}" if key else
           "SME or bidding/allotment milestones only")
    return EditorialCandidate(
        candidate_id=f"{edition}.IPO", section="IPO", family=IPO, tier=tier,
        reason=f"{len(rows)} IPO event(s): {why}", cost_s=ipo_cost(model),
        topics=frozenset({"ipo"}), as_of=as_of, payload=model)


def exchange_candidate(model, edition: str, as_of: dt.date | None) -> EditorialCandidate:
    return EditorialCandidate(
        candidate_id=f"{edition}.EXCHANGE", section="EXCHANGE", family=EXCHANGE, tier=ROUTINE,
        reason=f"{len(model['cards'])} official exchange list change(s) - routine surveillance "
               "/ ban-period updates", cost_s=cards_cost(model),
        topics=frozenset({"exchange_lists"}), as_of=as_of, payload=model)


__all__ = ["structure_candidate", "structure_state", "market_events_candidate", "ipo_candidate",
           "exchange_candidate", "structure_cost", "cards_cost", "ipo_cost",
           "FIFTY_TWO_WEEK_DIVERGENT", "FIFTY_TWO_WEEK_ALIGNED", "ROUTINE_EVENT_TAGS"]

"""Dashboard ATTENTION SET and WHAT CHANGED TODAY - deterministic, from existing fields only.

The Dashboard answers "what deserves my attention first?"; the Radar page stays the complete
candidate list. Nothing here computes a number to rank by: candidates are placed into named
TIERS by facts the Radar already recorded, ordered tier by tier (lexicographic), and the list is
cut at `ATTENTION_MAX`. Every shown candidate carries the labels that put it there.

Attention rule (docs/PRIVATE_DESK_USER_GUIDE.md "Dashboard attention set"):

  Tier 1  STORY SELECTED       chosen by the Radar's own editorial selector
                               (in the selector's own story order)
  Tier 2  3 EVIDENCE FAMILIES  HIGH_INTEREST: volume + structure + relative performance
  Tier 3  VOLUME + STRUCTURE   unusual volume on the same session as a structure event
  Tier 4  NEW                  Radar novelty NEW_CANDIDATE - first recorded appearance first,
                               then reappearances by longest absence (sessions since prior)
  Tier 5  STATE CHANGE         any other novelty except CONTINUATION - used ONLY to fill the set
                               up to `ATTENTION_MIN` on a quiet day

  Unchanged continuations enter only through tiers 1-3. Inside a tier (after the tier's own
  stated order) the tie-breaker is the existing Radar order: HIGH_INTEREST before NOTABLE,
  then symbol. Tiers 1-4 fill up to `ATTENTION_MAX`; the rest stay on the Radar page.

Uses only candidate metadata the dashboard already loaded (no replay, no extra query).
"""
from __future__ import annotations

from . import candidates as cs

ATTENTION_MAX = 10
ATTENTION_MIN = 8

STORY, HIGH, VOL_STRUCT, NEW, CHANGE = 1, 2, 3, 4, 5
TIER_LABELS = {STORY: "STORY SELECTED", HIGH: "3 EVIDENCE FAMILIES",
               VOL_STRUCT: "VOLUME + STRUCTURE", NEW: "NEW", CHANGE: "STATE CHANGE"}
TIER_RULES = {
    STORY: "chosen by the Radar's editorial selector (selector order)",
    HIGH: "all three evidence families active (HIGH_INTEREST)",
    VOL_STRUCT: "unusual volume and a structure event on the same session",
    NEW: "Radar novelty NEW_CANDIDATE: first recorded first, then longest absence",
    CHANGE: "other Radar state change - fills a quiet day up to the minimum",
}


def _tier(v: dict) -> int | None:
    fams = set(v["families"])
    if v["selected"]:
        return STORY
    if v["signal_count"] >= 3:
        return HIGH
    if {"VOLUME", "STRUCTURE"} <= fams:
        return VOL_STRUCT
    if v["novelty_type"] == "NEW_CANDIDATE":
        return NEW
    if v["novelty_type"] and v["novelty_type"] != "CONTINUATION":
        return CHANGE
    return None


def labels(v: dict, tier: int | None = None) -> list:
    """Every tier condition a candidate meets (not just its own tier) - shown next to it. The
    Radar novelty is repeated only for a STATE CHANGE filler (its novelty column says the rest)."""
    out = []
    if v["selected"]:
        out.append("STORY SELECTED")
    if v["signal_count"] >= 3:
        out.append("3 EVIDENCE FAMILIES")
    if {"VOLUME", "STRUCTURE"} <= set(v["families"]):
        out.append("VOLUME + STRUCTURE")
    if v["novelty_type"] == "NEW_CANDIDATE":
        if v["appearance"] == cs.FIRST_RECORDED:
            out.append("NEW · FIRST RECORDED")
        elif v["sessions_since_prior"]:
            out.append(f"NEW · BACK AFTER {v['sessions_since_prior']} SESSIONS")
        else:
            out.append("NEW")
    elif tier == CHANGE:
        out.append("STATE CHANGE · " + v["novelty_type"].replace("_", " "))
    return out


def attention_set(views: list, story_order: list) -> list:
    """`[{"view", "tier", "labels"}]`. `views` must be in the existing Radar order."""
    recorded = {v["symbol"]: i for i, v in enumerate(views)}
    story_pos = {s: i for i, s in enumerate(story_order)}

    def order(v, tier):
        if tier == STORY:
            inside = (story_pos.get(v["symbol"], len(story_pos)),)
        elif tier == NEW:
            inside = (0 if v["appearance"] == cs.FIRST_RECORDED else 1,
                      -(v["sessions_since_prior"] or 0))
        else:
            inside = ()
        return (tier, *inside, recorded[v["symbol"]])

    tiered = [(v, _tier(v)) for v in views]
    main = sorted(((v, t) for v, t in tiered if t is not None and t < CHANGE),
                  key=lambda p: order(*p))
    chosen = main[:ATTENTION_MAX]
    if len(chosen) < ATTENTION_MIN:
        filler = sorted(((v, t) for v, t in tiered if t == CHANGE), key=lambda p: order(*p))
        chosen += filler[:ATTENTION_MIN - len(chosen)]
    return [{"view": v, "tier": t, "labels": labels(v, t)} for v, t in chosen]


def radar_default_order(views: list) -> list:
    """Radar page default order (UI only; the stored Radar output is unchanged):
    NEW_CANDIDATE, then other state changes, then unchanged continuations; inside each group the
    existing Radar order (HIGH_INTEREST first, then symbol)."""
    def group(v):
        n = v["novelty_type"]
        return 0 if n == "NEW_CANDIDATE" else 2 if n in (None, "CONTINUATION") else 1
    recorded = {v["symbol"]: i for i, v in enumerate(views)}
    return sorted(views, key=lambda v: (group(v), recorded[v["symbol"]]))


# ------------------------------------------------------------------ what changed today
def change_summary(views: list, changes: dict) -> dict:
    """Counts straight from the candidate states. NEW + STATE CHANGE + UNCHANGED = candidates."""
    new = [v for v in views if v["novelty_type"] == "NEW_CANDIDATE"]
    unchanged = [v for v in views if v["novelty_type"] in (None, "CONTINUATION")]
    changed = [v for v in views if v["novelty_type"] not in (None, "CONTINUATION", "NEW_CANDIDATE")]
    structure = {v["symbol"] for k in ("RANGE_UP", "RANGE_DOWN", "SMA_CROSS") for v in changes[k]}
    rows = [
        ("NEW", "New", len(new), "Radar novelty NEW_CANDIDATE", "NEW"),
        ("REAPPEARED", "Reappeared", len(changes["REAPPEARED"]),
         "earlier recorded appearance, then a gap of at least one session", "REAPPEARED"),
        ("CHANGED", "State change", len(changed),
         "candidate before and something changed (family, event, persistence, direction, "
         "attention)", "CHANGED"),
        ("STRUCTURE", "New structure event", len(structure),
         "new range break or SMA cross versus its prior appearance (or on a new candidate)",
         "NEW_STRUCTURE"),
        ("VOLUME", "New unusual volume", len(changes["NEW_UNUSUAL_VOLUME"]),
         "VOLUME family newly active", "NEW_VOLUME"),
        ("UNCHANGED", "Continuing, unchanged", len(unchanged),
         "same recorded state as its prior appearance", "CONTINUATION"),
        ("LOST", "No longer a candidate", len(changes["NO_LONGER_CANDIDATE"]),
         "candidate on the previous session, not now", None),
    ]
    return {"rows": [{"key": k, "label": l, "count": n, "rule": r, "chip": c}
                     for k, l, n, r, c in rows],
            "total": len(views), "new": len(new), "changed": len(changed),
            "unchanged": len(unchanged), "structure_symbols": structure,
            "volume_symbols": {v["symbol"] for v in changes["NEW_UNUSUAL_VOLUME"]}}


def _event_line(code: str, v: dict) -> str:
    item = next((i for i in v["why"] if i["code"] == f"STRUCTURE_{code}"), None)
    if item and item.get("verified") and item.get("values", {}).get("break_distance_pct") is not None:
        val = item["values"]
        word = "above" if "ABOVE" in code else "below"
        bound = "high" if "ABOVE" in code else "low"
        return (f"Close {abs(val['break_distance_pct']):.1f}% {word} prior "
                f"{val.get('window_sessions')}-session {bound}")
    if code.startswith(("CROSS_ABOVE_", "CROSS_BELOW_")):
        word = "above" if "ABOVE" in code else "below"
        n = code[-2:]
        return f"Close crossed {word} its {n}-session average (SMA{n})"
    word, bound = ("above", "high") if "ABOVE" in code else ("below", "low")
    n = "20" if "20D" in code else "50"
    return f"Close {word} prior {n}-session {bound}"


def change_item(v: dict, history_start) -> dict:
    """One factual change item: symbol, change type, up to three short lines from existing facts."""
    kinds, lines = [], []
    novelty = v["novelty_type"]
    if novelty == "NEW_CANDIDATE":
        if v["appearance"] == cs.FIRST_RECORDED:
            kinds.append("NEW")
            lines.append(f"First appearance in recorded Radar history (since {history_start})")
        elif v["prior_appearance"]:
            kinds.append("REAPPEARED")
            lines.append(f"Back on Radar after {v['sessions_since_prior']} sessions "
                         f"(last {v['prior_appearance']})")
        else:
            kinds.append("NEW")
        new_families = list(v["families"])
        new_events = [c[len("STRUCTURE_"):] for c in v["reason_codes"] if c.startswith("STRUCTURE_")]
    else:
        kinds.append((novelty or "").replace("_", " "))
        new_families, new_events = v["new_families"], v["new_technical_events"]
    if "ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST" in v["novelty_codes"]:
        lines.append("Attention NOTABLE → HIGH INTEREST")
    if "VOLUME" in new_families:
        kinds.append("UNUSUAL VOLUME")
        if v["relative_volume"] is not None:
            lines.append(f"Relative volume {v['relative_volume']:.1f}× vs prior-20-session average")
        else:
            lines.append("Unusual-volume evidence active (values unavailable)")
    if new_events:
        kinds.append("STRUCTURE")
        lines.append(_event_line(new_events[0], v))
    for code in v["novelty_codes"]:
        if code.startswith("PERSISTENCE_TRANSITION_"):
            a, _, b = code[len("PERSISTENCE_TRANSITION_"):].partition("_TO_")
            lines.append(f"Relative persistence {a.replace('_', ' ')} → {b.replace('_', ' ')}")
    return {"symbol": v["symbol"], "kinds": [k for k in dict.fromkeys(kinds) if k],
            "lines": lines[:3], "day_pct": v["price_change_pct"], "view": v}


def change_items(attention: list, views: list, history_start, limit: int = 8) -> list:
    """Most relevant stock-level changes: attention-set members first (in attention order), then
    the other changed candidates in the Radar default order; continuations are not changes."""
    seen, out = set(), []
    ordered = [a["view"] for a in attention] + radar_default_order(views)
    for v in ordered:
        if v["symbol"] in seen or v["novelty_type"] in (None, "CONTINUATION"):
            continue
        seen.add(v["symbol"])
        out.append(change_item(v, history_start))
        if len(out) == limit:
            break
    return out


__all__ = ["attention_set", "labels", "radar_default_order", "change_summary", "change_items",
          "change_item", "ATTENTION_MAX", "ATTENTION_MIN", "TIER_LABELS", "TIER_RULES"]

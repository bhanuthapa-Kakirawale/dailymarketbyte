"""Builds the Short's script from the canonical report and the intelligence snapshot.

Reads both, mutates neither. Every scene carries the fact and insight ids behind it, so a
number on screen can always be walked back to the validated record that produced it.

Two rules shape every scene:

* **One primary message.** Each scene names one dominant number and at most a short
  supporting line. Supporting rows are capped hard.
* **Omission is the default.** A valid fact does not earn screen time; the canonical report
  keeps everything, and what does not fit the reading budget is recorded in `omitted` rather
  than squeezed in smaller.
"""
from __future__ import annotations

import datetime as dt

from core import Metric

from . import hook as hook_module
from .config import (MAX_CONTEXT_INSIGHTS, MAX_EVENTS, MAX_GLOBAL_CUES, MAX_HEATMAP_CARDS,
                     MAX_MOVERS_DISPLAYED, MAX_RANKED_MOVERS, MAX_SECTORS_HIGHLIGHTED,
                     MIN_SECTORS_FOR_HEATMAP, RANK_NOTABLE_FRACTION, TICKER_SCENES,
                     min_items_for)
from .models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from .movers_gate import movers_coverage_verdict, row_publishable
from .readability import apply_timing, trim_to_fit

# Which global cues matter most to an Indian pre-market viewer, in order. A fixed preference
# beats "whichever moved most": the overnight US close and the GIFT indication are what the
# session is read against, however quiet they were.
GLOBAL_CUE_PREFERENCE = ("GIFT NIFTY", "DOW JONES", "NASDAQ", "BRENT CRUDE",
                         "USD / INR", "GOLD")


def _rupees(value: float) -> str:
    return f"Rs {abs(value):,.0f} cr"


def _fact_id(report, metric, instrument=None):
    for fact in report.facts_for(metric):
        if instrument is None or fact.instrument == instrument:
            return fact.fact_id
    return None


def _displayable(snapshot, used_ids):
    if snapshot is None:
        return []
    return [i for i in snapshot.selected(limit=6) if i.insight_id not in used_ids]


# --------------------------------------------------------------------- scenes
def _hook_scene(report, snapshot, is_safe, admit=None) -> tuple:
    chosen = hook_module.select(report, snapshot, is_safe=is_safe, admit=admit)
    scene = ScenePlan(
        scene_id="hook", scene_type=SceneType.HOOK,
        primary_text=chosen.primary_text, primary_value=chosen.primary_value,
        primary_numeric=chosen.primary_numeric, primary_positive=chosen.primary_positive,
        secondary_text=chosen.secondary_text,
        show_ticker="HOOK" in TICKER_SCENES,
        source_fact_ids=list(chosen.source_fact_ids),
        source_insight_ids=list(chosen.source_insight_ids),
        metadata={"candidate_id": chosen.candidate_id, "score": round(chosen.score, 4)})
    return apply_timing(scene), chosen


def _global_scene(report) -> ScenePlan | None:
    """Up to `MAX_GLOBAL_CUES` cues, chosen by relevance order, in a compact scan grid.

    A retail viewer takes in a small grid of name+percentage cells in one glance rather than
    reading them as sequential narrative cards, so this is a scan presentation
    (`metadata["presentation"] = "GLOBAL_SCAN"`) - the same reasoning as the sector heatmap
    and the ranked-mover lists, just at a much smaller scale.
    """
    cues = {c.get("label"): c for c in report.global_cues or []
            if c.get("value") is not None and c.get("change_pct") is not None}
    ordered = [cues[label] for label in GLOBAL_CUE_PREFERENCE if label in cues]
    ordered += [c for label, c in sorted(cues.items()) if label not in GLOBAL_CUE_PREFERENCE]
    if len(ordered) < 2:
        return None

    scene = ScenePlan(scene_id="global", scene_type=SceneType.GLOBAL,
                      primary_text="GLOBAL CUES", secondary_text="Overnight, before the open",
                      show_ticker="GLOBAL" in TICKER_SCENES,
                      metadata={"presentation": "GLOBAL_SCAN"})
    for cue in ordered[:MAX_GLOBAL_CUES]:
        pct = float(cue["change_pct"])
        scene.items.append(EditorialItem(
            title=cue["label"], value=f"{pct:+.2f}%", numeric=pct, positive=pct >= 0,
            source_fact_ids=[cue["fact_id"]] if cue.get("fact_id") else []))
    scene.source_fact_ids = [fid for i in scene.items for fid in i.source_fact_ids]
    return apply_timing(scene)


def _nifty_scene(report, snapshot, used_insights) -> ScenePlan | None:
    """The index, reduced to one number and one observation. The chart stays; the terminal
    readout of DMAs, RSI, pivots and levels does not - all of it remains in the report."""
    nifty = report.nifty or {}
    pct = nifty.get("change_pct")
    if pct is None:
        return None

    secondary, insight_ids = "", []
    move = next((i for i in (snapshot.insights if snapshot else [])
                 if i.insight_id == "index-move-20" and i.is_displayable), None)
    ranked = _rank_line(move, "the last 20 sessions") if move else ""
    if ranked and move.insight_id not in used_insights:
        secondary = ranked
        insight_ids = [move.insight_id]
    else:
        technicals = report.technicals or {}
        close, ema20 = nifty.get("close"), technicals.get("ema20")
        if close is not None and ema20 is not None:
            secondary = f"Closed {'above' if close >= ema20 else 'below'} its 20-day average"
        elif nifty.get("india_vix") is not None:
            secondary = f"India VIX at {float(nifty['india_vix']):.1f}"

    scene = ScenePlan(
        scene_id="nifty", scene_type=SceneType.NIFTY,
        primary_text="NIFTY 50", primary_value=f"{float(pct):+.2f}%",
        primary_numeric=float(pct), primary_positive=float(pct) >= 0,
        secondary_text=secondary, has_chart=True,
        show_ticker="NIFTY" in TICKER_SCENES,
        source_fact_ids=[f for f in (nifty.get("fact_ids") or [])],
        source_insight_ids=insight_ids)
    return apply_timing(scene)


def _flows_scene(report, snapshot, used_insights) -> ScenePlan | None:
    """Two highly scannable panels (FII net, DII net) - a scan presentation, priced with
    `estimate_flows_scene` rather than the heavier narrative per-card cost, since a label, a
    subject and one number each register in a glance rather than being read in sequence."""
    flows = report.institutional_flows or {}
    fii, dii = flows.get("fii_net_cash_cr"), flows.get("dii_net_cash_cr")
    if fii is None or dii is None:
        return None

    scene = ScenePlan(scene_id="flows", scene_type=SceneType.FLOWS,
                      primary_text="FII / DII FLOWS",
                      show_ticker="FLOWS" in TICKER_SCENES,
                      source_fact_ids=list(flows.get("fact_ids") or []),
                      metadata={"presentation": "FLOWS_SCAN"})
    for label, value, metric in (("FII", float(fii), Metric.FII_NET_CASH),
                                 ("DII", float(dii), Metric.DII_NET_CASH)):
        fact_id = _fact_id(report, metric)
        scene.items.append(EditorialItem(
            title=label, value=f"{'+' if value >= 0 else '-'}{_rupees(value)}",
            label="NET BUYERS" if value >= 0 else "NET SELLERS",
            numeric=value, positive=value >= 0,
            source_fact_ids=[fact_id] if fact_id else []))

    streak = next((i for i in (snapshot.insights if snapshot else [])
                   if i.insight_id in ("fii-flow-streak", "dii-flow-streak")
                   and i.is_displayable and i.insight_id not in used_insights), None)
    if streak:
        sessions = streak.metadata.get("streak_sessions")
        direction = "sellers" if streak.metadata.get("direction") == "SELLING" else "buyers"
        scene.secondary_text = f"{streak.subject}s net {direction}, {sessions} recorded sessions"
        scene.source_insight_ids = [streak.insight_id]
    return apply_timing(scene)


def _sectors_scene(report) -> ScenePlan | None:
    """Strongest/weakest in NARRATIVE mode; a scannable heatmap grid once there are enough
    sectors for a grid to be worth it (`MIN_SECTORS_FOR_HEATMAP`).

    Reducing sector breadth to only the two extremes is accurate but throws away information
    a retail viewer can otherwise take in at a glance - a heatmap trades reading-one-at-a-time
    for scanning-all-at-once instead of trading it away. Below the threshold a grid would be
    an awkward 3+1 or 3+2, so the narrative comparison - which reads better at that size -
    stays the default.
    """
    available = len(report.sectors or [])
    sectors = [s for s in report.sectors or [] if s.get("change_pct") is not None]
    eligible = len(sectors)
    if eligible < 2:
        return None
    ordered = sorted(sectors, key=lambda s: (-float(s["change_pct"]), s.get("name") or ""))

    if len(ordered) >= MIN_SECTORS_FOR_HEATMAP:
        return _sectors_heatmap_scene(ordered, available, eligible)

    # Strongest and weakest first, the runner-up last: `trim_to_fit` drops from the end, so
    # a tight reading budget must cost the optional third sector, never the weakest one the
    # scene exists to contrast against.
    picked = [ordered[0], ordered[-1]]
    if len(ordered) > 2 and MAX_SECTORS_HIGHLIGHTED >= 3:
        picked.append(ordered[1])

    scene = ScenePlan(scene_id="sectors", scene_type=SceneType.SECTORS,
                      primary_text="SECTOR CHECK",
                      secondary_text="Strongest and weakest of the session",
                      show_ticker="SECTORS" in TICKER_SCENES,
                      metadata={
                          "presentation": "NARRATIVE",
                          "sectors_available": available, "sectors_eligible": eligible,
                          "fallback_reason": (
                              f"only {eligible} eligible sector(s), below "
                              f"MIN_SECTORS_FOR_HEATMAP ({MIN_SECTORS_FOR_HEATMAP})"),
                      })
    for sector in picked[:MAX_SECTORS_HIGHLIGHTED]:
        pct = float(sector["change_pct"])
        scene.items.append(EditorialItem(
            title=str(sector.get("name") or ""), value=f"{pct:+.2f}%",
            numeric=pct, positive=pct >= 0,
            source_fact_ids=[sector["fact_id"]] if sector.get("fact_id") else []))
    scene.source_fact_ids = [fid for i in scene.items for fid in i.source_fact_ids]
    scene.metadata["sectors_displayed"] = len(scene.items)
    scene.metadata["sectors_omitted"] = max(0, eligible - len(scene.items))
    return apply_timing(scene)


def _sectors_heatmap_scene(ordered: list, available: int, eligible: int) -> ScenePlan:
    """A scan-oriented grid: every sector the reading budget can fit (up to
    `MAX_HEATMAP_CARDS`), strongest to weakest, so the eye can scan top-to-bottom the same
    way it would read a narrative list - just all at once instead of one row at a time.

    Text density stays low - a name and a signed percentage, nothing else - while data
    density is high: this is the one scene in the Short designed to be scanned rather than
    read serially, which `ScenePlan.metadata["presentation"] = "HEATMAP"` tells both the
    reading-time estimator and readability QA's card-count gate not to price like the rest.
    """
    cells = ordered[:MAX_HEATMAP_CARDS]
    higher = sum(1 for s in cells if float(s["change_pct"]) > 0)
    lower = sum(1 for s in cells if float(s["change_pct"]) < 0)

    scene = ScenePlan(scene_id="sectors", scene_type=SceneType.SECTORS,
                      primary_text="SECTOR HEATMAP",
                      secondary_text=f"{higher} higher • {lower} lower",
                      show_ticker="SECTORS" in TICKER_SCENES,
                      metadata={
                          "presentation": "HEATMAP",
                          "sectors_available": available, "sectors_eligible": eligible,
                          "fallback_reason": "",
                      })
    for sector in cells:
        pct = float(sector["change_pct"])
        scene.items.append(EditorialItem(
            title=str(sector.get("name") or ""), value=f"{pct:+.2f}%",
            numeric=pct, positive=pct >= 0,
            source_fact_ids=[sector["fact_id"]] if sector.get("fact_id") else []))
    scene.source_fact_ids = [fid for i in scene.items for fid in i.source_fact_ids]
    scene.metadata["sectors_displayed"] = len(scene.items)
    scene.metadata["sectors_omitted"] = max(0, eligible - len(scene.items))
    return apply_timing(scene)


def _mover_display_score(row) -> tuple:
    """Editorial DISPLAY score - how much screen time a move has earned, nothing more.

    It is not a ranking of the stock, a quality measure, or anything a viewer should act on.
    Size of move dominates; unusual volume and a real catalyst break ties, because both make
    the move easier to explain in one line.
    """
    pct = abs(float(row.get("change_pct") or 0))
    volume = float(row.get("relative_volume") or 0)
    catalyst = (row.get("catalyst") or {}).get("type") == "POSSIBLE_CATALYST"
    return (round(pct, 4), round(min(volume, 10.0), 4), 1 if catalyst else 0,
            row.get("symbol") or "")


def _movers_scene(report) -> ScenePlan | None:
    """The day's best and worst, not a leaderboard of one direction.

    Kept as an internal fallback builder only - `plan_short` no longer calls this for the
    normal Short, which shows `gainers_scene`/`losers_scene` (two ranked scan lists) instead.
    """
    gainers = sorted([r for r in report.gainers or [] if r.get("change_pct") is not None],
                     key=_mover_display_score, reverse=True)
    losers = sorted([r for r in report.losers or [] if r.get("change_pct") is not None],
                    key=_mover_display_score, reverse=True)
    if not gainers and not losers:
        return None

    # Best gainer and worst loser first, the runner-up last. Ranking the pool purely by size
    # of move let a strong session fill the scene with three gainers, and `trim_to_fit` drops
    # from the end - so a tight reading budget must cost the third row, never the only row
    # showing the other side of the session.
    rows = [side[0] for side in (gainers, losers) if side]
    rest = sorted(gainers[1:] + losers[1:], key=_mover_display_score, reverse=True)
    rows.extend(rest)

    scene = ScenePlan(scene_id="movers", scene_type=SceneType.MOVERS,
                      primary_text="BIGGEST MOVES",
                      show_ticker="MOVERS" in TICKER_SCENES)
    for row in rows[:MAX_MOVERS_DISPLAYED]:
        pct = float(row["change_pct"])
        catalyst = row.get("catalyst") or {}
        note = ""
        if catalyst.get("type") == "POSSIBLE_CATALYST" and catalyst.get("text"):
            # Non-causal by construction: the catalyst text is shown as something that was
            # reported alongside the move, never as the reason the move happened.
            note = _short_note(catalyst["text"], max_words=5)
        scene.items.append(EditorialItem(
            title=str(row.get("symbol") or ""), value=f"{pct:+.2f}%", note=note,
            numeric=pct, positive=pct >= 0,
            source_fact_ids=list(row.get("fact_ids") or [])))
    scene.source_fact_ids = [fid for i in scene.items for fid in i.source_fact_ids]
    return apply_timing(scene)


def _ranked_scan_scene(rows: list, scene_type: SceneType, primary_text: str) -> ScenePlan | None:
    """Shared builder behind `gainers_scene`/`losers_scene`: a reusable ranked-mover scan
    list, not a narrative card list. Up to `MAX_RANKED_MOVERS` rows, already ordered by the
    caller, each reduced to rank + symbol + signed percentage - no catalyst text, so the list
    stays fast to scan regardless of how many rows it holds. `metadata["presentation"] =
    "SCAN"` tells both the reading-time estimator and readability QA's card-count gate to
    price this the way the heatmap is priced: registered by a scanning eye, not read card by
    card.
    """
    rows = rows[:MAX_RANKED_MOVERS]
    if not rows:
        return None
    scene = ScenePlan(scene_id=scene_type.value.lower(), scene_type=scene_type,
                      primary_text=primary_text,
                      show_ticker=scene_type.value in TICKER_SCENES,
                      metadata={"presentation": "SCAN"})
    for index, row in enumerate(rows, start=1):
        pct = float(row["change_pct"])
        scene.items.append(EditorialItem(
            title=str(row.get("symbol") or ""), value=f"{pct:+.2f}%", rank=index,
            numeric=pct, positive=pct >= 0,
            source_fact_ids=list(row.get("fact_ids") or [])))
    scene.source_fact_ids = [fid for i in scene.items for fid in i.source_fact_ids]
    # The bar for each row is scaled against the largest move actually shown in THIS scene,
    # not a fixed constant - deterministic and reproducible from the same items already on
    # the scene, never a new value the report didn't already validate.
    scene.metadata["scan_max_abs_pct"] = round(max(abs(i.numeric) for i in scene.items), 4)
    return apply_timing(scene)


def gainers_scene(report) -> ScenePlan | None:
    """Top 5 Gainers scan scene: largest positive move first, down to the smallest positive
    move that still made the cut. Zero and invalid (missing `change_pct`) rows never appear;
    fewer than 5 real gainers means fewer than 5 rows, never invented ones."""
    rows = [r for r in report.gainers or []
           if r.get("change_pct") is not None and float(r["change_pct"]) > 0
           and row_publishable(r)]
    ordered = sorted(rows, key=lambda r: (-float(r["change_pct"]), r.get("symbol") or ""))
    return _ranked_scan_scene(ordered, SceneType.GAINERS, "TOP 5 GAINERS")


def losers_scene(report) -> ScenePlan | None:
    """Top 5 Losers scan scene: largest absolute negative move first, down to the smaller
    (still negative) moves that made the cut. Mirrors `gainers_scene`'s rules exactly."""
    rows = [r for r in report.losers or []
           if r.get("change_pct") is not None and float(r["change_pct"]) < 0
           and row_publishable(r)]
    ordered = sorted(rows, key=lambda r: (float(r["change_pct"]), r.get("symbol") or ""))
    return _ranked_scan_scene(ordered, SceneType.LOSERS, "TOP 5 LOSERS")


def _short_note(text: str, max_words: int = 6) -> str:
    """Trim a catalyst to a glanceable fragment, on a word boundary and without inventing
    causality. Returns "" rather than a fragment that would read as a broken claim."""
    words = str(text).split()
    if not words:
        return ""
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",;:-") + "..."


def _rank_line(insight, window: str) -> str:
    """Where today's index move sits in its window - said only when that is worth saying.

    A rank is informative at the ends and empty in the middle. "Bigger move than 0 of the
    last 20 sessions" is accurate, tells the viewer nothing, and reads on screen like a
    failed lookup; a mid-pack rank is no better. Both extremes are genuinely notable, so
    each gets stated in its own direction and everything between returns "" for the caller
    to replace with a line that does carry information.
    """
    larger = insight.metadata.get("larger_than_sessions")
    lookback = insight.lookback_sessions or 0
    if larger is None or not lookback:
        return ""
    quieter = lookback - larger
    if larger / lookback >= RANK_NOTABLE_FRACTION:
        return f"Bigger move than {larger} of {window}"
    if quieter / lookback >= RANK_NOTABLE_FRACTION:
        return f"Smaller move than {quieter} of {window}"
    return ""


def context_line(insight) -> str:
    """A card-sized rendering of an insight, built from its own recorded metadata.

    The intelligence statement is written for the artifact - "Over the last 20 available
    sessions DIIs were net buyers in 20 of them, with a cumulative net flow of Rs 5,195
    crore" is precise and unreadable on a phone. This composes the same numbers into a
    glanceable line from the insight's structured metadata, so nothing is invented and the
    calculation is untouched; only the wording is chosen for the screen.
    """
    meta, insight_id = insight.metadata or {}, insight.insight_id

    if insight_id.startswith("index-move"):
        return _rank_line(insight, f"{insight.lookback_sessions} sessions")
    if insight_id.startswith("vix-rank"):
        return (f"Higher than {meta.get('higher_than_sessions')} of "
                f"{insight.lookback_sessions} readings")
    if insight_id.startswith("vix-mean"):
        above = (insight.current_value or 0) > (insight.comparison_value or 0)
        return f"{'Above' if above else 'Below'} its {insight.lookback_sessions}-session average"
    if insight_id.endswith("flow-streak"):
        word = "Net sellers" if meta.get("direction") == "SELLING" else "Net buyers"
        return f"{word}, {meta.get('streak_sessions')} recorded sessions"
    if "flow-cumulative" in insight_id:
        # The sign carries the whole meaning, and a bare "Rs 7,045 cr over 20 sessions" next
        # to a "DII" chip leaves the viewer to infer which way it went.
        total = meta.get("cumulative_flow") or 0
        word = "Net buying" if total >= 0 else "Net selling"
        return f"{word} Rs {abs(total):,.0f} cr over {insight.lookback_sessions} sessions"
    if insight_id.startswith("sector-streak"):
        word = "Up" if meta.get("direction") == "UP" else "Down"
        return f"{word} {meta.get('streak_sessions')} recorded sessions"
    if insight_id.startswith("mover-recurrence"):
        return (f"{meta.get('prior_appearances')} appearances in "
                f"{insight.lookback_sessions} sessions")
    if insight_id.startswith("relative-volume"):
        return (f"Higher than {meta.get('higher_than_readings')} of "
                f"{meta.get('comparable_sample')} readings")
    return _short_note(insight.statement, max_words=8)


def _context_scene(snapshot, used_insights, admit=None) -> ScenePlan | None:
    # An insight whose short form comes back empty has nothing card-sized to say - an index
    # move sitting mid-window, for instance. It stays in the snapshot and leaves the screen.
    lines = [(i, context_line(i)) for i in _displayable(snapshot, used_insights)]
    lines = [(i, line) for i, line in lines if line and (admit is None or admit(i, line))]
    lines = lines[:MAX_CONTEXT_INSIGHTS]
    if not lines:
        return None
    insights = [i for i, _ in lines]
    scene = ScenePlan(scene_id="context", scene_type=SceneType.CONTEXT,
                      primary_text="MARKET CONTEXT",
                      secondary_text="Versus recent recorded sessions",
                      show_ticker="CONTEXT" in TICKER_SCENES,
                      source_insight_ids=[i.insight_id for i in insights])
    for insight, line in lines:
        scene.items.append(EditorialItem(
            title=line, label=_context_label(insight),
            source_insight_ids=[insight.insight_id],
            source_fact_ids=list(insight.supporting_fact_ids[:1])))
    return apply_timing(scene)


_FIXED_CONTEXT_LABELS = {"INDEX_MOVE": "NIFTY", "VOLATILITY": "VIX"}


def _context_label(insight) -> str:
    fixed = _FIXED_CONTEXT_LABELS.get(insight.category.value)
    return fixed if fixed else (insight.subject or "CONTEXT").upper()[:12]


def _events_scene(report, admit=None) -> ScenePlan | None:
    events = [e for e in report.events or [] if e.get("text") and (admit is None or admit(e))]
    if not events:
        return None
    scene = ScenePlan(scene_id="events", scene_type=SceneType.EVENTS,
                      primary_text="WATCH NEXT",
                      show_ticker="EVENTS" in TICKER_SCENES)
    for event in events[:MAX_EVENTS]:
        scene.items.append(EditorialItem(title=str(event.get("text")),
                                         label=str(event.get("tag") or "")[:10]))
    return apply_timing(scene)


def _outro_scene() -> ScenePlan:
    scene = ScenePlan(scene_id="outro", scene_type=SceneType.OUTRO,
                      primary_text="SUBSCRIBE",
                      secondary_text="Every trading day",
                      show_ticker="OUTRO" in TICKER_SCENES)
    return apply_timing(scene)


# --------------------------------------------------------------------- plan
def plan_short(report, snapshot=None, now: dt.datetime | None = None,
               is_safe=None, profile=None) -> ShortsPlan:
    """Assemble the Short. Reads the report and the snapshot; writes to neither.

    `profile` (default PUBLIC_UNREGISTERED): the publication gate decides what may appear - a
    single named stock, a top-gainer/loser ranking, a stock-level history insight or a
    news/AI "event" is refused publicly and recorded in `omitted`; PRIVATE_ANALYTICS keeps them."""
    from publication import PublicationGate
    from publication.classify import insight_fact, mover_fact, report_event_fact

    plan = ShortsPlan(report_id=report.report_id,
                      session_date=report.session_date or report.report_date,
                      generated_at=now)
    pub = PublicationGate(profile)
    plan.publication_profile, plan.gate = pub.profile.value, pub
    session = plan.session_date

    def admit_hook(c):
        if c.candidate_id == "hook-mover":
            return pub.admit(mover_fact(c.primary_text, f"{c.primary_text} {c.primary_value}",
                                        session))
        if c.candidate_id.startswith("hook-insight-") and snapshot is not None:
            ins = next((i for i in snapshot.insights if i.insight_id in c.source_insight_ids), None)
            if ins is not None:
                return pub.admit(insight_fact(ins, c.secondary_text, session))
        return True

    def admit_insight(ins, line):
        ok = pub.admit(insight_fact(ins, line, session))
        if not ok:
            plan.omitted.append({"scene": "context", "reason": "publication_profile",
                                 "item": ins.insight_id, "profile": pub.profile.value})
        return ok

    def admit_event(ev):
        ok = pub.admit(report_event_fact(ev, session))
        if not ok:
            plan.omitted.append({"scene": "events", "reason": "publication_profile",
                                 "item": ev.get("text"), "origin": ev.get("origin"),
                                 "profile": pub.profile.value})
        return ok

    hook_scene, chosen = _hook_scene(report, snapshot, is_safe, admit_hook)
    used_insights = set(chosen.source_insight_ids)
    plan.scenes.append(hook_scene)
    plan.notes.append(f"hook: {chosen.candidate_id}")

    # POST freeze: a ranking over a partial (or unproven) universe is never published.
    gate = movers_coverage_verdict(report)
    plan.notes.append(f"movers coverage gate: {gate['status']} - {gate['reason']}")
    if not gate["publishable"]:
        plan.omitted.append({"scene": "movers", "reason": "universe_coverage",
                             "status": gate["status"], "detail": gate["reason"],
                             "coverage_pct": gate["coverage_pct"],
                             "universe_expected": gate["universe_expected"],
                             "universe_validated": gate["universe_validated"]})
    for held in gate["guard_excluded"]:
        plan.omitted.append({"scene": "movers", "reason": "move_guard", "item": held["symbol"],
                             "status": held["status"], "detail": held["reason"]})
    movers = [gainers_scene(report), losers_scene(report)] if gate["publishable"] else [None, None]
    for sc in [m for m in movers if m is not None and m.items]:
        top = sc.items[0]
        if not pub.admit(mover_fact(top.title, f"{top.title} {top.value}", session)):
            plan.omitted.append({"scene": "movers", "reason": "publication_profile",
                                 "profile": pub.profile.value,
                                 "detail": "named single-stock moves are a security ranking - "
                                           "PRIVATE_ANALYTICS only"})
            movers = [None, None]
            break
    builders = [
        _global_scene(report),
        _nifty_scene(report, snapshot, used_insights),
        _flows_scene(report, snapshot, used_insights),
        _sectors_scene(report),
        movers[0],
        movers[1],
    ]
    for scene in builders:
        if scene is None:
            continue
        used_insights.update(scene.source_insight_ids)
        plan.scenes.append(scene)

    context = _context_scene(snapshot, used_insights, admit_insight if pub.public else None)
    if context is not None:
        plan.scenes.append(context)
    events = _events_scene(report, admit_event if pub.public else None)
    if events is not None:
        plan.scenes.append(events)
    plan.scenes.append(_outro_scene())

    # Anything that cannot be read inside its scene's longest duration leaves the Short and
    # is recorded, so an omission is visible in the artifact rather than silently lost.
    for scene in plan.scenes:
        floor = min_items_for(scene.scene_type.value)
        for dropped in trim_to_fit(scene, minimum_items=floor):
            plan.omitted.append({"scene": scene.scene_id, "reason": "reading_budget",
                                 "item": dropped.title})
        # The sector scene's diagnostic counts are set when the scene is built, before this
        # reading-budget trim can still shrink `items` further - refresh them so the artifact
        # reports what actually reached the screen, not the pre-trim count.
        if "sectors_eligible" in scene.metadata:
            eligible = scene.metadata["sectors_eligible"]
            scene.metadata["sectors_displayed"] = len(scene.items)
            scene.metadata["sectors_omitted"] = max(0, eligible - len(scene.items))

    _record_omissions(plan, report, snapshot, movers_gated=not gate["publishable"])
    return plan


def _record_omissions(plan, report, snapshot, movers_gated: bool = False) -> None:
    """Note what the canonical record holds but the Short chose not to show."""
    def _count(section):
        return len([x for x in (section or [])])

    shown_sectors = len((plan.scene(SceneType.SECTORS) or ScenePlan("", SceneType.SECTORS)).items)
    total_sectors = _count(report.sectors)
    if total_sectors > shown_sectors:
        plan.omitted.append({"scene": "sectors", "reason": "editorial_cap",
                             "count": total_sectors - shown_sectors})

    shown_gainers = len((plan.scene(SceneType.GAINERS) or ScenePlan("", SceneType.GAINERS)).items)
    shown_losers = len((plan.scene(SceneType.LOSERS) or ScenePlan("", SceneType.LOSERS)).items)
    total_movers = _count(report.gainers) + _count(report.losers)
    if total_movers > shown_gainers + shown_losers and not movers_gated:
        plan.omitted.append({"scene": "movers", "reason": "editorial_cap",
                             "count": total_movers - (shown_gainers + shown_losers)})

    shown_cues = len((plan.scene(SceneType.GLOBAL) or ScenePlan("", SceneType.GLOBAL)).items)
    total_cues = _count(report.global_cues)
    if total_cues > shown_cues:
        plan.omitted.append({"scene": "global", "reason": "editorial_cap",
                             "count": total_cues - shown_cues})

    shown_events = len((plan.scene(SceneType.EVENTS) or ScenePlan("", SceneType.EVENTS)).items)
    total_events = _count(report.events)
    if total_events > shown_events:
        plan.omitted.append({"scene": "events", "reason": "editorial_cap",
                             "count": total_events - shown_events})

    if snapshot is not None:
        shown = len((plan.scene(SceneType.CONTEXT) or ScenePlan("", SceneType.CONTEXT)).items)
        available = len(snapshot.displayable())
        if available > shown:
            plan.omitted.append({"scene": "context", "reason": "editorial_cap",
                                 "count": available - shown})


__all__ = ["plan_short", "GLOBAL_CUE_PREFERENCE", "gainers_scene", "losers_scene"]

"""Sector persistence: which sectors have held a direction across recent sessions.

Daily percentage changes are compounded, never summed. Summing them is wrong immediately and
increasingly wrong as the window lengthens, and the result would still be labelled a
"return", which is how a plausible-looking number ends up on screen.

A sector missing from a session is skipped explicitly and shrinks the sample rather than
being treated as a flat day - an absent reading is not a zero.

The streak is a CONTINUITY claim and walks the canonical session spine: a recorded session
where the sector is missing or its fact ineligible BREAKS the run rather than being stepped
over. A missing calendar day is different - weekends, holidays and days we never recorded
are not on the spine at all and cannot break anything.
"""
from __future__ import annotations

from core import Metric

from .history import compounded_return, continuity_streak, median, strength_for
from .models import IntelligenceInsight, InsightCategory, Strength, is_eligible

LOOKBACK = 5
MIN_STREAK_TO_REPORT = 3
MAX_SECTOR_INSIGHTS = 2


def analyse(report, window) -> list:
    current = _current_sectors(report)
    if not current:
        return []

    history = window.series(Metric.SECTOR_CHANGE_PCT)
    by_session = {}
    for point in history:
        by_session.setdefault(point.market_date, {})[point.instrument] = point
    # The spine, not the sessions that happen to have sector data: a recorded session where
    # a sector is missing must be able to break that sector's run.
    sessions = window.session_spine(LOOKBACK)

    candidates = []
    for name, (value, fact_id) in sorted(current.items()):
        readings = [by_session[s][name] for s in sessions
                    if s in by_session and name in by_session[s]]
        sample = len(readings) + 1                      # historical readings plus today
        if sample < 2:
            continue

        rising = value > 0
        supporting = (continuity_streak(sessions,
                                        {s: by_session[s][name] for s in sessions
                                         if s in by_session and name in by_session[s]},
                                        positive=rising)
                      if value != 0 else [])
        run = 1 + len(supporting) if value != 0 else 0
        sequence = [value] + [r.value for r in readings]
        positives = sum(1 for v in sequence if v > 0)
        cumulative = compounded_return(sequence)

        if len(readings) < len(sessions):
            window.warn(f"sector {name} is absent from "
                        f"{len(sessions) - len(readings)} of the last {len(sessions)} "
                        "recorded sessions; its sample is reduced rather than padded")

        candidates.append({
            "name": name, "value": value, "fact_id": fact_id, "readings": readings,
            "supporting": supporting, "sample": sample, "streak": run,
            "positives": positives, "cumulative": cumulative, "rising": rising,
            "outperformed": _median_outperformance(name, sessions, by_session, current),
        })

    # Longest run first; ties broken by name so the selection is total and stable.
    candidates.sort(key=lambda c: (-c["streak"], c["name"]))
    insights = []
    for candidate in candidates:
        if candidate["streak"] < MIN_STREAK_TO_REPORT:
            continue
        if len(insights) >= MAX_SECTOR_INSIGHTS:
            break
        insights.append(_insight(candidate))

    if not insights and candidates:
        window.warn(f"no sector held a direction for {MIN_STREAK_TO_REPORT}+ consecutive "
                    "available sessions")
    return insights


def _insight(candidate) -> IntelligenceInsight:
    name, run = candidate["name"], candidate["streak"]
    direction = "advanced" if candidate["rising"] else "declined"
    supporting = candidate["supporting"]
    return IntelligenceInsight(
        insight_id=f"sector-streak-{name.lower().replace(' ', '-')}",
        category=InsightCategory.SECTOR_PERSISTENCE, subject=name,
        statement=f"Nifty {name} has {direction} in {run} consecutive recorded sessions.",
        current_value=candidate["value"], comparison_value=candidate["cumulative"],
        lookback_sessions=run, sample_size=candidate["sample"],
        strength=strength_for(candidate["sample"], LOOKBACK + 1),
        supporting_report_ids=[r.report_id for r in supporting],
        supporting_fact_ids=[candidate["fact_id"]] + [r.fact_id for r in supporting],
        metadata={
            "streak_sessions": run, "direction": "UP" if candidate["rising"] else "DOWN",
            "positive_sessions": candidate["positives"],
            # Compounded, not summed - see the module docstring.
            "compounded_change_pct": candidate["cumulative"],
            "compounding": "product(1 + r/100) - 1",
            "outperformed_median_sessions": candidate["outperformed"],
            "includes_current_session": True,
            "continuity": "adjacent canonical sessions; a recorded session where the sector "
                          "is missing or ineligible breaks the run",
            "selection_score": min(run / 5.0, 1.0),
        })


def _median_outperformance(name, sessions, by_session, current) -> int:
    """How often this sector beat the median sector, today included.

    The median is taken across the sectors present in that session, so a session where only
    half the sectors were captured is still compared against its own cohort rather than
    against a padded one.
    """
    count = 0
    today_values = [v for v, _ in current.values()]
    today_median = median(today_values)
    if today_median is not None and current[name][0] > today_median:
        count += 1

    for session in sessions:
        # The spine includes recorded sessions that have no eligible sector readings at all,
        # so this must tolerate a session being absent from `by_session` entirely.
        readings = by_session.get(session)
        if not readings or name not in readings:
            continue
        mid = median([r.value for r in readings.values()])
        if mid is not None and readings[name].value > mid:
            count += 1
    return count


def _current_sectors(report) -> dict:
    out = {}
    for fact in report.facts_for(Metric.SECTOR_CHANGE_PCT):
        if fact.value is not None and is_eligible(fact.validation_status):
            out[fact.instrument] = (float(fact.value), fact.fact_id)
    return out


__all__ = ["analyse", "LOOKBACK", "MIN_STREAK_TO_REPORT", "MAX_SECTOR_INSIGHTS"]

"""FII/DII persistence: how long the current direction of institutional flow has held.

A streak is a run that ends today, so today's reading is appended to history exactly once -
history is loaded strictly before the session, and the current value comes from the current
report. Double-counting today would lengthen every streak by one.

Two different questions, two different semantics, and the wording says which is which:

* **Streak** - a CONTINUITY claim over *consecutive recorded sessions*. It walks the
  canonical session spine and stops at the first recorded session that does not continue
  the run, including one whose evidence is missing or ineligible. It never skips over such
  a session to find another matching value further back.
* **Cumulative flow** - a SUMMARY over the last N *available sessions*, which is exactly
  what "available" discloses: sessions with eligible evidence, gaps passed over.

Co-occurrence is not causality. This layer reports that FIIs sold and that Nifty moved; it
never says one caused the other.
"""
from __future__ import annotations

from core import Metric

from .history import continuity_streak, strength_for
from .models import IntelligenceInsight, InsightCategory, Strength, is_eligible

FLOWS = (("FII", Metric.FII_NET_CASH), ("DII", Metric.DII_NET_CASH))
CUMULATIVE_LOOKBACKS = (5, 20)
MIN_STREAK_TO_REPORT = 2
UNIT = "INR_CRORE"


def analyse(report, window) -> list:
    insights = []
    for subject, metric in FLOWS:
        current = _current_flow(report, metric)
        if current is None:
            continue
        value, fact_id = current
        history = window.series(metric, subject)
        # Today first, then history - a run ending today, counted once.
        sequence = [value] + [p.value for p in history]

        insights.extend(_streak_insight(subject, value, fact_id, history, window))
        insights.extend(_cumulative_insights(subject, metric, value, fact_id, history,
                                             sequence, window))
    return insights


def _streak_insight(subject, value, fact_id, history, window) -> list:
    """A continuity claim: consecutive recorded sessions, broken by any session we recorded
    whose evidence does not continue the run (opposite sign, zero, missing or ineligible)."""
    if value == 0:
        return []
    buying = value > 0
    supporting = continuity_streak(window.session_spine(),
                                   {p.market_date: p for p in history}, positive=buying)
    length = 1 + len(supporting)                     # today plus the unbroken run behind it
    if length < MIN_STREAK_TO_REPORT:
        return []

    direction = "net buyers" if buying else "net sellers"
    return [IntelligenceInsight(
        insight_id=f"{subject.lower()}-flow-streak",
        category=InsightCategory.INSTITUTIONAL_FLOW, subject=subject,
        statement=(f"{subject}s have been {direction} for {length} consecutive "
                   f"recorded sessions."),
        current_value=value, lookback_sessions=length, sample_size=length,
        strength=Strength.FULL_HISTORY,
        supporting_report_ids=[p.report_id for p in supporting],
        supporting_fact_ids=[fact_id] + [p.fact_id for p in supporting],
        metadata={"direction": "BUYING" if buying else "SELLING", "streak_sessions": length,
                  "includes_current_session": True, "unit": UNIT,
                  "continuity": "adjacent canonical sessions; a recorded session with "
                                "missing or ineligible evidence breaks the run",
                  # A five-session run is genuinely unusual; anything beyond that is capped
                  # so one very long streak cannot crowd out every other category forever.
                  "selection_score": min(length / 5.0, 1.0)})]


def _cumulative_insights(subject, metric, value, fact_id, history, sequence, window) -> list:
    insights = []
    for lookback in CUMULATIVE_LOOKBACKS:
        # "Last N sessions" includes today, so N-1 historical sessions are required.
        needed = lookback - 1
        recent = history[:needed]
        sample = len(recent) + 1
        strength = strength_for(sample, lookback)

        if strength is not Strength.FULL_HISTORY:
            window.warn(f"{subject} cumulative flow over {lookback} sessions needs {needed} "
                        f"prior sessions, {len(recent)} available")
            insights.append(IntelligenceInsight(
                insight_id=f"{subject.lower()}-flow-cumulative-{lookback}",
                category=InsightCategory.INSTITUTIONAL_FLOW, subject=subject, statement="",
                current_value=value, lookback_sessions=lookback, sample_size=sample,
                strength=Strength.INSUFFICIENT_HISTORY, supporting_fact_ids=[fact_id],
                metadata={"reason": "insufficient_history", "required_sessions": lookback}))
            continue

        values = sequence[:lookback]
        total = sum(values)
        buying = sum(1 for v in values if v > 0)
        selling = sum(1 for v in values if v < 0)
        # The count must be of the direction actually named, or the sentence contradicts
        # itself ("net sellers in 0 of them").
        net_buyers = total > 0
        direction = "net buyers" if net_buyers else "net sellers"
        matching = buying if net_buyers else selling
        insights.append(IntelligenceInsight(
            insight_id=f"{subject.lower()}-flow-cumulative-{lookback}",
            category=InsightCategory.INSTITUTIONAL_FLOW, subject=subject,
            statement=(f"Over the last {lookback} available sessions {subject}s were "
                       f"{direction} in {matching} of them, with a cumulative net flow of "
                       f"Rs {total:,.0f} crore."),
            current_value=value, comparison_value=total, lookback_sessions=lookback,
            sample_size=sample, strength=strength,
            supporting_report_ids=[p.report_id for p in recent],
            supporting_fact_ids=[fact_id] + [p.fact_id for p in recent],
            metadata={"cumulative_flow": total, "positive_sessions": buying,
                      "negative_sessions": selling, "matching_sessions": matching,
                      "unit": UNIT, "includes_current_session": True,
                      # One-sided windows are the interesting ones; an even split is not.
                      "selection_score": abs(buying / lookback - 0.5) * 2}))
    return insights


def _current_flow(report, metric):
    for fact in report.facts_for(metric):
        if fact.value is not None and is_eligible(fact.validation_status):
            return float(fact.value), fact.fact_id
    return None


__all__ = ["analyse", "FLOWS", "CUMULATIVE_LOOKBACKS", "MIN_STREAK_TO_REPORT"]

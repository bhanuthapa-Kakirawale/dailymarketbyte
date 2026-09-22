"""Index move context: how today's Nifty move compares with recent sessions.

Descriptive only. "Larger than 17 of the previous 20 sessions" is a countable fact a viewer
could verify; "a strong market" is an opinion about the future wearing a number's clothes.
"""
from __future__ import annotations

from core import Metric

from .history import count_below, mean, median
from .models import IntelligenceInsight, InsightCategory, Strength

INSTRUMENT = "NIFTY 50"
LOOKBACKS = (5, 20)


def analyse(report, window) -> list:
    """Insights comparing today's index move against the previous 5 and 20 sessions."""
    current = _current_move(report)
    if current is None:
        window.warn("no eligible NIFTY 50 change fact in the current report; "
                    "index move context skipped")
        return []

    value, fact_id = current
    today = abs(value)
    history = window.series(Metric.INDEX_CHANGE_PCT, INSTRUMENT)

    insights = []
    for lookback in LOOKBACKS:
        recent = history[:lookback]
        sample = len(recent)

        # A statistic defined as N sessions is not that statistic with fewer. Calling a
        # 15-session sample "partial" would invite presenting it as a 20-session reading
        # anyway, so a short window is simply insufficient and makes no statement.
        if sample < lookback:
            window.warn(f"index move context over {lookback} sessions needs {lookback} prior "
                        f"sessions, {sample} available")
            insights.append(IntelligenceInsight(
                insight_id=f"index-move-{lookback}",
                category=InsightCategory.INDEX_MOVE, subject=INSTRUMENT,
                statement="", current_value=value, lookback_sessions=lookback,
                sample_size=sample, strength=Strength.INSUFFICIENT_HISTORY,
                supporting_fact_ids=[fact_id],
                metadata={"reason": "insufficient_history", "required_sessions": lookback}))
            continue

        moves = [abs(p.value) for p in recent]
        larger_than = count_below(moves, today)
        average = mean(moves)
        insights.append(IntelligenceInsight(
            insight_id=f"index-move-{lookback}",
            category=InsightCategory.INDEX_MOVE, subject=INSTRUMENT,
            statement=(f"Nifty's {today:.2f}% move is larger than {larger_than} of the "
                       f"previous {lookback} sessions."),
            current_value=value, comparison_value=average, lookback_sessions=lookback,
            sample_size=sample, strength=Strength.FULL_HISTORY,
            supporting_report_ids=[p.report_id for p in recent],
            supporting_fact_ids=[fact_id] + [p.fact_id for p in recent],
            metadata={
                "absolute_move": today,
                "larger_than_sessions": larger_than,
                "mean_absolute_move": average,
                "median_absolute_move": median(moves),
                # How far up the recent range this sits: 1.0 means bigger than every prior
                # session in the window, which is what makes it worth screen time.
                "selection_score": larger_than / lookback if lookback else 0.0,
            }))
    return insights


def _current_move(report):
    """Today's index change, taken from the canonical fact so it carries a fact_id."""
    from .models import is_eligible
    for fact in report.facts_for(Metric.INDEX_CHANGE_PCT):
        if fact.instrument == INSTRUMENT and fact.value is not None and is_eligible(
                fact.validation_status):
            return float(fact.value), fact.fact_id
    return None


__all__ = ["analyse", "LOOKBACKS", "INSTRUMENT"]

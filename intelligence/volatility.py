"""India VIX context: where today's reading sits in its own recent range.

Elevated volatility is a description of the present, not a forecast. Nothing here says a
correction is coming, because nothing in the data says that.
"""
from __future__ import annotations

from core import Metric

from .history import count_below, mean
from .models import IntelligenceInsight, InsightCategory, Strength, is_eligible

INSTRUMENT = "INDIA VIX"
SHORT_LOOKBACK = 5
LONG_LOOKBACK = 20


def analyse(report, window) -> list:
    current = _current_vix(report)
    if current is None:
        return []                       # VIX is optional; its absence is not worth a warning

    value, fact_id = current
    history = window.series(Metric.VOLATILITY_INDEX, INSTRUMENT)
    insights = []

    short = history[:SHORT_LOOKBACK]
    if len(short) == SHORT_LOOKBACK:
        average = mean([p.value for p in short])
        insights.append(IntelligenceInsight(
            insight_id=f"vix-mean-{SHORT_LOOKBACK}",
            category=InsightCategory.VOLATILITY, subject=INSTRUMENT,
            statement=(f"India VIX at {value:.1f} is "
                       f"{'above' if value > average else 'below'} its "
                       f"{SHORT_LOOKBACK}-session average of {average:.1f}."),
            current_value=value, comparison_value=average,
            lookback_sessions=SHORT_LOOKBACK, sample_size=len(short),
            strength=Strength.FULL_HISTORY,
            supporting_report_ids=[p.report_id for p in short],
            supporting_fact_ids=[fact_id] + [p.fact_id for p in short],
            metadata={"mean": average,
                      "selection_score": _divergence(value, average)}))

    long = history[:LONG_LOOKBACK]
    sample = len(long)
    # As in market_context: a 20-session rank needs 20 sessions, or it is not that rank.
    if sample < LONG_LOOKBACK:
        window.warn(f"VIX context over {LONG_LOOKBACK} sessions needs {LONG_LOOKBACK} prior "
                    f"readings, {sample} available")
        insights.append(IntelligenceInsight(
            insight_id=f"vix-rank-{LONG_LOOKBACK}",
            category=InsightCategory.VOLATILITY, subject=INSTRUMENT, statement="",
            current_value=value, lookback_sessions=LONG_LOOKBACK, sample_size=sample,
            strength=Strength.INSUFFICIENT_HISTORY, supporting_fact_ids=[fact_id],
            metadata={"reason": "insufficient_history", "required_sessions": LONG_LOOKBACK}))
        return insights

    values = [p.value for p in long]
    higher_than = count_below(values, value)
    average = mean(values)
    insights.append(IntelligenceInsight(
        insight_id=f"vix-rank-{LONG_LOOKBACK}",
        category=InsightCategory.VOLATILITY, subject=INSTRUMENT,
        statement=(f"India VIX at {value:.1f} is higher than {higher_than} of the previous "
                   f"{LONG_LOOKBACK} available readings."),
        current_value=value, comparison_value=average,
        lookback_sessions=LONG_LOOKBACK, sample_size=sample, strength=Strength.FULL_HISTORY,
        supporting_report_ids=[p.report_id for p in long],
        supporting_fact_ids=[fact_id] + [p.fact_id for p in long],
        metadata={
            "higher_than_sessions": higher_than,
            "mean": average, "minimum": min(values), "maximum": max(values),
            # Distance from the middle of the range: a VIX sitting at either extreme is the
            # interesting case, one in the middle is not.
            "selection_score": abs(higher_than / LONG_LOOKBACK - 0.5) * 2,
        }))
    return insights


def _divergence(value: float, average: float) -> float:
    if not average:
        return 0.0
    return min(abs(value - average) / abs(average), 1.0)


def _current_vix(report):
    for fact in report.facts_for(Metric.VOLATILITY_INDEX):
        if fact.instrument == INSTRUMENT and fact.value is not None and is_eligible(
                fact.validation_status):
            return float(fact.value), fact.fact_id
    return None


__all__ = ["analyse", "INSTRUMENT", "SHORT_LOOKBACK", "LONG_LOOKBACK"]

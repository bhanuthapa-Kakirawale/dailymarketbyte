"""Mover recurrence and relative-volume context.

Both describe stocks, and both are descriptive only. A stock appearing repeatedly among the
tracked movers is a fact about how often it has moved; it is not a signal, a ranking, or a
reason to own it, and the wording is chosen so it cannot be read as one.

Relative volume is only ever compared against readings produced by the SAME definition
version. Phase 3 changed the window from 10 prior sessions to 20, so mixing 1.x and 2.0
readings would compare two different measurements that happen to share a name.
"""
from __future__ import annotations

from core import Metric

from .history import count_below, strength_for
from .models import IntelligenceInsight, InsightCategory, Strength, is_eligible

RECENT_LOOKBACK = 5
LONG_LOOKBACK = 20

# Documented, tested thresholds over the long lookback. Descriptive labels for how often a
# stock has shown up - never a measure of how attractive it is.
FIRST_APPEARANCE = "FIRST_APPEARANCE"
REPEAT_MOVER = "REPEAT_MOVER"
FREQUENT_MOVER = "FREQUENT_MOVER"
REPEAT_THRESHOLD = 1          # 1-2 prior appearances
FREQUENT_THRESHOLD = 3        # 3 or more

RELATIVE_VOLUME_DEFINITION_VERSION = "2.0"
RELATIVE_VOLUME_MIN_SAMPLE = 5
MAX_MOVER_INSIGHTS = 2
MAX_VOLUME_INSIGHTS = 1


def classify(appearances: int) -> str:
    if appearances >= FREQUENT_THRESHOLD:
        return FREQUENT_MOVER
    if appearances >= REPEAT_THRESHOLD:
        return REPEAT_MOVER
    return FIRST_APPEARANCE


# --------------------------------------------------------------------- recurrence
def analyse_recurrence(report, window) -> list:
    symbols = _current_movers(report)
    if not symbols:
        return []

    points = window.points(Metric.STOCK_CHANGE_PCT)
    # One appearance per (symbol, session): a symbol listed twice in one report is one
    # appearance, not two.
    seen, appearances = set(), {}
    for point in points:
        key = (point.instrument, point.market_date)
        if key in seen:
            continue
        seen.add(key)
        appearances.setdefault(point.instrument, []).append(point)

    sessions = sorted({p.market_date for p in points}, reverse=True)
    recent_sessions = set(sessions[:RECENT_LOOKBACK])
    long_sessions = set(sessions[:LONG_LOOKBACK])

    candidates = []
    for symbol, (fact_id, _) in sorted(symbols.items()):
        history = appearances.get(symbol, [])
        long_hits = [p for p in history if p.market_date in long_sessions]
        recent_hits = [p for p in history if p.market_date in recent_sessions]
        gainers = sum(1 for p in recent_hits
                      if (p.observation_metadata or {}).get("bucket") == "gainer")
        losers = sum(1 for p in recent_hits
                     if (p.observation_metadata or {}).get("bucket") == "loser")
        candidates.append({"symbol": symbol, "fact_id": fact_id, "hits": long_hits,
                           "count": len(long_hits), "recent": len(recent_hits),
                           "gainers": gainers, "losers": losers,
                           "window": min(len(sessions), LONG_LOOKBACK)})

    candidates.sort(key=lambda c: (-c["count"], c["symbol"]))
    insights = []
    for candidate in candidates:
        if candidate["count"] < REPEAT_THRESHOLD:
            continue                       # a first appearance is not worth screen time
        if len(insights) >= MAX_MOVER_INSIGHTS:
            break
        insights.append(_recurrence_insight(candidate))
    return insights


def _recurrence_insight(candidate) -> IntelligenceInsight:
    symbol, count, window_size = candidate["symbol"], candidate["count"], candidate["window"]
    return IntelligenceInsight(
        insight_id=f"mover-recurrence-{symbol.lower()}",
        category=InsightCategory.MOVER_RECURRENCE, subject=symbol,
        statement=(f"{symbol} has appeared among the tracked top movers {count} times in the "
                   f"last {window_size} available sessions."),
        current_value=float(count), lookback_sessions=window_size,
        sample_size=window_size, strength=strength_for(window_size, LONG_LOOKBACK),
        supporting_report_ids=[p.report_id for p in candidate["hits"]],
        supporting_fact_ids=[candidate["fact_id"]] + [p.fact_id for p in candidate["hits"]],
        metadata={
            "classification": classify(count),
            "prior_appearances": count,
            "appearances_last_5_sessions": candidate["recent"],
            "as_gainer_last_5": candidate["gainers"],
            "as_loser_last_5": candidate["losers"],
            "excludes_current_session": True,
            "selection_score": min(count / 5.0, 1.0),
        })


# --------------------------------------------------------------------- relative volume
def analyse_relative_volume(report, window) -> list:
    current = _current_relative_volumes(report)
    if not current:
        return []

    points = window.points(Metric.STOCK_RELATIVE_VOLUME)
    comparable, skipped = {}, 0
    for point in points:
        if (point.observation_metadata or {}).get(
                "definition_version") != RELATIVE_VOLUME_DEFINITION_VERSION:
            skipped += 1
            continue                # a 1.x reading measures a different thing entirely
        comparable.setdefault(point.instrument, []).append(point)
    if skipped:
        window.warn(f"{skipped} historical relative-volume readings use an older definition "
                    f"than {RELATIVE_VOLUME_DEFINITION_VERSION} and were excluded")

    candidates = []
    for symbol, (value, fact_id) in sorted(current.items()):
        history = comparable.get(symbol, [])
        if len(history) < RELATIVE_VOLUME_MIN_SAMPLE:
            continue
        values = [p.value for p in history]
        higher_than = count_below(values, value)
        candidates.append({"symbol": symbol, "value": value, "fact_id": fact_id,
                           "history": history, "higher_than": higher_than,
                           "sample": len(values)})

    if not candidates:
        window.warn("no stock has enough comparable relative-volume history yet "
                    f"(needs {RELATIVE_VOLUME_MIN_SAMPLE} readings at definition "
                    f"{RELATIVE_VOLUME_DEFINITION_VERSION})")
        return []

    candidates.sort(key=lambda c: (-(c["higher_than"] / c["sample"]), -c["value"], c["symbol"]))
    return [_volume_insight(c) for c in candidates[:MAX_VOLUME_INSIGHTS]]


def _volume_insight(candidate) -> IntelligenceInsight:
    symbol, value = candidate["symbol"], candidate["value"]
    sample, higher_than = candidate["sample"], candidate["higher_than"]
    return IntelligenceInsight(
        insight_id=f"relative-volume-{symbol.lower()}",
        category=InsightCategory.RELATIVE_VOLUME, subject=symbol,
        statement=(f"{symbol} traded at {value:.1f}x its 20-session average volume, higher "
                   f"than {higher_than} of its previous {sample} comparable readings."),
        current_value=value, lookback_sessions=sample, sample_size=sample,
        strength=strength_for(sample, RELATIVE_VOLUME_MIN_SAMPLE),
        supporting_report_ids=[p.report_id for p in candidate["history"]],
        supporting_fact_ids=[candidate["fact_id"]] + [p.fact_id for p in candidate["history"]],
        metadata={
            "higher_than_readings": higher_than,
            "comparable_sample": sample,
            "definition_version": RELATIVE_VOLUME_DEFINITION_VERSION,
            "selection_score": higher_than / sample if sample else 0.0,
        })


# --------------------------------------------------------------------- current report
def _current_movers(report) -> dict:
    out = {}
    for fact in report.facts_for(Metric.STOCK_CHANGE_PCT):
        if fact.value is not None and is_eligible(fact.validation_status):
            out[fact.instrument] = (fact.fact_id, float(fact.value))
    return out


def _current_relative_volumes(report) -> dict:
    """Today's relative volumes, only where the observation says definition 2.0 produced them."""
    out = {}
    for fact in report.facts_for(Metric.STOCK_RELATIVE_VOLUME):
        if fact.value is None or not is_eligible(fact.validation_status):
            continue
        versions = {(o.metadata or {}).get("definition_version") for o in fact.observations}
        if RELATIVE_VOLUME_DEFINITION_VERSION in versions:
            out[fact.instrument] = (float(fact.value), fact.fact_id)
    return out


__all__ = ["analyse_recurrence", "analyse_relative_volume", "classify", "RECENT_LOOKBACK",
           "LONG_LOOKBACK", "FIRST_APPEARANCE", "REPEAT_MOVER", "FREQUENT_MOVER",
           "REPEAT_THRESHOLD", "FREQUENT_THRESHOLD", "RELATIVE_VOLUME_MIN_SAMPLE",
           "RELATIVE_VOLUME_DEFINITION_VERSION"]

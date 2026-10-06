"""Deterministic FII/DII materiality context: is today's flow unusual relative to recent
canonical history, worth a POST/PRE mention - never a probability, never a prediction.

Two independent dimensions are reported per participant, never collapsed into one tag:

* **magnitude** - how large |today's net flow| is against the median of the prior 10 eligible
  sessions. Needs >= 10 prior eligible sessions or the verdict is INSUFFICIENT_HISTORY; a
  caller that sees that must fall back to its own legacy threshold rather than treat it as
  NORMAL_RANGE (silence is not evidence of an ordinary day - it is evidence of no history yet).
* **streak** - the CONTINUITY state (`intelligence.history.continuity_streak`, same semantics
  as `intelligence.flows`'s streak insight): does today continue, reverse, or say nothing new
  about the run that came before it.

No acquisition, no model: every number here is arithmetic over facts `intelligence.history`
already loaded from canonical history (`ELIGIBLE_HISTORICAL_STATUSES` only).
"""
from __future__ import annotations

from core import Metric

from .history import continuity_streak, median
from .models import is_eligible

RULE_VERSION = "inst-flow-materiality-1.0"

MATERIALITY_LOOKBACK = 10        # prior eligible sessions required for a magnitude verdict
LARGE_MULTIPLE = 2.0             # |current| >= this x the prior-10 median |value|
LARGE_FLOOR_CR = 1000.0          # ... AND at least this large in absolute terms
REVERSAL_MULTIPLE = 1.0          # |current| >= this x the prior-10 median, to call a reversal
REVERSAL_MIN_PRIOR_RUN = 3       # an opposite-direction run must be at least this long
STREAK_MILESTONES = (4, 5)       # lengths (including today) that are always a milestone
STREAK_MILESTONE_STEP = 5        # beyond the fixed milestones, every 5th session is one too

INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NORMAL_RANGE = "NORMAL_RANGE"
LARGE_NET_BUY = "LARGE_NET_BUY"
LARGE_NET_SELL = "LARGE_NET_SELL"
DIRECTION_CONTINUES = "DIRECTION_CONTINUES"
DIRECTION_REVERSES = "DIRECTION_REVERSES"
NO_MEANINGFUL_CHANGE = "NO_MEANINGFUL_CHANGE"

FLOWS = (("FII", Metric.FII_NET_CASH), ("DII", Metric.DII_NET_CASH))


def _current(report, metric):
    for fact in report.facts_for(metric):
        if fact.value is not None and is_eligible(fact.validation_status):
            return float(fact.value), fact.fact_id
    return None


def _is_streak_milestone(length: int) -> bool:
    if length in STREAK_MILESTONES:
        return True
    return length >= 10 and length % STREAK_MILESTONE_STEP == 0


def analyse_participant(report, window, subject: str, metric) -> dict | None:
    """Materiality context for one participant (FII or DII), or None if there is no current
    eligible flow fact to assess at all."""
    current = _current(report, metric)
    if current is None:
        return None
    value, fact_id = current
    history = window.series(metric, subject)
    prior10 = [p.value for p in history[:MATERIALITY_LOOKBACK]]

    out = {"subject": subject, "current_value": value, "fact_id": fact_id,
          "rule_version": RULE_VERSION, "prior_sessions_available": len(history),
          "lookback": MATERIALITY_LOOKBACK}

    # --------------------------------------------------------------- magnitude
    if len(prior10) < MATERIALITY_LOOKBACK:
        out["magnitude_state"] = INSUFFICIENT_HISTORY
        out["median_abs_prior_10"] = None
        out["multiple_of_median"] = None
    else:
        med = median([abs(v) for v in prior10])
        out["median_abs_prior_10"] = med
        multiple = (abs(value) / med) if med else None
        out["multiple_of_median"] = multiple
        if multiple is not None and multiple >= LARGE_MULTIPLE and abs(value) >= LARGE_FLOOR_CR:
            out["magnitude_state"] = LARGE_NET_BUY if value > 0 else LARGE_NET_SELL
        else:
            out["magnitude_state"] = NORMAL_RANGE

    # --------------------------------------------------------------- streak / reversal
    if value == 0:
        out.update(streak_state=NO_MEANINGFUL_CHANGE, streak_length=0, streak_milestone=False,
                  prior_run_length=0)
        return out
    buying = value > 0
    by_session = {p.market_date: p for p in history}
    same_dir_run = continuity_streak(window.session_spine(), by_session, positive=buying)
    streak_length = 1 + len(same_dir_run)
    milestone = _is_streak_milestone(streak_length)
    if streak_length > 1:
        out.update(streak_state=DIRECTION_CONTINUES, streak_length=streak_length,
                   streak_milestone=milestone, prior_run_length=0,
                   streak_supporting_fact_ids=[fact_id] + [p.fact_id for p in same_dir_run])
        return out

    # today does NOT continue an existing same-direction run (streak_length == 1): check
    # whether it reverses an opposite-direction run immediately before it.
    opposite_run = continuity_streak(window.session_spine(), by_session, positive=not buying)
    med = out.get("median_abs_prior_10")
    reverses = (len(opposite_run) >= REVERSAL_MIN_PRIOR_RUN and med is not None
               and abs(value) >= REVERSAL_MULTIPLE * med)
    out.update(
        streak_state=DIRECTION_REVERSES if reverses else NO_MEANINGFUL_CHANGE,
        streak_length=streak_length, streak_milestone=False,
        prior_run_length=len(opposite_run),
        streak_supporting_fact_ids=[fact_id] + [p.fact_id for p in opposite_run] if reverses
        else [fact_id])
    return out


def analyse(report, window) -> dict:
    """{'FII': {...}, 'DII': {...}} - only for participants with a current eligible fact."""
    out = {}
    for subject, metric in FLOWS:
        ctx = analyse_participant(report, window, subject, metric)
        if ctx is not None:
            out[subject] = ctx
    return out


def is_material(ctx: dict) -> bool:
    """One deterministic yes/no for 'does this participant's context earn a mention' - large
    magnitude, a streak milestone, or a reversal. Never NORMAL_RANGE/INSUFFICIENT_HISTORY alone."""
    if ctx is None:
        return False
    if ctx.get("magnitude_state") in (LARGE_NET_BUY, LARGE_NET_SELL):
        return True
    if ctx.get("streak_state") == DIRECTION_CONTINUES and ctx.get("streak_milestone"):
        return True
    return ctx.get("streak_state") == DIRECTION_REVERSES


__all__ = ["RULE_VERSION", "MATERIALITY_LOOKBACK", "LARGE_MULTIPLE", "LARGE_FLOOR_CR",
           "REVERSAL_MULTIPLE", "REVERSAL_MIN_PRIOR_RUN", "INSUFFICIENT_HISTORY",
           "NORMAL_RANGE", "LARGE_NET_BUY", "LARGE_NET_SELL", "DIRECTION_CONTINUES",
           "DIRECTION_REVERSES", "NO_MEANINGFUL_CHANGE", "FLOWS", "analyse",
           "analyse_participant", "is_material"]

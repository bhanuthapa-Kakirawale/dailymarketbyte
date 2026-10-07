"""Human-readable statements of the Radar's OWN rules, built from its threshold dataclasses.

Nothing here decides anything: the desk only quotes the rule that produced a reason code, read
from `radar.thresholds` at runtime, so the text cannot drift from the code that applies it.
"""
from __future__ import annotations

from radar.thresholds import (DEFAULT_COMPOSITE_THRESHOLDS, DEFAULT_NOVELTY_THRESHOLDS,
                              DEFAULT_RELATIVE_THRESHOLDS, DEFAULT_TECHNICAL_THRESHOLDS,
                              DEFAULT_VOLUME_THRESHOLDS)

V, T, R, C, N = (DEFAULT_VOLUME_THRESHOLDS, DEFAULT_TECHNICAL_THRESHOLDS,
                 DEFAULT_RELATIVE_THRESHOLDS, DEFAULT_COMPOSITE_THRESHOLDS,
                 DEFAULT_NOVELTY_THRESHOLDS)

FAMILY_RULES = {
    "VOLUME": (f"Relative volume (session volume / mean of the prior 20 sessions) at least "
               f"{V.unusual_rvol:g}x = UNUSUAL, at least {V.extreme_rvol:g}x = EXTREME. "
               f"{V.elevated_rvol:g}x-{V.unusual_rvol:g}x is ELEVATED and stays context only."),
    "STRUCTURE": (f"A discrete price-structure transition: close beyond the prior "
                  f"{T.range_window_short}- or {T.range_window_long}-session high/low, or a close "
                  f"crossing its SMA{T.sma_short} / SMA{T.sma_long} versus the previous session. "
                  "Range compression alone never counts."),
    "RELATIVE_PERFORMANCE": (f"5-session AND 20-session return versus NIFTY 50 both beyond "
                             f"+/-{R.persistence_threshold_pp:g} pp in the same direction "
                             "(PERSISTENT_POSITIVE / PERSISTENT_NEGATIVE)."),
}

REASON_RULES = {
    "VOLUME_UNUSUAL": f"RVOL >= {V.unusual_rvol:g}x prior-20 average",
    "VOLUME_EXTREME": f"RVOL >= {V.extreme_rvol:g}x prior-20 average",
    "STRUCTURE_BREAK_ABOVE_20D_RANGE": f"close > highest high of prior {T.range_window_short} sessions",
    "STRUCTURE_BREAK_BELOW_20D_RANGE": f"close < lowest low of prior {T.range_window_short} sessions",
    "STRUCTURE_BREAK_ABOVE_50D_RANGE": f"close > highest high of prior {T.range_window_long} sessions",
    "STRUCTURE_BREAK_BELOW_50D_RANGE": f"close < lowest low of prior {T.range_window_long} sessions",
    "STRUCTURE_CROSS_ABOVE_SMA20": f"close moved from below to above SMA{T.sma_short}",
    "STRUCTURE_CROSS_BELOW_SMA20": f"close moved from above to below SMA{T.sma_short}",
    "STRUCTURE_CROSS_ABOVE_SMA50": f"close moved from below to above SMA{T.sma_long}",
    "STRUCTURE_CROSS_BELOW_SMA50": f"close moved from above to below SMA{T.sma_long}",
    "RELATIVE_PERSISTENT_POSITIVE": f"5D and 20D vs NIFTY 50 both > +{R.persistence_threshold_pp:g} pp",
    "RELATIVE_PERSISTENT_NEGATIVE": f"5D and 20D vs NIFTY 50 both < -{R.persistence_threshold_pp:g} pp",
}

CANDIDATE_RULE = (f"A Radar candidate has at least {C.min_independent_families} of the 3 "
                  "independent evidence families active. NOTABLE = "
                  f"{C.notable_min_families} families, HIGH_INTEREST = "
                  f"{C.high_interest_min_families}.")

NOVELTY_RULE = (f"Radar novelty compares a candidate with its own most recent candidate "
                f"appearance within the last {N.lookback_sessions} sessions (radar.novelty).")

APPEARANCE_RULE = ("Desk-derived from recorded candidate history: FIRST RECORDED = no earlier "
                   "appearance since history began; CONSECUTIVE = also a candidate on the "
                   "previous session; REAPPEARED = earlier appearance with a gap of at least "
                   "one session.")

MS_UNUSUAL_VOLUME_RULE = (f"Market Structure counts UNUSUAL VOLUME at RVOL >= "
                          f"{V.elevated_rvol:g}x (its own public definition) - a wider net than "
                          f"the Radar VOLUME family (>= {V.unusual_rvol:g}x).")


def reason_rule(code: str) -> str:
    return REASON_RULES.get(code, "")


def family_of(code: str) -> str:
    if code.startswith("VOLUME_"):
        return "VOLUME"
    if code.startswith("STRUCTURE_"):
        return "STRUCTURE"
    if code.startswith("RELATIVE_"):
        return "RELATIVE_PERFORMANCE"
    return ""


__all__ = ["FAMILY_RULES", "REASON_RULES", "CANDIDATE_RULE", "NOVELTY_RULE", "APPEARANCE_RULE",
          "MS_UNUSUAL_VOLUME_RULE", "reason_rule", "family_of"]

"""The ten component scorers (A-J, owner spec sections 3, 23-25) and the total.

Every scorer takes the already-assembled `inputs` dict (built by `service.py` from
`context.py`/`metrics.py` reads) and returns one `ComponentScore` - deterministic, explainable,
no opaque numbers. A component that cannot be scored from the data available returns
`status=NOT_SCORABLE`, `score=0.0`, and names exactly what was missing; `max_score` never
shrinks, so a `DATA_INCOMPLETE` candidate's low total stays self-explanatory rather than hidden
by renormalizing the denominator.
"""
from __future__ import annotations

from .models import (DATA_QUALITY_INTEGRITY, EVENT_RISK, LIQUIDITY, MARKET_ALIGNMENT,
                     MTF_CONFIRMATION, NOT_SCORABLE, RANGE_POSITION_AND_EXTENSION,
                     RELATIVE_STRENGTH, SCORED, SECTOR_ALIGNMENT, SETUP_QUALITY, VOLATILITY_FIT,
                     ComponentScore)
from .policy import DEFAULT_SCORING_POLICY, ScoringPolicy

_SIGNAL_BASE = {0: 0.0, 1: 6.0, 2: 13.0, 3: 18.0}
_VOLUME_BONUS = {"ELEVATED": 0.0, "UNUSUAL": 1.0, "EXTREME": 2.0}


def _agree(direction_sign: int, value: float | None) -> bool | None:
    if value is None or direction_sign == 0:
        return None
    return (direction_sign > 0 and value > 0) or (direction_sign < 0 and value < 0)


def score_setup_quality(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_setup_quality
    count = inputs.get("independent_signal_count") or 0
    base = _SIGNAL_BASE.get(min(count, 3), 0.0)
    bonus = _VOLUME_BONUS.get(inputs.get("volume_level") or "", 0.0)
    score = min(cap, base + bonus)
    evidence = [f"{count} independent evidence family(ies) active"]
    if inputs.get("volume_level"):
        evidence.append(f"volume anomaly level {inputs['volume_level']}")
    if inputs.get("technical_events"):
        evidence.append("technical events: " + ", ".join(inputs["technical_events"]))
    return ComponentScore(SETUP_QUALITY, score, cap, SCORED, tuple(evidence), (),
                          f"base {base:g} (signal count) + {bonus:g} (volume level)")


def score_market_alignment(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_market_alignment
    regime = inputs.get("regime_label")
    direction_sign = inputs.get("direction_sign") or 0
    if not regime or regime == "INSUFFICIENT_DATA" or direction_sign == 0:
        return ComponentScore(MARKET_ALIGNMENT, 0.0, cap, NOT_SCORABLE, (),
                              ("regime_label", "direction"),
                              "no usable regime classification or candidate direction")
    bull_aligned = regime == "BULLISH" and direction_sign > 0
    bear_aligned = regime == "BEARISH" and direction_sign < 0
    against = (regime == "BULLISH" and direction_sign < 0) or (regime == "BEARISH" and direction_sign > 0)
    aligned = bull_aligned or bear_aligned
    base = 8.0 if aligned else (0.0 if against else 4.0)
    streak = inputs.get("regime_streak") or 0
    streak_bonus = (4.0 if streak >= 5 else 2.0 if streak >= 2 else 0.0) if aligned else 0.0
    score = min(cap, base + streak_bonus)
    alignment = ("CANDIDATE_ALIGNS_WITH_REGIME" if aligned
                else "CANDIDATE_AGAINST_REGIME" if against else "REGIME_NEUTRAL")
    evidence = (f"regime {regime} vs candidate direction -> {alignment}",
               f"regime streak {streak} session(s)")
    return ComponentScore(MARKET_ALIGNMENT, score, cap, SCORED, evidence, (),
                          f"base {base:g} + streak bonus {streak_bonus:g}")


def score_sector_alignment(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_sector_alignment
    pp = inputs.get("sector_relative_20d_pp")
    direction_sign = inputs.get("direction_sign") or 0
    if pp is None or direction_sign == 0:
        return ComponentScore(SECTOR_ALIGNMENT, 0.0, cap, NOT_SCORABLE, (),
                              ("sector_relative_20d_pp",),
                              "no sector mapping or sector series available for this session")
    agree = _agree(direction_sign, pp)
    score = min(cap, abs(pp) / 2.0) if agree else 0.0
    evidence = (f"20-session sector-relative performance {pp:+.2f}pp "
               f"({'agrees' if agree else 'disagrees'} with candidate direction)",)
    return ComponentScore(SECTOR_ALIGNMENT, score, cap, SCORED, evidence, (),
                          "magnitude of sector-relative pp when direction agrees, else 0")


def score_relative_strength(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_relative_strength
    pp = inputs.get("market_relative_20d_pp")
    direction_sign = inputs.get("direction_sign") or 0
    if pp is None or direction_sign == 0:
        return ComponentScore(RELATIVE_STRENGTH, 0.0, cap, NOT_SCORABLE, (),
                              ("market_relative_20d_pp",), "no market-relative reading available")
    agree = _agree(direction_sign, pp)
    base = min(cap - 2.0, abs(pp) / 2.0) if agree else 0.0
    persistence = inputs.get("persistence_state")
    persists = ((persistence == "PERSISTENT_POSITIVE" and direction_sign > 0)
               or (persistence == "PERSISTENT_NEGATIVE" and direction_sign < 0))
    bonus = 2.0 if (agree and persists) else 0.0
    score = min(cap, base + bonus)
    evidence = (f"20-session market-relative performance {pp:+.2f}pp "
               f"({'agrees' if agree else 'disagrees'} with candidate direction)",
               f"persistence state: {persistence or 'unavailable'}")
    return ComponentScore(RELATIVE_STRENGTH, score, cap, SCORED, evidence, (),
                          "magnitude of market-relative pp when direction agrees, plus a "
                          "persistence bonus")


def score_mtf_confirmation(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_mtf_confirmation
    alignment = inputs.get("weekly_alignment")
    if alignment == "ALIGNED":
        return ComponentScore(MTF_CONFIRMATION, cap, cap, SCORED,
                              (inputs.get("weekly_evidence") or "",), (), "weekly ALIGNED")
    if alignment == "DIVERGENT":
        return ComponentScore(MTF_CONFIRMATION, 0.0, cap, SCORED,
                              (inputs.get("weekly_evidence") or "",), (), "weekly DIVERGENT")
    return ComponentScore(MTF_CONFIRMATION, 0.0, cap, NOT_SCORABLE, (), ("weekly_alignment",),
                          "insufficient completed weeks for a weekly reading")


def score_range_position_and_extension(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_range_position_and_extension
    state = inputs.get("extension_state")
    scale = {"NORMAL": 1.0, "STRETCHED": 0.5, "DEEPLY_STRETCHED": 0.0}
    if state not in scale:
        return ComponentScore(RANGE_POSITION_AND_EXTENSION, 0.0, cap, NOT_SCORABLE, (),
                              ("extension_state",), "ATR14 or SMA20 distance unavailable")
    score = cap * scale[state]
    atr_units = inputs.get("extension_atr_units")
    evidence = [f"extension state {state}"]
    if atr_units is not None:
        evidence.append(f"{atr_units:.2f} ATR14 from the 20-session mean")
    rp = inputs.get("range_position_pct")
    if rp is not None:
        evidence.append(f"{rp:.0f}% of the way between the prior 20-session low and high")
    return ComponentScore(RANGE_POSITION_AND_EXTENSION, score, cap, SCORED, tuple(evidence), (),
                          f"extension state {state} scaled against the 20-session mean")


def score_liquidity(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_liquidity
    band = inputs.get("liquidity_band")
    scale = {"THIN": 0.25, "ADEQUATE": 0.75, "DEEP": 1.0}
    if band not in scale:
        return ComponentScore(LIQUIDITY, 0.0, cap, NOT_SCORABLE, (), ("liquidity_band",),
                              "no volume/price data for a turnover estimate")
    turnover = inputs.get("turnover_cr")
    evidence = [f"liquidity band {band}"]
    if turnover is not None:
        evidence.append(f"~{turnover:.1f} crore average 20-session turnover")
    return ComponentScore(LIQUIDITY, cap * scale[band], cap, SCORED, tuple(evidence), (),
                          f"liquidity band {band}")


def score_volatility_fit(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_volatility_fit
    band = inputs.get("volatility_band")
    scale = {"TIGHT": 0.625, "NORMAL": 1.0, "ELEVATED": 0.5, "EXTREME": 0.0}
    if band not in scale:
        return ComponentScore(VOLATILITY_FIT, 0.0, cap, NOT_SCORABLE, (), ("volatility_band",),
                              "ATR14 unavailable")
    atr_pct = inputs.get("atr14_pct")
    evidence = [f"volatility band {band}"]
    if atr_pct is not None:
        evidence.append(f"ATR14 is {atr_pct:.2f}% of close")
    return ComponentScore(VOLATILITY_FIT, cap * scale[band], cap, SCORED, tuple(evidence), (),
                          f"volatility band {band}")


def score_event_risk(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_event_risk
    proximity = inputs.get("event_proximity")
    if proximity == "NO_KNOWN_EVENT":
        return ComponentScore(EVENT_RISK, cap, cap, SCORED,
                              ("no known stock-level event near this session",), (),
                              "NO_KNOWN_EVENT")
    if proximity == "EVENT_WITHIN_WINDOW":
        evidence = [f"{inputs.get('event_family')} event within "
                   f"{inputs.get('sessions_to_event')} trading session(s)"]
        # visibility over aggressive exclusion (owner spec section 21): flagged, not zeroed.
        return ComponentScore(EVENT_RISK, cap * 1 / 3, cap, SCORED, tuple(evidence), (),
                              "EVENT_WITHIN_WINDOW: flagged for review, not excluded")
    return ComponentScore(EVENT_RISK, 0.0, cap, NOT_SCORABLE, (), ("event_proximity",),
                          "event data unavailable for this symbol")


def score_data_quality_integrity(inputs: dict, policy: ScoringPolicy) -> ComponentScore:
    cap = policy.weight_data_quality_integrity
    matched = inputs.get("reconcile_status") == "MATCHED"
    warnings = inputs.get("detector_warnings") or ()
    if not matched:
        return ComponentScore(DATA_QUALITY_INTEGRITY, 0.0, cap, NOT_SCORABLE, (),
                              ("reconcile_status",),
                              f"replay status {inputs.get('reconcile_status')}")
    score = cap if not warnings else cap * 0.5
    evidence = ["replay MATCHED the recorded candidate"]
    if warnings:
        evidence.append(f"{len(warnings)} detector warning(s): " + "; ".join(warnings[:3]))
    return ComponentScore(DATA_QUALITY_INTEGRITY, score, cap, SCORED, tuple(evidence), (),
                          "MATCHED replay, demoted for detector warnings" if warnings
                          else "MATCHED replay, no detector warnings")


SCORERS = (score_setup_quality, score_market_alignment, score_sector_alignment,
          score_relative_strength, score_mtf_confirmation, score_range_position_and_extension,
          score_liquidity, score_volatility_fit, score_event_risk,
          score_data_quality_integrity)


def score_candidate(inputs: dict, policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> tuple:
    """Every component, in A-J order."""
    return tuple(scorer(inputs, policy) for scorer in SCORERS)


def total_score(components: tuple, policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> tuple:
    """`(score_total, score_max)` - `score_max` is always `policy.max_total` regardless of how
    many components are `NOT_SCORABLE` (never renormalized)."""
    total = sum(c.score for c in components)
    return total, policy.max_total


__all__ = ["score_candidate", "total_score", "SCORERS"]

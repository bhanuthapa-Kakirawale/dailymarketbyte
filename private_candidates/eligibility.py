"""Eligibility / data-quality state machine (owner spec sections 6-7).

Every Radar candidate for the session gets exactly one `ResearchPriority` - never deleted for
failing a trade-quality filter. Uses only fields confirmed to exist on the replayed detector
output; never invents a rejection rule to shrink the list artificially.
"""
from __future__ import annotations

from .models import DataQualityTier, ResearchPriority
from .policy import DEFAULT_SCORING_POLICY, ScoringPolicy

NOT_REPLAYED = "NOT_REPLAYED"
REASON_CODES_DIFFER = "REASON_CODES_DIFFER"
UNTRUSTED_REPLAY_STATUSES = (NOT_REPLAYED, REASON_CODES_DIFFER)


def data_quality(*, volume_family_active: bool, has_volume_anomaly: bool, has_technical: bool,
                 has_relative: bool, ohlcv_sessions: int, atr_available: bool,
                 policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """`(tier, reasons)`. INSUFFICIENT when an essential detector reading is missing or OHLCV
    depth is below the ATR floor; PARTIAL when only an optional reading (relative-performance,
    ATR) is missing; COMPLETE otherwise.

    `has_volume_anomaly=False` is the ORDINARY, expected state for any candidate whose Radar
    membership came from STRUCTURE + RELATIVE_PERFORMANCE alone (`VolumeAnomaly` is only ever
    constructed when a reading actually clears the ELEVATED threshold - `radar.thresholds
    .classify_anomaly`) - it is never, by itself, a data-quality gap. It only indicates a real
    gap when `volume_family_active` (the candidate's own recorded `active_families` includes
    VOLUME) and yet no anomaly reading came back from replay - an internal inconsistency, not a
    "no anomaly" result. `TechnicalStructure`/`RelativePerformance`, by contrast, are produced
    for EVERY symbol with sufficient OHLCV regardless of whether anything was flagged
    (`radar/relative.py`: "this detector describes context for every stock, not just
    outliers") - so their absence always does mean insufficient OHLCV for that detector."""
    volume_gap = volume_family_active and not has_volume_anomaly
    if volume_gap or not has_technical or ohlcv_sessions < policy.min_ohlcv_sessions:
        reasons = []
        if volume_gap:
            reasons.append("VOLUME family recorded as active but no volume-anomaly reading "
                          "came back from replay")
        if not has_technical:
            reasons.append("no technical-structure reading for this session")
        if ohlcv_sessions < policy.min_ohlcv_sessions:
            reasons.append(f"only {ohlcv_sessions} OHLCV session(s) available, "
                          f"{policy.min_ohlcv_sessions} required")
        return DataQualityTier.INSUFFICIENT, reasons
    missing = []
    if not has_relative:
        missing.append("no relative-performance reading for this session")
    if not atr_available:
        missing.append(f"insufficient OHLCV history for ATR{policy.atr_window}")
    tier = DataQualityTier.PARTIAL if missing else DataQualityTier.COMPLETE
    return tier, missing


def assess(*, reconcile_status: str, volume_family_active: bool, has_volume_anomaly: bool,
          has_technical: bool, has_relative: bool, ohlcv_sessions: int, atr_available: bool,
          score_total: float, score_max: float, extension_state: str, volatility_band: str,
          policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """`{"research_priority", "data_quality_tier", "eligibility_reasons", "flags"}`."""
    if reconcile_status in UNTRUSTED_REPLAY_STATUSES:
        return {"research_priority": ResearchPriority.DATA_INCOMPLETE.value,
               "data_quality_tier": DataQualityTier.INSUFFICIENT.value,
               "eligibility_reasons": (f"replay status {reconcile_status}: detector values for "
                                      "this candidate are not trusted",),
               "flags": ()}

    tier, missing_reasons = data_quality(
        volume_family_active=volume_family_active, has_volume_anomaly=has_volume_anomaly,
        has_technical=has_technical, has_relative=has_relative, ohlcv_sessions=ohlcv_sessions,
        atr_available=atr_available, policy=policy)
    if tier is DataQualityTier.INSUFFICIENT:
        return {"research_priority": ResearchPriority.DATA_INCOMPLETE.value,
               "data_quality_tier": tier.value, "eligibility_reasons": tuple(missing_reasons),
               "flags": ()}

    veto = (extension_state == policy.veto_extension_state
           and volatility_band == policy.veto_volatility_band)
    flags = ("COMPOUNDING_RISK_VETO",) if veto else ()
    reasons = list(missing_reasons)
    if veto:
        reasons.append(f"{policy.veto_extension_state} extension combined with "
                      f"{policy.veto_volatility_band} volatility")
        return {"research_priority": ResearchPriority.NOT_ACTIONABLE.value,
               "data_quality_tier": tier.value, "eligibility_reasons": tuple(reasons),
               "flags": flags}

    pct = (score_total / score_max * 100) if score_max else 0.0
    # A PARTIAL tier is capped at REVIEW regardless of score (owner spec section 7: "a high
    # technical score with poor data must not rank normally") - only COMPLETE data can reach
    # ELIGIBLE.
    if pct >= policy.eligible_floor and tier is DataQualityTier.COMPLETE:
        priority = ResearchPriority.ELIGIBLE
    elif pct >= policy.review_floor or tier is DataQualityTier.PARTIAL:
        priority = ResearchPriority.REVIEW
    else:
        priority = ResearchPriority.NOT_ACTIONABLE
    reasons.append(f"score {pct:.1f}/100 ({tier.value.lower()} data)")
    return {"research_priority": priority.value, "data_quality_tier": tier.value,
           "eligibility_reasons": tuple(reasons), "flags": flags}


__all__ = ["data_quality", "assess", "NOT_REPLAYED", "REASON_CODES_DIFFER",
          "UNTRUSTED_REPLAY_STATUSES"]

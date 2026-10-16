"""Deterministic, configurable weights/thresholds for Trade Candidate Intelligence V1.

Every number here is PROVISIONAL (owner spec section 5): a reasonable first-pass judgment call,
never derived from a backtest, never optimized against historical returns. Mirrors the house
style of `radar/thresholds.py` - one frozen dataclass per caller can tune without touching
scoring logic, and `score_policy_version` is bumped whenever a weight or threshold changes so a
stored `TradeCandidateAssessment` snapshot is never silently reinterpreted under a different
policy (`private_candidates/store.py` keys storage by this version).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringPolicy:
    score_policy_version: str = "trade-candidate-score-1.0"

    # Component weights (A-J). Must sum to `max_total` - enforced by a test, not at runtime,
    # so a deliberate change is caught immediately rather than silently shifting the scale.
    weight_setup_quality: float = 20.0
    weight_market_alignment: float = 12.0
    weight_sector_alignment: float = 10.0
    weight_relative_strength: float = 10.0
    weight_mtf_confirmation: float = 10.0
    weight_range_position_and_extension: float = 12.0
    weight_liquidity: float = 8.0
    weight_volatility_fit: float = 8.0
    weight_event_risk: float = 6.0
    weight_data_quality_integrity: float = 4.0
    max_total: float = 100.0

    # Eligibility bands (research_priority), read against score_total out of max_total.
    eligible_floor: float = 60.0
    review_floor: float = 35.0          # below this (and DATA_INCOMPLETE/veto aside) -> NOT_ACTIONABLE

    # Data completeness.
    min_ohlcv_sessions: int = 15        # floor for ATR14 (needs 14 prior + current)
    min_percentile_sample: int = 20     # mirrors radar.thresholds.VolumeThresholds

    # ATR / extension / structural reference (owner spec sections 9, 16-19).
    atr_window: int = 14
    extension_normal_atr: float = 1.5           # |distance from SMA20| / ATR14 below this -> NORMAL
    extension_stretched_atr: float = 3.0        # below this -> STRETCHED, else DEEPLY_STRETCHED
    pullback_at_structure_atr: float = 1.0       # distance to structural reference / ATR14
    pullback_near_structure_atr: float = 2.5
    risk_width_narrow_atr: float = 1.5
    risk_width_wide_atr: float = 3.0
    # Compounding-risk veto: DEEPLY_STRETCHED + EXTREME volatility together demotes to
    # NOT_ACTIONABLE regardless of score - never silent, always recorded as a flag.
    veto_extension_state: str = "DEEPLY_STRETCHED"
    veto_volatility_band: str = "EXTREME"

    # Weekly multi-timeframe (owner spec sections 10-11).
    weekly_min_completed_weeks: int = 10
    weekly_sma_window: int = 10

    # Liquidity bands (owner spec section 8): average 20-session turnover in INR crore
    # (average_volume_20 * close / 1e7). NIFTY 200 membership alone is explicitly NOT treated
    # as proof of liquidity - this reads only what the pipeline actually has (volume * price).
    liquidity_thin_below_cr: float = 5.0
    liquidity_deep_at_or_above_cr: float = 50.0

    # Volatility bands (owner spec section 9): ATR14 as a percent of close.
    volatility_tight_below_pct: float = 1.5
    volatility_normal_below_pct: float = 3.0
    volatility_elevated_below_pct: float = 6.0

    # Event proximity window (owner spec section 20), in TRADING sessions, not calendar days.
    event_window_sessions: int = 3

    # Diversified shortlist (owner spec sections 28-30).
    sector_cap: int = 2
    correlation_min_sample: int = 20
    correlation_window_sessions: int = 60
    correlation_high_threshold: float = 0.85

    # Regime streak (owner spec section 12): how far back to look for a consecutive-regime run.
    regime_streak_lookback: int = 30


DEFAULT_SCORING_POLICY = ScoringPolicy()


def component_weights(policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    from .models import (DATA_QUALITY_INTEGRITY, EVENT_RISK, LIQUIDITY, MARKET_ALIGNMENT,
                         MTF_CONFIRMATION, RANGE_POSITION_AND_EXTENSION, RELATIVE_STRENGTH,
                         SECTOR_ALIGNMENT, SETUP_QUALITY, VOLATILITY_FIT)
    return {
        SETUP_QUALITY: policy.weight_setup_quality,
        MARKET_ALIGNMENT: policy.weight_market_alignment,
        SECTOR_ALIGNMENT: policy.weight_sector_alignment,
        RELATIVE_STRENGTH: policy.weight_relative_strength,
        MTF_CONFIRMATION: policy.weight_mtf_confirmation,
        RANGE_POSITION_AND_EXTENSION: policy.weight_range_position_and_extension,
        LIQUIDITY: policy.weight_liquidity,
        VOLATILITY_FIT: policy.weight_volatility_fit,
        EVENT_RISK: policy.weight_event_risk,
        DATA_QUALITY_INTEGRITY: policy.weight_data_quality_integrity,
    }


__all__ = ["ScoringPolicy", "DEFAULT_SCORING_POLICY", "component_weights"]

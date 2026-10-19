"""Trade Candidate Intelligence V1 vocabulary and the packet it produces per (session, symbol).

Every enum here is a descriptive research label, never a BUY/SELL/target/stop-loss word - the
same discipline `radar/models.py` and `private_desk/packet.py` already hold. Field names are
chosen so none of them collides, as a standalone `_`-split token, with
`private_desk.packet.FORBIDDEN_FIELD_TOKENS` (checked explicitly by
`tests/test_private_candidates_isolation.py`): "entry"/"stop"/"target"/"exit" never appear as a
field-name word, which is why the entry-location concept below is called `range_position_pct`/
`extension_state`/`pullback_state` and the stop-reference concept is `structural_reference_*`/
`risk_width_label`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum

SCHEMA_VERSION = "private-trade-candidate-1.2"

# Informational flags (never change the score on their own).
# The session's own price move went against the Radar's multi-session direction - a pullback or
# bounce day; the components follow the Radar direction.
DAY_MOVE_AGAINST_RADAR_DIRECTION = "DAY_MOVE_AGAINST_RADAR_DIRECTION"
# The Radar's structure and relative evidence disagree (or set no direction): the day's move is
# used as the direction and the candidate is capped at REVIEW.
MIXED_DIRECTION_EVIDENCE = "MIXED_DIRECTION_EVIDENCE"
# The session falls in a quarterly results window and no results date for this quarter is on
# file - "no known event" may just be missing data.
RESULTS_DATE_NOT_ON_FILE = "RESULTS_DATE_NOT_ON_FILE"


class ResearchPriority(str, Enum):
    """Every Radar candidate for the session gets exactly one of these - never deleted for
    failing a trade-quality filter (owner rule: a Radar candidate is retained for auditability
    regardless of its research priority)."""
    ELIGIBLE = "ELIGIBLE"
    REVIEW = "REVIEW"
    NOT_ACTIONABLE = "NOT_ACTIONABLE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class DataQualityTier(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class MTFAlignment(str, Enum):
    ALIGNED = "ALIGNED"
    DIVERGENT = "DIVERGENT"
    WEEKLY_INSUFFICIENT_DATA = "WEEKLY_INSUFFICIENT_DATA"


class VolatilityBand(str, Enum):
    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"
    VOLATILITY_UNKNOWN = "VOLATILITY_UNKNOWN"


class LiquidityBand(str, Enum):
    THIN = "THIN"
    ADEQUATE = "ADEQUATE"
    DEEP = "DEEP"
    LIQUIDITY_UNKNOWN = "LIQUIDITY_UNKNOWN"


class EventProximity(str, Enum):
    NO_KNOWN_EVENT = "NO_KNOWN_EVENT"
    EVENT_WITHIN_WINDOW = "EVENT_WITHIN_WINDOW"
    EVENT_DATA_UNAVAILABLE = "EVENT_DATA_UNAVAILABLE"


class ExtensionState(str, Enum):
    NORMAL = "NORMAL"
    STRETCHED = "STRETCHED"
    DEEPLY_STRETCHED = "DEEPLY_STRETCHED"
    EXTENSION_UNKNOWN = "EXTENSION_UNKNOWN"


class PullbackState(str, Enum):
    AT_STRUCTURE = "AT_STRUCTURE"
    NEAR_STRUCTURE = "NEAR_STRUCTURE"
    EXTENDED_FROM_STRUCTURE = "EXTENDED_FROM_STRUCTURE"
    PULLBACK_UNKNOWN = "PULLBACK_UNKNOWN"


class RiskWidthLabel(str, Enum):
    NARROW = "NARROW"
    MODERATE = "MODERATE"
    WIDE = "WIDE"
    RISK_WIDTH_UNKNOWN = "RISK_WIDTH_UNKNOWN"


class RegimeAlignment(str, Enum):
    CANDIDATE_ALIGNS_WITH_REGIME = "CANDIDATE_ALIGNS_WITH_REGIME"
    CANDIDATE_AGAINST_REGIME = "CANDIDATE_AGAINST_REGIME"
    REGIME_NEUTRAL = "REGIME_NEUTRAL"


# Component identifiers (A-J) - also used as `ComponentScore.component` values.
SETUP_QUALITY = "SETUP_QUALITY"
MARKET_ALIGNMENT = "MARKET_ALIGNMENT"
SECTOR_ALIGNMENT = "SECTOR_ALIGNMENT"
RELATIVE_STRENGTH = "RELATIVE_STRENGTH"
MTF_CONFIRMATION = "MTF_CONFIRMATION"
RANGE_POSITION_AND_EXTENSION = "RANGE_POSITION_AND_EXTENSION"
LIQUIDITY = "LIQUIDITY"
VOLATILITY_FIT = "VOLATILITY_FIT"
EVENT_RISK = "EVENT_RISK"
DATA_QUALITY_INTEGRITY = "DATA_QUALITY_INTEGRITY"
ALL_COMPONENTS = (SETUP_QUALITY, MARKET_ALIGNMENT, SECTOR_ALIGNMENT, RELATIVE_STRENGTH,
                  MTF_CONFIRMATION, RANGE_POSITION_AND_EXTENSION, LIQUIDITY, VOLATILITY_FIT,
                  EVENT_RISK, DATA_QUALITY_INTEGRITY)

SCORED = "SCORED"
NOT_SCORABLE = "NOT_SCORABLE"


@dataclass(frozen=True)
class ComponentScore:
    """One scored (or explicitly not-scorable) component. Always present on every candidate,
    even at 0, so a low total is self-explanatory from `missing_data`/`reason` rather than
    hidden by renormalizing the denominator (`scoring.total_score` never rescales `max_score`)."""
    component: str
    score: float
    max_score: float
    status: str = SCORED              # SCORED | NOT_SCORABLE
    evidence: tuple = field(default_factory=tuple)
    missing_data: tuple = field(default_factory=tuple)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"component": self.component, "score": self.score, "max_score": self.max_score,
                "status": self.status, "evidence": list(self.evidence),
                "missing_data": list(self.missing_data), "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentScore":
        return cls(component=d["component"], score=d["score"], max_score=d["max_score"],
                  status=d.get("status", SCORED), evidence=tuple(d.get("evidence") or ()),
                  missing_data=tuple(d.get("missing_data") or ()), reason=d.get("reason", ""))


@dataclass(frozen=True)
class TradeCandidateAssessment:
    """One Radar candidate's trading-research assessment for one session - PRIVATE_ANALYTICS
    only, never a trade instruction. A new, independently-versioned sibling of
    `private_desk.packet.PrivateCandidatePacket` (see docs/PRIVATE_TRADE_CANDIDATES.md for why:
    bolting this onto the existing packet would force every existing consumer to absorb an
    unrelated schema bump and would blur two different audiences)."""
    schema_version: str
    score_policy_version: str
    session_date: str
    symbol: str
    company: str | None
    sector: str | None
    capture_mode: str                       # POINT_IN_TIME | RECONSTRUCTED
    # The PIT cutoff this assessment honors: the latest timestamp whose information was
    # eligible to be read (every per-subsystem read uses `retrieved_at <= candidate_as_of`,
    # with each subsystem's own inclusive/exclusive convention - see context.py). Distinct from
    # `captured_at` (below): for a live same-day capture the two are close but never the same
    # field, and for a replay/catch-up they can be far apart (docs/PRIVATE_TRADE_CANDIDATES.md
    # "Point-in-time capture contract").
    candidate_as_of: str
    captured_at: str                        # when THIS assessment object was physically computed

    research_priority: str
    eligibility_reasons: tuple
    data_quality_tier: str

    score_total: float
    score_max: float
    score_validation_status: str
    components: tuple                       # ComponentScore.to_dict() each

    radar_reason_codes: tuple
    radar_active_families: tuple

    range_position_pct: float | None
    extension_state: str
    pullback_state: str
    structural_reference_price: float | None
    # BROKEN_PRIOR_20/50_HIGH|LOW (the level a range break just cleared) or PRIOR_20_LOW|HIGH
    # (the far end of the prior 20-session range when no range break is in the setup direction)
    structural_reference_kind: str | None
    risk_width_label: str
    atr14: float | None
    atr14_pct: float | None

    weekly_alignment: str
    volatility_band: str
    liquidity_band: str

    event_proximity: str
    event_family: str | None
    sessions_to_event: int | None

    regime_label: str | None
    regime_streak_sessions: int | None
    regime_alignment: str | None

    sector_relative_20d_pp: float | None
    sector_rotation_context: str | None

    positive_evidence: tuple
    risk_evidence: tuple
    missing_context: tuple
    flags: tuple = field(default_factory=tuple)
    # The Radar's own stored `direction_compatibility` (STRUCTURE + RELATIVE evidence only:
    # ALIGNED_POSITIVE / ALIGNED_NEGATIVE / MIXED / NON_DIRECTIONAL). Policy 2.0 scores in this
    # direction; MIXED/NON_DIRECTIONAL fall back to the day's move. None before schema 1.1.
    radar_direction: str | None = None
    # The sector's own 20-session return minus NIFTY 50's (equal-weight series of the sector's
    # peers, the stock itself excluded) - what SECTOR_ALIGNMENT reads. None before schema 1.2.
    sector_vs_market_20d_pp: float | None = None
    notes: tuple = field(default_factory=lambda: (
        "Research priority, not a trade recommendation.",
        "Score is PROVISIONAL and has not been historically validated.",
        "structural_reference_price/risk_width_label describe distance to a prior price "
        "level for research context only - not an order of any kind. No profit target "
        "exists anywhere in this system, so this is never a reward-to-risk ratio."))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["components"] = [c if isinstance(c, dict) else c.to_dict() for c in self.components]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TradeCandidateAssessment":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        for tup_field in ("eligibility_reasons", "radar_reason_codes", "radar_active_families",
                         "positive_evidence", "risk_evidence", "missing_context", "flags",
                         "notes"):
            if tup_field in known:
                known[tup_field] = tuple(known[tup_field] or ())
        known["components"] = tuple(known.get("components") or ())
        return cls(**known)


def total_max_score() -> float:
    return 100.0


__all__ = ["SCHEMA_VERSION", "DAY_MOVE_AGAINST_RADAR_DIRECTION", "MIXED_DIRECTION_EVIDENCE",
          "RESULTS_DATE_NOT_ON_FILE", "ResearchPriority",
          "DataQualityTier", "MTFAlignment",
          "VolatilityBand", "LiquidityBand", "EventProximity", "ExtensionState", "PullbackState",
          "RiskWidthLabel", "RegimeAlignment", "SETUP_QUALITY", "MARKET_ALIGNMENT",
          "SECTOR_ALIGNMENT", "RELATIVE_STRENGTH", "MTF_CONFIRMATION",
          "RANGE_POSITION_AND_EXTENSION", "LIQUIDITY", "VOLATILITY_FIT", "EVENT_RISK",
          "DATA_QUALITY_INTEGRITY", "ALL_COMPONENTS", "SCORED", "NOT_SCORABLE", "ComponentScore",
          "TradeCandidateAssessment", "total_max_score"]

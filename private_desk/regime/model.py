"""Versioned output contract of the private market regime classifier.

`MarketRegimeSnapshot` describes the CURRENT market environment of one session - never a
forecast, never an instruction. It carries no order / trade field (test-enforced, same token
list as `private_desk.packet`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "private-market-regime-1.0"
# Bump on ANY change to a metric, threshold or rule: cached snapshots carry it and are rebuilt
# when it differs (private_desk/regime/store.py).
CALCULATION_VERSION = "regime-v1.0-provisional"

# regime labels
BULLISH, BEARISH, NEUTRAL, TRANSITIONAL, INSUFFICIENT_DATA = (
    "BULLISH", "BEARISH", "NEUTRAL", "TRANSITIONAL", "INSUFFICIENT_DATA")
REGIMES = (BULLISH, BEARISH, NEUTRAL, TRANSITIONAL, INSUFFICIENT_DATA)

# directional dimension states
POSITIVE, NEGATIVE, MIXED, UNAVAILABLE = "POSITIVE", "NEGATIVE", "MIXED", "UNAVAILABLE"
NEUTRAL_STATE = "NEUTRAL"
# volatility states
ELEVATED, RISING, FALLING, STABLE = "ELEVATED", "RISING", "FALLING", "STABLE"
# context-only states
NET_BUYERS, NET_SELLERS, BROAD, NARROW, BALANCED = (
    "NET_BUYERS", "NET_SELLERS", "BROAD", "NARROW", "BALANCED")

# roles
CORE, STRESS, CONTEXT = "CORE", "STRESS", "CONTEXT"


@dataclass(frozen=True)
class RegimeDimension:
    key: str                     # TREND / BREADTH / SECTORS / VOLUME / VOLATILITY / RELATIVE / FLOWS
    name: str                    # owner-facing name ("Index trend")
    role: str                    # CORE (decides) / STRESS (can block BULLISH) / CONTEXT (shown only)
    state: str                   # POSITIVE / NEGATIVE / MIXED / NEUTRAL / ELEVATED / ... / UNAVAILABLE
    facts: dict                  # the numbers the state was decided from
    rule: str                    # the exact rule text that produced `state`
    explanation: str             # one deterministic sentence built from `facts`
    source: str                  # where the facts came from
    data_as_of: str              # session the facts describe
    available: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RegimeDimension":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    schema_version: str
    calculation_version: str
    session_date: str
    regime: str
    candidate_regime: str            # this session's evidence alone, before the confirmation rule
    previous_candidate: str | None   # the previous session's candidate (confirmation input)
    rule_applied: str                # the regime rule that fired (text)
    reason_code: str                 # e.g. BOTH_PILLARS_POSITIVE / CONFLICT / UNCONFIRMED_CHANGE
    dimensions: tuple                # RegimeDimension, fixed display order
    supporting_evidence: tuple       # sentences: evidence agreeing with the regime
    conflicting_evidence: tuple      # sentences: evidence against it
    missing_dimensions: tuple        # keys of UNAVAILABLE dimensions
    explanation: str                 # WHY THIS REGIME - built only from the dimensions above
    universe: dict                   # label / members / membership source (+ backdated flag)
    generated_at: str
    notes: tuple = field(default_factory=tuple)

    def dimension(self, key: str) -> RegimeDimension | None:
        return next((d for d in self.dimensions if d.key == key), None)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dimensions"] = [x.to_dict() for x in self.dimensions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MarketRegimeSnapshot":
        vals = {k: d.get(k) for k in cls.__dataclass_fields__}
        vals["dimensions"] = tuple(RegimeDimension.from_dict(x) for x in d.get("dimensions") or [])
        for k in ("supporting_evidence", "conflicting_evidence", "missing_dimensions", "notes"):
            vals[k] = tuple(d.get(k) or ())
        return cls(**vals)

    def summary(self) -> dict:
        """Compact context (for the candidate packet / CSV): label + dimension states."""
        return {"label": self.regime, "session_date": self.session_date,
                "calculation_version": self.calculation_version,
                "dimensions": {x.key: x.state for x in self.dimensions}}


__all__ = ["RegimeDimension", "MarketRegimeSnapshot", "SCHEMA_VERSION", "CALCULATION_VERSION",
           "REGIMES", "BULLISH", "BEARISH", "NEUTRAL", "TRANSITIONAL", "INSUFFICIENT_DATA",
           "POSITIVE", "NEGATIVE", "MIXED", "NEUTRAL_STATE", "UNAVAILABLE", "ELEVATED", "RISING",
           "FALLING", "STABLE", "NET_BUYERS", "NET_SELLERS", "BROAD", "NARROW", "BALANCED",
           "CORE", "STRESS", "CONTEXT"]

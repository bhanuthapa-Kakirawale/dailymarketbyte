"""One persisted candidate-history row (Phase 4.2 Packet 5.4D) - the minimal novelty-relevant
snapshot of one composite Radar candidate on one session.

Deliberately NOT `radar.models.StockRadarCandidate` and does not import from `radar/` at all -
`storage/` stays radar-agnostic, mirroring `storage/ohlcv_models.py`/`storage/editorial_models.py`
(neither imports from `radar/` either). Reconstructing a `StockRadarCandidate`-compatible object
suitable for `radar.novelty.classify_history` is `radar/daily_pipeline.py`'s job, not this
module's - it is the caller that understands what `radar.novelty` actually reads.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value)


@dataclass(frozen=True)
class StoredCandidateState:
    """Exactly the fields `radar.novelty` reads off a `StockRadarCandidate` (packet spec
    section 11) - `active_families`, `reason_codes` (for structural-event extraction),
    `independent_signal_count`, `direction_compatibility`, `attention_level`,
    `persistence_state`. `price_change_pct` is carried for context/display only, never read by
    novelty classification itself. No OHLCV, no full detector snapshot."""
    session_date: dt.date
    instrument: str
    active_families: tuple
    reason_codes: tuple
    independent_signal_count: int
    direction_compatibility: str | None
    attention_level: str | None
    persistence_state: str | None
    price_change_pct: float | None
    calculation_version: str
    created_at: dt.datetime | None = None

    def to_dict(self) -> dict:
        return {
            "session_date": _iso(self.session_date), "instrument": self.instrument,
            "active_families": list(self.active_families), "reason_codes": list(self.reason_codes),
            "independent_signal_count": self.independent_signal_count,
            "direction_compatibility": self.direction_compatibility,
            "attention_level": self.attention_level, "persistence_state": self.persistence_state,
            "price_change_pct": self.price_change_pct,
            "calculation_version": self.calculation_version, "created_at": _iso(self.created_at),
        }


__all__ = ["StoredCandidateState"]

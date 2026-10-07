"""Which regime snapshots may enter research - the default gate for any future Phase 2 study.

A snapshot is RESEARCH ELIGIBLE only when:

1. it is classifiable (regime AND this session's candidate are not INSUFFICIENT_DATA);
2. its own universe is POINT_IN_TIME (`universe_quality`);
3. source alignment passes:
   - every membership list its classification read is POINT_IN_TIME. That covers the 10-session
     advance-share window of the session and of the previous session (confirmation rule) and
     both 5-session volume windows (`window_universe_quality`);
   - the constituent list is dated on or before the session;
   - every dimension describes exactly this session (`data_as_of`).

BACKDATED_UNIVERSE and UNKNOWN sessions are therefore EXCLUDED by default from forward-return
studies, MFE/MAE studies, regime-conditioned Radar performance, threshold optimisation and
strategy research, unless the historical universe is later recovered
(docs/PRIVATE_MARKET_REGIME.md). Nothing here changes a classification.
"""
from __future__ import annotations

from .data import POINT_IN_TIME
from .model import INSUFFICIENT_DATA, MarketRegimeSnapshot


def research_eligibility(snapshot: MarketRegimeSnapshot) -> tuple:
    """`(eligible, reasons)` - reasons lists every failed condition (empty when eligible)."""
    reasons = []
    if snapshot.regime == INSUFFICIENT_DATA or snapshot.candidate_regime == INSUFFICIENT_DATA:
        reasons.append("NOT_CLASSIFIABLE")
    if snapshot.universe_quality != POINT_IN_TIME:
        reasons.append(f"UNIVERSE_{snapshot.universe_quality}")
    u = snapshot.universe or {}
    if u.get("window_universe_quality") != POINT_IN_TIME:
        reasons.append("WINDOW_UNIVERSE_NOT_POINT_IN_TIME")
    src = u.get("universe_source_date")
    if not src or src > snapshot.session_date:
        reasons.append("UNIVERSE_SOURCE_AFTER_SESSION")
    if not snapshot.dimensions or any(d.data_as_of != snapshot.session_date
                                      for d in snapshot.dimensions):
        reasons.append("DIMENSION_SESSION_MISMATCH")
    return not reasons, reasons


def is_regime_research_eligible(snapshot: MarketRegimeSnapshot) -> bool:
    return research_eligibility(snapshot)[0]


__all__ = ["is_regime_research_eligible", "research_eligibility"]

"""Deterministic historical context derived from canonical history.

Answers "how does today compare with recent sessions?" using only today's MarketReport and
what is already persisted in MarketHistory. It acquires nothing, predicts nothing, and
recommends nothing: given the same report and the same database it produces the same output,
every time, because the calculations are rules rather than a model's opinion.

The result is DERIVED data. Canonical history stays immutable; the snapshot is written as its
own artifact and can be regenerated from the records it cites.
"""
from .engine import ARTIFACT_DIR, build_snapshot, describe, save_snapshot
from .history import HistoricalWindow, HistoryUnavailable
from .models import (CALCULATION_VERSION, ELIGIBLE_HISTORICAL_STATUSES,
                     INTELLIGENCE_SCHEMA_VERSION, MAX_DISPLAYED_INSIGHTS, InsightCategory,
                     IntelligenceInsight, IntelligenceSnapshot, Strength, is_eligible)

__all__ = ["build_snapshot", "save_snapshot", "describe", "ARTIFACT_DIR",
           "IntelligenceSnapshot", "IntelligenceInsight", "InsightCategory", "Strength",
           "HistoricalWindow", "HistoryUnavailable", "ELIGIBLE_HISTORICAL_STATUSES",
           "is_eligible", "INTELLIGENCE_SCHEMA_VERSION", "CALCULATION_VERSION",
           "MAX_DISPLAYED_INSIGHTS"]

"""Private market regime classifier V1 (docs/PRIVATE_MARKET_REGIME.md).

Describes the CURRENT market environment of one session as BULLISH / BEARISH / NEUTRAL /
TRANSITIONAL / INSUFFICIENT_DATA from explicit evidence dimensions, with the rule and the numbers
behind every label. Deterministic rules only - no model, no score, no forecast, no trade
language. Session d is classified from data dated <= d only (`RegimeData.until`).

    data.py     read-only, session-aligned inputs (OHLCV store, Market Structure membership,
                canonical-report context)
    metrics.py  market-level metrics of one session
    rules.py    thresholds, dimension states, regime rules, WHY sentences
    engine.py   candidate + one-session confirmation -> MarketRegimeSnapshot
    store.py    derived, versioned, rebuildable snapshot cache (private_desk/regime/)
    research.py distributions + historical validation artifacts (private_desk/regime_research/)

PRIVATE only: nothing in the DMB public pipeline imports it, and it never changes a Radar
candidate, its ranking or the attention set (context only).
"""
from .model import CALCULATION_VERSION, SCHEMA_VERSION, MarketRegimeSnapshot, RegimeDimension

__all__ = ["CALCULATION_VERSION", "SCHEMA_VERSION", "MarketRegimeSnapshot", "RegimeDimension"]

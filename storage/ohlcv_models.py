"""Raw OHLCV bar model for the local Yahoo cache (Phase 4.2 Packet 5.2).

An `OHLCVBar` is what a provider said about one symbol on one session - retrieved market
data, not a judgement. It is deliberately NOT a `core.Fact`/`core.Observation`/`MarketReport`
and never forced into those shapes: those model a validated, cross-checked *publication* claim
("the Nifty closed at X, corroborated by two independent sources"), while a bar is a single
provider's raw reading, stored so it does not have to be re-fetched. See docs/OHLCV_STORE.md
for how this relates to `market_history.db`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class QualityStatus(str, Enum):
    """Deterministic state of one stored bar - never silently implied by absence of a field.

    Mirrors the acquisition signatures `market.py` already distinguishes (see its
    `get_universe_relative_volume`/`get_universe_technical_series` skip-reason vocabulary):
    a row can be a complete reading (`OK`), a session Yahoo has published OHLV for but not yet
    backfilled Close on (`BACKFILL_PENDING`), a reading whose link to the expected prior
    session is broken (`DATE_GAP`), or an explicit record that nothing was available at all
    (`NO_DATA`) - distinct from simply never having attempted that symbol/session.
    """
    OK = "OK"
    BACKFILL_PENDING = "BACKFILL_PENDING"
    DATE_GAP = "DATE_GAP"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True)
class OHLCVBar:
    """One symbol's raw OHLCV reading for one session, from one provider.

    Frozen because this is a retrieved fact about what a provider returned at `retrieved_at` -
    it is never mutated in place. A later, more complete reading of the same
    (symbol, session_date, source) is a NEW `OHLCVBar` that replaces the stored row via
    `OHLCVStore.upsert_bars`, not an edit of this instance.
    """
    symbol: str
    session_date: dt.date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    source: str
    retrieved_at: dt.datetime
    quality_status: QualityStatus


__all__ = ["OHLCVBar", "QualityStatus"]

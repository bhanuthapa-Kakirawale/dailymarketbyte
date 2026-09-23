"""Universe-wide OHLC acquisition for the technical-structure detector (Phase 4.2 Packet 2).

Mirrors `radar/acquisition.py`'s split: acquisition (this module, talks to `market.py`, which
talks to yfinance) is kept separate from detection (`radar/technical.py`, pure and offline).

Unlike relative volume, the 20/50-session range and moving-average windows this detector needs
do not require canonical history to accumulate across days first - a single bulk OHLCV fetch
(`market.get_universe_technical_series`, the same "one download, whole universe" call
`get_universe_relative_volume` makes) already carries ~1 year of trailing sessions per symbol,
which is enough for every window this packet defines from the first run. So, unlike
`radar.acquisition.acquire_universe_scan_report`, nothing here is persisted to canonical
`MarketHistory` - there is no multi-day accumulation for this detector to depend on. See
docs/MARKET_INTELLIGENCE_RADAR.md.
"""
from __future__ import annotations

import datetime as dt

import market


def build_universe_technical_series(universe: dict, recap_date: dt.date, prev_date: dt.date,
                                    period: str = "1y"):
    """Thin wrapper over `market.get_universe_technical_series` - kept as its own function
    (rather than calling `market.py` directly from `radar/technical.py`) so the detector module
    stays free of any acquisition/network reference, exactly like `radar/acquisition.py` does
    for `radar/volume.py`.

    Returns `(series, skip_reasons)` - see `market.get_universe_technical_series` for the exact
    shape.
    """
    return market.get_universe_technical_series(universe, recap_date, prev_date, period=period)


__all__ = ["build_universe_technical_series"]

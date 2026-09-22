"""Yahoo Finance acquisition, wrapped. market.py still does the fetching underneath.

Yahoo is SECONDARY and its own independence group: every number here - the index session,
the movers, the sector moves, the global tiles - is one witness, no matter how many separate
calls produced it. `CrossSourceValidator` enforces that; this module just labels it honestly.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import market
from adapters.market_adapter import MarketAdapter
from core import Observation

from .base import Provider, ProviderResult

# Candle fields the chart draws. Stored in the report so that every number rendered on the
# chart - not just the headline close - is traceable to the report rather than to a
# DataFrame that only ever existed in memory.
CANDLE_FIELDS = ("open", "high", "low", "close", "ema20", "ema50")


@dataclass
class IndexSession:
    """One index's completed session: the typed payload that crosses the provider boundary.

    Deliberately not a bare dict - this is the structure the report's `nifty` and
    `technicals` sections are built from, and the presentation layer reads it back out, so
    its shape is a contract rather than an implementation detail.
    """
    session_date: dt.date
    prev_date: dt.date | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    previous_close: float | None = None
    change_points: float | None = None
    change_pct: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    rsi14: float | None = None
    bank_nifty_change_pct: float | None = None
    india_vix: float | None = None
    support: list = field(default_factory=list)
    resistance: list = field(default_factory=list)
    trendline: dict | None = None
    pivot: dict = field(default_factory=dict)
    candles: list = field(default_factory=list)

    @classmethod
    def from_analyze(cls, m: dict) -> IndexSession:
        """Build from market.analyze()'s dict - the one place that shape is read."""
        levels = m.get("levels") or {}
        return cls(
            session_date=m["recap_date"], prev_date=m.get("prev_date"),
            open=m.get("open"), high=m.get("high"), low=m.get("low"), close=m.get("close"),
            previous_close=m.get("prev"), change_points=m.get("chg"), change_pct=m.get("pct"),
            ema20=m.get("ema20"), ema50=m.get("ema50"), rsi14=m.get("rsi"),
            bank_nifty_change_pct=m.get("bank_pct"), india_vix=m.get("vix"),
            support=list(levels.get("sup", [])), resistance=list(levels.get("res", [])),
            trendline=m.get("trend"), pivot=dict(m.get("pivot") or {}),
            candles=_candles_from_frame(m.get("chart_df")),
        )

    def nifty_section(self) -> dict:
        return {"close": self.close, "change_points": self.change_points,
                "change_pct": self.change_pct, "open": self.open, "high": self.high,
                "low": self.low, "previous_close": self.previous_close,
                "bank_nifty_change_pct": self.bank_nifty_change_pct,
                "india_vix": self.india_vix}

    def technicals_section(self) -> dict:
        return {"ema20": self.ema20, "ema50": self.ema50, "rsi14": self.rsi14,
                "support": list(self.support), "resistance": list(self.resistance),
                "trendline": self.trendline, "pivot": dict(self.pivot),
                "candles": list(self.candles),
                "basis": "computed from Yahoo daily candles; no external source corroborates these"}


def _candles_from_frame(frame) -> list:
    """Flatten the chart DataFrame into JSON-native rows.

    The chart renders these, so under the Phase 2 presentation invariant they have to live
    in the report: a number on screen that exists only in an in-memory DataFrame is a number
    nobody can trace.
    """
    if frame is None or not len(frame):
        return []
    rows = []
    for stamp, row in frame.iterrows():
        entry = {"date": stamp.date().isoformat()}
        for field_name in CANDLE_FIELDS:
            column = {"ema20": "ema20", "ema50": "ema50"}.get(field_name, field_name.capitalize())
            value = row.get(column)
            entry[field_name] = None if value is None else float(value)
        rows.append(entry)
    return rows


class YahooProvider(Provider):
    name = "yahoo_finance"

    def _adapter(self) -> MarketAdapter:
        return MarketAdapter(self.session_date, self.retrieved_at, demo=self.demo,
                             report_date=self.report_date)

    def from_market_dict(self, m: dict) -> ProviderResult:
        """Observations + typed session payload from an already-fetched analyze() dict.

        Taking the dict as an argument rather than fetching it here keeps demo mode on the
        identical path: synthetic data becomes Observations through exactly the same code,
        so the demo exercises the real pipeline instead of a parallel one.
        """
        adapter = self._adapter()
        observations = adapter.observe_nifty(m) + adapter.observe_technicals(m)
        return ProviderResult(observations=observations,
                              payload={"index_session": IndexSession.from_analyze(m)})

    def fetch_session(self) -> ProviderResult:
        return self.from_market_dict(market.get_market())

    def movers(self, gainers: list, losers: list) -> ProviderResult:
        adapter = self._adapter()
        observations = (adapter.observe_movers(gainers, "gainer")
                        + adapter.observe_movers(losers, "loser"))
        return ProviderResult(observations=observations,
                              payload={"gainers": list(gainers), "losers": list(losers)})

    def sectors(self, sectors: list, nse_indices: dict) -> ProviderResult:
        return ProviderResult(observations=self._adapter().observe_sectors(sectors, nse_indices),
                              payload={"sectors": list(sectors)})

    def globals(self, tiles: list, ai_labels: frozenset = frozenset()) -> ProviderResult:
        return ProviderResult(observations=self._adapter().observe_globals(tiles, ai_labels=ai_labels),
                              payload={"tiles": list(tiles)})


__all__ = ["YahooProvider", "IndexSession", "CANDLE_FIELDS"]

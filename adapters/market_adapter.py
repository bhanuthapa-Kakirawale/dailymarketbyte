"""Translates market.py's plain dicts into canonical Observations.

market.py is untouched by this migration: it still returns the dicts the renderer expects.
The adapter reads those dicts and reconstructs the provenance the dicts throw away - which
source a sector number came from, whether a global tile was Yahoo or Gemini, and so on.

Where provenance genuinely cannot be recovered, it is left empty rather than guessed. An
honestly blank source field is a Phase 2 work item; an invented one is a lie in an audit trail.
"""
from __future__ import annotations

import datetime as dt

from core import Metric, Observation, SourceType
from core.models import (UNIT_INR, UNIT_INR_CRORE, UNIT_PERCENT, UNIT_POINTS, UNIT_RATIO,
                         UNIT_USD)

# Source names are stable identifiers, not display strings. Keep them lowercase and terse.
SRC_YAHOO = "yahoo_finance"
SRC_NSE = "nse_website"
SRC_DEMO = "demo_fixture"

# Which global tiles market.get_globals() produces, and how to classify each.
_GLOBAL_METRICS = {
    "DOW JONES": (Metric.INDEX_LEVEL, UNIT_POINTS),
    "NASDAQ": (Metric.INDEX_LEVEL, UNIT_POINTS),
    "USD / INR": (Metric.FX_RATE, UNIT_INR),
    "GOLD": (Metric.COMMODITY_PRICE, UNIT_USD),
    "BRENT CRUDE": (Metric.COMMODITY_PRICE, UNIT_USD),
    "GIFT NIFTY": (Metric.INDEX_LEVEL, UNIT_POINTS),
}

# Technical readings this pipeline computes itself; all DERIVED, none critical.
_TECHNICAL_KEYS = ("ema20", "ema50", "rsi")

# Tiles that are a reading of *this morning*, not of the session being recapped. GIFT Nifty
# is an indicative pre-open level fetched on the report date; dating it to the recap session
# would both misdate it and stop it deduplicating against the same value from news_adapter.
_REPORT_DATE_LABELS = frozenset({"GIFT NIFTY"})


def _relative_volume_definition() -> dict:
    """The versioned relative-volume semantics, attached to every such observation.

    Carried on the observation rather than assumed, because reports written before Phase 3
    used a 10-session window and are not rewritten: a reader has to be able to tell which
    definition produced the number in front of them.
    """
    try:
        import market
        return dict(market.RELATIVE_VOLUME_DEFINITION)
    except Exception:
        return {}


def _sector_nse_names() -> dict:
    """Label -> NSE index name, mirroring market.SECTORS so we can tell which sector rows
    came from NSE's feed and which fell back to Yahoo."""
    try:
        import market
        return {label: nse_name for label, nse_name, _ in market.SECTORS}
    except Exception:
        return {}


class MarketAdapter:
    """Builds Observations for one session from the dicts market.py already returned.

    `demo` relabels every source as the demo fixture, so synthetic numbers can never be
    mistaken for real ones in an archived report - the same discipline the on-screen
    "DEMO DATA - NOT REAL" badge applies to the video.
    """

    def __init__(self, session_date: dt.date, retrieved_at: dt.datetime, demo: bool = False,
                 report_date: dt.date | None = None):
        self.session_date = session_date
        self.report_date = report_date or session_date
        self.retrieved_at = retrieved_at
        self.demo = demo
        self._sector_nse = _sector_nse_names()

    # ------------------------------------------------------------------ helpers
    def _src(self, name: str, kind: SourceType) -> tuple[str, SourceType]:
        return (SRC_DEMO, SourceType.DERIVED) if self.demo else (name, kind)

    def _obs(self, metric: Metric, instrument: str, value, unit: str,
             source_name: str, source_type: SourceType,
             market_date: dt.date | None = None, **meta) -> Observation | None:
        if value is None:
            return None
        name, kind = self._src(source_name, source_type)
        return Observation(metric=metric, instrument=instrument, value=float(value), unit=unit,
                           market_date=market_date or self.session_date,
                           source_name=name, source_type=kind,
                           retrieved_at=self.retrieved_at, metadata=meta)

    # ------------------------------------------------------------------ index
    def observe_nifty(self, m: dict) -> list[Observation]:
        """Yahoo's view of the session: the numbers the video actually displays."""
        out = [
            self._obs(Metric.INDEX_CLOSE, "NIFTY 50", m.get("close"), UNIT_POINTS,
                      SRC_YAHOO, SourceType.SECONDARY, ticker="^NSEI"),
            self._obs(Metric.INDEX_CHANGE_PCT, "NIFTY 50", m.get("pct"), UNIT_PERCENT,
                      SRC_YAHOO, SourceType.SECONDARY, ticker="^NSEI",
                      previous_close=m.get("prev"), change_points=m.get("chg")),
            self._obs(Metric.INDEX_CHANGE_PCT, "BANK NIFTY", m.get("bank_pct"), UNIT_PERCENT,
                      SRC_YAHOO, SourceType.SECONDARY, ticker="^NSEBANK"),
            self._obs(Metric.VOLATILITY_INDEX, "INDIA VIX", m.get("vix"), UNIT_POINTS,
                      SRC_YAHOO, SourceType.SECONDARY, ticker="^INDIAVIX"),
        ]
        return [o for o in out if o]

    def observe_nse_indices(self, nse_idx: dict) -> list[Observation]:
        """NSE's own numbers - the primary source, present only when NSE did not block us.

        nse_all_indices() already refused to return anything whose timestamp was not the
        recap session, so reaching here means the date check upstream passed.
        """
        row = (nse_idx or {}).get("NIFTY 50")
        if not row:
            return []
        out = [
            self._obs(Metric.INDEX_CLOSE, "NIFTY 50", row.get("last"), UNIT_POINTS,
                      SRC_NSE, SourceType.PRIMARY, endpoint="/api/allIndices"),
            self._obs(Metric.INDEX_CHANGE_PCT, "NIFTY 50", row.get("pct"), UNIT_PERCENT,
                      SRC_NSE, SourceType.PRIMARY, endpoint="/api/allIndices"),
        ]
        return [o for o in out if o]

    # ------------------------------------------------------------------ derived technicals
    def observe_technicals(self, m: dict) -> list[Observation]:
        """EMA/RSI/pivots are computed here from Yahoo candles, so they are DERIVED: no
        external source vouches for them and none can corroborate them."""
        out = []
        for key in _TECHNICAL_KEYS:
            out.append(self._obs(Metric.TECHNICAL_LEVEL, f"NIFTY 50 {key.upper()}", m.get(key),
                                 UNIT_POINTS if key != "rsi" else UNIT_RATIO,
                                 SRC_YAHOO, SourceType.DERIVED, indicator=key,
                                 computed_from="^NSEI daily candles"))
        pivot = m.get("pivot") or {}
        for key, value in pivot.items():
            out.append(self._obs(Metric.TECHNICAL_LEVEL, f"NIFTY 50 PIVOT {key}", value,
                                 UNIT_POINTS, SRC_YAHOO, SourceType.DERIVED,
                                 indicator=f"floor_pivot_{key}"))
        return [o for o in out if o]

    # ------------------------------------------------------------------ globals
    def observe_globals(self, tiles: list, ai_labels: frozenset = frozenset()) -> list[Observation]:
        """Global cue tiles. `ai_labels` names the tiles that main.py inserted from the
        Gemini fact set (GIFT Nifty, Brent) - everything else came from Yahoo.

        A tile whose label matches neither is left unobserved rather than attributed to a
        source we cannot name.
        """
        out = []
        for tile in tiles or []:
            label = tile.get("label")
            spec = _GLOBAL_METRICS.get(label)
            if not spec:
                continue
            metric, unit = spec
            if label in ai_labels:
                source, kind = "gemini", SourceType.AI
            else:
                source, kind = SRC_YAHOO, SourceType.SECONDARY
            when = self.report_date if label in _REPORT_DATE_LABELS else self.session_date
            out.append(self._obs(metric, label, tile.get("value"), unit, source, kind,
                                 market_date=when, change_pct=tile.get("pct")))
        return [o for o in out if o]

    # ------------------------------------------------------------------ sectors
    def observe_sectors(self, sectors: list, nse_idx: dict) -> list[Observation]:
        """get_sectors() prefers NSE and falls back to Yahoo per sector; that choice is not
        recorded in its output, so it is reconstructed here with the same rule it used."""
        nse_idx = nse_idx or {}
        out = []
        for row in sectors or []:
            label = row.get("name")
            from_nse = self._sector_nse.get(label) in nse_idx
            source, kind = ((SRC_NSE, SourceType.PRIMARY) if from_nse
                            else (SRC_YAHOO, SourceType.SECONDARY))
            out.append(self._obs(Metric.SECTOR_CHANGE_PCT, label, row.get("pct"), UNIT_PERCENT,
                                 source, kind))
        return [o for o in out if o]

    # ------------------------------------------------------------------ flows
    def observe_fii_dii(self, fd: dict) -> list[Observation]:
        """Institutional flows. fd["source"] is the one place the legacy pipeline already
        kept provenance: "NSE" for the exchange feed, "web" for the Gemini fallback."""
        if not fd:
            return []
        tag = str(fd.get("source", "")).lower()
        if tag == "nse":
            source, kind = SRC_NSE, SourceType.PRIMARY
        elif tag in ("web", "gemini", "ai"):
            source, kind = "gemini", SourceType.AI
        else:
            source, kind = SRC_DEMO, SourceType.DERIVED
        out = [
            self._obs(Metric.FII_NET_CASH, "FII", fd.get("fii"), UNIT_INR_CRORE, source, kind,
                      basis="net cash market, provisional"),
            self._obs(Metric.DII_NET_CASH, "DII", fd.get("dii"), UNIT_INR_CRORE, source, kind,
                      basis="net cash market, provisional"),
        ]
        return [o for o in out if o]

    # ------------------------------------------------------------------ movers
    def observe_movers(self, rows: list, bucket: str) -> list[Observation]:
        """Top gainers/losers. get_movers() already dropped any stock whose own previous
        close did not land on the expected previous session, so survivors are date-aligned."""
        out = []
        for row in rows or []:
            symbol = row.get("symbol")
            common = {"bucket": bucket, "company": row.get("name")}
            out += [
                self._obs(Metric.STOCK_CLOSE, symbol, row.get("close"), UNIT_INR,
                          SRC_YAHOO, SourceType.SECONDARY, **common),
                self._obs(Metric.STOCK_CHANGE_PCT, symbol, row.get("pct"), UNIT_PERCENT,
                          SRC_YAHOO, SourceType.SECONDARY, **common),
                self._obs(Metric.STOCK_RELATIVE_VOLUME, symbol, row.get("volx"), UNIT_RATIO,
                          SRC_YAHOO, SourceType.DERIVED,
                          **_relative_volume_definition(), **common),
            ]
        return [o for o in out if o]


__all__ = ["MarketAdapter", "SRC_YAHOO", "SRC_NSE", "SRC_DEMO"]

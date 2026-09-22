"""Turns a validated MarketReport into the structures the existing renderer already accepts.

This is the presentation boundary, and it enforces the Phase 2 invariant:

    IF A NUMBER IS DISPLAYED IN THE VIDEO, THAT NUMBER EXISTS IN THE MARKETREPORT.

Nothing here fetches data and nothing here computes a market fact. Reshaping report sections
into the dict/list shapes video.py and chart.py expect is presentation work; deriving a new
market number would not be, and would silently put something on screen that no validator
ever saw. Formatting (digit grouping, label text, chip strings) stays downstream in video.py.

video.py and chart.py are deliberately untouched by Phase 2 - the adapter meets them where
they already are.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from core import MarketReport


class ReportPresentation:
    """Renderer-facing view of one report.

    The attribute names (`m`, `tiles`, `fd`, `sec`, `gainers`, `losers`, `events`) match what
    main.py's caption builders and the video scenes have always consumed, so the renderer
    needs no changes to read from the report instead of from provider output.
    """

    def __init__(self, report: MarketReport):
        self.report = report
        self.session_date: dt.date = report.session_date or report.report_date
        self.m = self._market()
        self.tiles = self._tiles()
        self.fd = self._flows()
        self.sec = self._sectors()
        self.gainers = self._movers(report.gainers)
        self.losers = self._movers(report.losers)
        self.events = self._events()
        self.nifty_reason = report.nifty.get("move_summary") or ""

    # ------------------------------------------------------------------ nifty + technicals
    def _market(self) -> dict:
        n, t = self.report.nifty, self.report.technicals
        meta = self.report.metadata or {}
        prev = meta.get("previous_session_date")
        return {
            "recap_date": self.session_date,
            "prev_date": dt.date.fromisoformat(prev) if prev else None,
            "open": n.get("open"), "high": n.get("high"), "low": n.get("low"),
            "close": n.get("close"), "prev": n.get("previous_close"),
            "chg": n.get("change_points"), "pct": n.get("change_pct"),
            "ema20": t.get("ema20"), "ema50": t.get("ema50"), "rsi": t.get("rsi14"),
            "bank_pct": n.get("bank_nifty_change_pct"), "vix": n.get("india_vix"),
            "levels": {"res": list(t.get("resistance") or []), "sup": list(t.get("support") or [])},
            "trend": t.get("trendline"), "pivot": dict(t.get("pivot") or {}),
            "chart_df": self.chart_frame(),
        }

    def chart_frame(self) -> pd.DataFrame:
        """Rebuild the candle frame chart.py draws, from the report's stored series.

        The chart renders these values, so they live in the report rather than in a
        DataFrame that only ever existed in memory - that is what makes every number on the
        chart traceable. Reconstructing the frame is reshaping, not recomputation: no value
        is derived here that is not already in the report.
        """
        candles = (self.report.technicals or {}).get("candles") or []
        if not candles:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "ema20", "ema50"])
        frame = pd.DataFrame([{
            "Open": c.get("open"), "High": c.get("high"), "Low": c.get("low"),
            "Close": c.get("close"), "ema20": c.get("ema20"), "ema50": c.get("ema50"),
        } for c in candles])
        frame.index = pd.to_datetime([c["date"] for c in candles])
        return frame

    # ------------------------------------------------------------------ other sections
    def _tiles(self) -> list:
        return [{"label": t.get("label"), "value": t.get("value"), "pct": t.get("change_pct"),
                 "dec": t.get("decimals", 0), "prefix": t.get("prefix", "")}
                for t in self.report.global_cues or []
                if t.get("value") is not None and t.get("change_pct") is not None]

    def _flows(self) -> dict | None:
        flows = self.report.institutional_flows or {}
        if flows.get("fii_net_cash_cr") is None or flows.get("dii_net_cash_cr") is None:
            return None
        return {"fii": flows["fii_net_cash_cr"], "dii": flows["dii_net_cash_cr"],
                "source": flows.get("legacy_source_tag")}

    def _sectors(self) -> list:
        return [{"name": s.get("name"), "pct": s.get("change_pct")}
                for s in self.report.sectors or [] if s.get("change_pct") is not None]

    def _movers(self, rows: list) -> list:
        out = []
        for row in rows or []:
            catalyst = row.get("catalyst") or {}
            out.append({"symbol": row.get("symbol"), "name": row.get("name"),
                        "close": row.get("close"), "pct": row.get("change_pct"),
                        "volx": row.get("relative_volume"),
                        "reason": catalyst.get("text", "")})
        return out

    def _events(self) -> list:
        return [{"tag": e.get("tag", ""), "text": e.get("text", "")}
                for e in self.report.events or [] if e.get("text")]

    # ------------------------------------------------------------------ scene selection
    def present(self) -> set:
        """Which optional scenes have enough data, using the report's own contents."""
        present = {"intro", "nifty", "gainers", "losers", "events", "outro"}
        if len(self.tiles) >= 3:
            present.add("global")
        if self.fd:
            present.add("fii")
        if len(self.sec) >= 6:
            present.add("sector")
        return present

    def displayed_numbers(self) -> dict:
        """Every market number this presentation will put on screen, labelled.

        Used by the presentation-invariant test to assert that nothing displayed originates
        outside the report.
        """
        out = {}
        for key in ("close", "chg", "pct", "high", "low", "bank_pct", "vix"):
            if self.m.get(key) is not None:
                out[f"nifty.{key}"] = self.m[key]
        for tile in self.tiles:
            out[f"tile.{tile['label']}.value"] = tile["value"]
            out[f"tile.{tile['label']}.pct"] = tile["pct"]
        for sector in self.sec:
            out[f"sector.{sector['name']}"] = sector["pct"]
        for bucket, rows in (("gainer", self.gainers), ("loser", self.losers)):
            for row in rows:
                out[f"{bucket}.{row['symbol']}.close"] = row["close"]
                out[f"{bucket}.{row['symbol']}.pct"] = row["pct"]
        if self.fd:
            out["flows.fii"] = self.fd["fii"]
            out["flows.dii"] = self.fd["dii"]
        return out


__all__ = ["ReportPresentation"]

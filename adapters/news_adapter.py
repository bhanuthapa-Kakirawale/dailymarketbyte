"""Translates news.ai_pass()'s numeric fact set into canonical AI Observations.

Everything Gemini returns is SourceType.AI, without exception. That single classification is
what lets validation refuse to mark a critical number VERIFIED on an LLM's say-so, while
still letting the same number corroborate a Yahoo or NSE reading.

Catalyst and event provenance used to be reconstructed here by string-matching finished text
against candidate headlines. Phase 2 removed that: news.ai_pass now records which branch
produced each reason and event as it happens, and providers/news.py carries it through, so
there is nothing left to infer.
"""
from __future__ import annotations

import datetime as dt

from core import Metric, Observation, SourceType
from core.models import UNIT_INR_CRORE, UNIT_POINTS, UNIT_USD

SRC_GEMINI = "gemini"
SRC_GOOGLE_NEWS = "google_news_rss"
SRC_DEMO = "demo_fixture"

# The exact string news.ai_pass() falls back to when no catalyst could be established.
NO_CATALYST_TEXT = "No major company-specific news; moved with sector trend."


class NewsAdapter:
    """Observations for the AI-sourced numeric facts of one session."""

    def __init__(self, session_date: dt.date, report_date: dt.date,
                 retrieved_at: dt.datetime, demo: bool = False):
        self.session_date = session_date
        self.report_date = report_date          # GIFT Nifty is a *today* reading, not the session's
        self.retrieved_at = retrieved_at
        self.demo = demo

    def _obs(self, metric: Metric, instrument: str, value, unit: str,
             market_date: dt.date, **meta) -> Observation | None:
        if value is None:
            return None
        name, kind = ((SRC_DEMO, SourceType.DERIVED) if self.demo
                      else (SRC_GEMINI, SourceType.AI))
        return Observation(metric=metric, instrument=instrument, value=float(value), unit=unit,
                           market_date=market_date, source_name=name, source_type=kind,
                           retrieved_at=self.retrieved_at, metadata=meta)

    # ------------------------------------------------------------------ numeric facts
    def observe_facts(self, facts: dict) -> list[Observation]:
        """The validated Gemini fact set.

        `nifty_close` is the important one: it is the independent second opinion main.py
        already used for its cross-check gate, and recording it here is what turns that
        previously print-and-discard comparison into an auditable ValidationResult.
        """
        facts = facts or {}
        out = []

        if facts.get("nifty_close") is not None:
            out.append(self._obs(Metric.INDEX_CLOSE, "NIFTY 50", facts["nifty_close"],
                                 UNIT_POINTS, self.session_date,
                                 role="independent cross-check of the Yahoo close",
                                 grounding="google_search"))

        gift = facts.get("gift") or {}
        if gift.get("value") is not None:
            out.append(self._obs(Metric.INDEX_LEVEL, "GIFT NIFTY", gift["value"], UNIT_POINTS,
                                 self.report_date, change_pct=gift.get("pct"),
                                 grounding="google_search",
                                 note="indicative pre-open level for the report date"))

        brent = facts.get("brent") or {}
        if brent.get("value") is not None:
            out.append(self._obs(Metric.COMMODITY_PRICE, "BRENT CRUDE", brent["value"], UNIT_USD,
                                 self.session_date, change_pct=brent.get("pct"),
                                 grounding="google_search",
                                 note="sourced here because yfinance BZ=F was verified unreliable"))

        fd = facts.get("fii_dii") or {}
        if fd.get("fii") is not None:
            out.append(self._obs(Metric.FII_NET_CASH, "FII", fd["fii"], UNIT_INR_CRORE,
                                 self.session_date, basis="net cash market, provisional"))
        if fd.get("dii") is not None:
            out.append(self._obs(Metric.DII_NET_CASH, "DII", fd["dii"], UNIT_INR_CRORE,
                                 self.session_date, basis="net cash market, provisional"))

        return [o for o in out if o]


__all__ = ["NewsAdapter", "SRC_GEMINI", "SRC_GOOGLE_NEWS", "NO_CATALYST_TEXT"]

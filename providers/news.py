"""Narrative acquisition (catalysts and events), wrapped around news.ai_pass.

The important Phase 2 change is that provenance now arrives *with* the text. news.ai_pass
records which branch produced each reason and event at the moment it takes that branch, so
nothing downstream has to guess by comparing a finished sentence against candidate
headlines - a guess that was always a reconstruction rather than a record.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import news
from core import SourceType
from core.sources import (SRC_DEMO, SRC_GEMINI, SRC_GOOGLE_NEWS, SRC_RULE_EXPIRY,
                          news_source, source_metadata)

from .base import Provider, ProviderResult

# How an acquisition origin maps onto a source identity and a catalyst classification.
_ORIGIN_SOURCE = {
    news.ORIGIN_GEMINI: SRC_GEMINI,
    news.ORIGIN_GEMINI_SEARCH: SRC_GEMINI,
    news.ORIGIN_GOOGLE_NEWS: SRC_GOOGLE_NEWS,
    news.ORIGIN_RULE_EXPIRY: SRC_RULE_EXPIRY,
    news.ORIGIN_RULE_PLACEHOLDER: SRC_RULE_EXPIRY,
    news.ORIGIN_FIXTURE: SRC_DEMO,
}


def _source_identity(origin: str, publisher: str | None) -> tuple:
    """(source_name, source_type, independence_group) for a narrative item.

    A Google News item is attributed to its publisher, not to Google: two syndications of
    one wire story must not look like two independent confirmations of a catalyst.
    """
    if origin == news.ORIGIN_NONE:
        return None, None, None
    if origin == news.ORIGIN_GOOGLE_NEWS:
        meta = news_source(publisher)
        return meta.source_name, meta.source_type.value, meta.independence_group
    name = _ORIGIN_SOURCE.get(origin)
    if not name:
        return None, None, None
    meta = source_metadata(name)
    return meta.source_name, meta.source_type.value, meta.independence_group


@dataclass
class Catalyst:
    """Why one stock moved, plus where that explanation came from."""
    symbol: str
    text: str
    origin: str
    publisher: str | None = None
    headline_date: str | None = None

    @property
    def catalyst_type(self) -> str:
        return ("NO_VERIFIED_CATALYST" if self.origin == news.ORIGIN_NONE
                else "POSSIBLE_CATALYST")

    def to_dict(self) -> dict:
        source_name, source_type, group = _source_identity(self.origin, self.publisher)
        return {"type": self.catalyst_type, "text": self.text, "origin": self.origin,
                "source": source_name, "source_type": source_type,
                "independence_group": group, "publisher": self.publisher,
                "headline_date": self.headline_date,
                # Phase 2: recorded by the acquisition layer, no longer reconstructed.
                "inferred": False}


@dataclass
class EventItem:
    """One scheduled event and its origin."""
    tag: str
    text: str
    origin: str
    publisher: str | None = None

    def to_dict(self) -> dict:
        source_name, source_type, group = _source_identity(self.origin, self.publisher)
        return {"tag": self.tag, "text": self.text, "origin": self.origin,
                "source": source_name, "source_type": source_type,
                "independence_group": group, "publisher": self.publisher,
                "provenance_resolved": source_name is not None}


@dataclass
class NarrativeBundle:
    """Everything the AI/news layer produced for one session, with provenance attached."""
    nifty_reason: str = ""
    nifty_reason_origin: str = news.ORIGIN_NONE
    nifty_reason_publisher: str | None = None
    catalysts: dict = field(default_factory=dict)          # symbol -> Catalyst
    events: list = field(default_factory=list)             # list[EventItem]
    ai_facts: dict = field(default_factory=dict)

    def nifty_reason_dict(self) -> dict:
        source_name, source_type, group = _source_identity(self.nifty_reason_origin,
                                                           self.nifty_reason_publisher)
        return {"text": self.nifty_reason, "origin": self.nifty_reason_origin,
                "source": source_name, "source_type": source_type,
                "independence_group": group, "publisher": self.nifty_reason_publisher}


class NewsProvider(Provider):
    name = "news"

    def from_ai_pass(self, nifty_reason, events: list, gainers: list, losers: list,
                     ai_facts: dict) -> ProviderResult:
        """Wrap one ai_pass() result into typed, provenance-carrying objects.

        Accepts the already-obtained result rather than calling ai_pass itself so demo mode
        travels the same path, and so the single Gemini request per run stays single.
        """
        default_origin = news.ORIGIN_FIXTURE if self.demo else news.ORIGIN_NONE

        if isinstance(nifty_reason, dict):
            text = nifty_reason.get("text", "")
            origin = nifty_reason.get("source") or default_origin
            publisher = nifty_reason.get("publisher")
        else:                                   # demo fixtures pass a plain string
            text, publisher = (nifty_reason or ""), None
            origin = default_origin if text else news.ORIGIN_NONE

        catalysts = {}
        for row in list(gainers or []) + list(losers or []):
            recorded = row.get("reason_source")
            catalysts[row.get("symbol")] = Catalyst(
                symbol=row.get("symbol"), text=row.get("reason", ""),
                origin=recorded or (default_origin if row.get("reason") else news.ORIGIN_NONE),
                publisher=row.get("reason_publisher"),
                headline_date=row.get("reason_headline_date"))

        items = [EventItem(tag=e.get("tag", ""), text=e.get("text", ""),
                           origin=e.get("source") or default_origin,
                           publisher=e.get("publisher"))
                 for e in events or []]

        bundle = NarrativeBundle(nifty_reason=text, nifty_reason_origin=origin,
                                 nifty_reason_publisher=publisher, catalysts=catalysts,
                                 events=items, ai_facts=ai_facts or {})
        return ProviderResult(payload={"narrative": bundle})


__all__ = ["NewsProvider", "NarrativeBundle", "Catalyst", "EventItem"]

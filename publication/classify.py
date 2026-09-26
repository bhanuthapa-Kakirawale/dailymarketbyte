"""Classifiers: the existing product's content, expressed as `PublishableFact`s.

Each builder is called where the content is assembled (the planner / storyboard), because only
there is it still known what a statement is about and where it came from. Nothing here parses
rendered text to guess a classification.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from core import sources as S
from core.enums import SourceType

from .classification import (ContentClass, Orientation, Origin, PublishableFact, RightsStatus,
                             Scope)
from .rights import rights_for

SOURCE_LABELS = {
    S.SRC_NSE: "NSE", S.SRC_NSE_ARCHIVE: "NSE", S.SRC_NSE_CONSTITUENTS: "NSE Indices",
    S.SRC_NSE_FO_BAN: "NSE", S.SRC_NSE_SURVEILLANCE: "NSE", S.SRC_NSE_IPO: "NSE",
    S.SRC_YAHOO: "Yahoo Finance", S.SRC_SEBI_OFFER_DOC: "SEBI offer document",
    S.SRC_RBI_PRESS: "RBI", S.SRC_FED_CALENDAR: "US Federal Reserve",
    S.SRC_RULE_EXPIRY: "NSE expiry rule", S.SRC_DERIVED: "Daily Market Byte calculation",
    S.SRC_MARKET_STRUCTURE: "Daily Market Byte count", S.SRC_NSEIX_LIVE: "NSE IX",
    S.SRC_NSEIX_DSP: "NSE IX", S.SRC_GEMINI: "AI", S.SRC_GOOGLE_NEWS: "news headline",
    S.SRC_DEMO: "synthetic fixture",
}
_RANK = {RightsStatus.APPROVED: 0, RightsStatus.REVIEW_REQUIRED: 1, RightsStatus.UNKNOWN: 2,
         RightsStatus.RESTRICTED: 3}


def source_label(names) -> str:
    labels = []
    for n in names:
        lab = SOURCE_LABELS.get(n, n)
        if lab not in labels:
            labels.append(lab)
    return " · ".join(labels)


def strictest_rights(names) -> RightsStatus:
    statuses = [rights_for(n).status for n in names] or [RightsStatus.UNKNOWN]
    return max(statuses, key=lambda s: _RANK[s])


def origin_for(source_name: str) -> Origin:
    meta = S.source_metadata(source_name)
    if meta.source_type is SourceType.AI:
        return Origin.AI
    if meta.source_type is SourceType.NEWS:
        return Origin.NEWS
    if source_name in (S.SRC_DERIVED, S.SRC_MARKET_STRUCTURE, S.SRC_RULE_EXPIRY):
        return Origin.INTERNAL_ANALYTICS
    if meta.source_family is S.SourceFamily.REGULATOR:
        return Origin.OFFICIAL_REGULATOR
    return Origin.MARKET_DATA


# --------------------------------------------------------------------------- report facts
def report_fact(report, fact_ids, text: str, scope: Scope, section: str, fact_id: str,
                session: dt.date | None = None) -> PublishableFact:
    """A validated MarketReport number (index/sector/flow/VIX/global). Origin is the non-AI
    evidence behind it; a fact whose ONLY observations are AI is origin AI (blocked)."""
    obs = []
    for fid in fact_ids or []:
        f = report.fact(fid) if fid else None
        if f is not None:
            obs.extend(f.observations or [])
    non_ai = [o for o in obs if o.source_type is not SourceType.AI]
    names = list(dict.fromkeys(o.source_name for o in non_ai))
    if obs and not non_ai:
        origin, names = Origin.AI, [S.SRC_GEMINI]
    elif not obs:
        origin = Origin.MARKET_DATA
    else:
        origin = Origin.MARKET_DATA
    retrieved = max((o.retrieved_at for o in non_ai if o.retrieved_at), default=None)
    as_of = session or (report.session_date or report.report_date)
    return PublishableFact(
        fact_id=fact_id, text=text, scope=scope, origin=origin,
        content_class=ContentClass.MARKET_AGGREGATE if scope is not Scope.SECURITY
        else ContentClass.FINANCIAL_STATISTIC,
        orientation=Orientation.HISTORICAL, source_name=names[0] if names else "",
        source_label=source_label(names), source_reference=", ".join(
            S.source_metadata(n).reference for n in names if S.source_metadata(n).reference),
        data_as_of=as_of, retrieved_at=retrieved,
        publication_rights_status=strictest_rights(names) if names else RightsStatus.UNKNOWN,
        verification_status=None, section=section,
        universe=None)


def index_statement(report, fact_ids, text, section, fact_id, scope=Scope.INDEX):
    return report_fact(report, fact_ids, text, scope, section, fact_id)


# --------------------------------------------------------------------------- stock-level
def radar_story_fact(instrument: str, text: str, session=None) -> PublishableFact:
    """A Market Radar story: our own technical analysis of a named security."""
    return PublishableFact(
        fact_id=f"radar.{instrument}", text=text, scope=Scope.SECURITY,
        origin=Origin.INTERNAL_ANALYTICS, content_class=ContentClass.TECHNICAL_ANALYSIS,
        orientation=Orientation.HISTORICAL, source_name=S.SRC_DERIVED,
        source_label=source_label([S.SRC_DERIVED]), data_as_of=session,
        publication_rights_status=RightsStatus.APPROVED, security=instrument,
        section="RADAR", tags=frozenset({"RADAR"}))


def mover_fact(symbol: str, text: str, session=None, rank: str = "TOP") -> PublishableFact:
    """A top gainer/loser: a named security placed in an order by its move."""
    return PublishableFact(
        fact_id=f"mover.{rank.lower()}.{symbol}", text=text, scope=Scope.SECURITY,
        origin=Origin.MARKET_DATA, content_class=ContentClass.FINANCIAL_STATISTIC,
        orientation=Orientation.HISTORICAL, source_name=S.SRC_YAHOO,
        source_label=source_label([S.SRC_YAHOO]), data_as_of=session,
        publication_rights_status=rights_for(S.SRC_YAHOO).status, security=symbol,
        ranking=True, section="MOVERS")


def stock_watch_fact(symbol: str, text: str, session=None) -> PublishableFact:
    """PRE stock watch: a previous-session Radar story carried forward to the open."""
    f = radar_story_fact(symbol, text, session)
    return PublishableFact(**{**f.__dict__, "fact_id": f"stock_watch.{symbol}",
                              "section": "STOCK_WATCH", "ranking": True})


# --------------------------------------------------------------------------- insights / events
_INSIGHT_SCOPE = {"INDEX_MOVE": Scope.INDEX, "INSTITUTIONAL_FLOW": Scope.MARKET,
                  "VOLATILITY": Scope.INDEX, "SECTOR_PERSISTENCE": Scope.SECTOR,
                  "MOVER_RECURRENCE": Scope.SECURITY, "RELATIVE_VOLUME": Scope.SECURITY}


def insight_fact(insight, text: str, session=None) -> PublishableFact:
    cat = getattr(insight.category, "value", str(insight.category))
    scope = _INSIGHT_SCOPE.get(cat, Scope.SECURITY)
    security = None if scope is not Scope.SECURITY else (insight.subject or insight.insight_id)
    return PublishableFact(
        fact_id=f"insight.{insight.insight_id}", text=text, scope=scope,
        origin=Origin.INTERNAL_ANALYTICS,
        content_class=ContentClass.MARKET_AGGREGATE if scope is not Scope.SECURITY
        else ContentClass.TECHNICAL_ANALYSIS,
        orientation=Orientation.HISTORICAL, source_name=S.SRC_DERIVED,
        source_label="Daily Market Byte history", data_as_of=session,
        publication_rights_status=RightsStatus.APPROVED, security=security, section="CONTEXT")


def report_event_fact(event: dict, session=None) -> PublishableFact:
    """An item from report.events: a rule-derived expiry, or a news/Gemini headline."""
    src = str(event.get("origin") or event.get("source") or "").upper()
    if src in ("RULE_FNO_EXPIRY", S.SRC_RULE_EXPIRY.upper()):
        origin, name = Origin.INTERNAL_ANALYTICS, S.SRC_RULE_EXPIRY
    elif "GEMINI" in src:
        origin, name = Origin.AI, S.SRC_GEMINI
    else:
        # news headlines, the "no events" placeholder, and anything unattributed
        origin, name = Origin.NEWS, S.SRC_GOOGLE_NEWS
    return PublishableFact(
        fact_id=f"event.{event.get('tag', '')}."
                f"{hashlib.sha1(str(event.get('text', '')).encode()).hexdigest()[:10]}",
        text=str(event.get("text") or ""), scope=Scope.MARKET, origin=origin,
        content_class=ContentClass.EXCHANGE_EVENT if origin is Origin.INTERNAL_ANALYTICS
        else ContentClass.OPINION if origin is Origin.AI else ContentClass.CORPORATE_EVENT,
        orientation=Orientation.SCHEDULED_EVENT, source_name=name,
        source_label=source_label([name]), data_as_of=session,
        publication_rights_status=rights_for(name).status, section="EVENTS")


__all__ = ["SOURCE_LABELS", "source_label", "strictest_rights", "origin_for", "report_fact",
           "index_statement", "radar_story_fact", "mover_fact", "stock_watch_fact",
           "insight_fact", "report_event_fact"]

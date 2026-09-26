"""Who a number came from, and - separately - whether two numbers are actually independent.

SOURCE AUTHORITY IS NOT SOURCE INDEPENDENCE. They answer different questions:

* `source_type` (authority) - how much this source's word is worth on its own. NSE is the
  exchange; Yahoo redistributes; Gemini is an LLM.
* `independence_group` (independence) - whether two observations constitute *separate*
  evidence. Two Yahoo readings of the same close are one piece of evidence no matter how
  they were fetched, and two Google News articles that both syndicate the same wire story
  are one publisher, not two witnesses.

Corroboration requires independence, not authority: two agreeing observations only verify a
fact when they come from different independence groups. That rule lives in
`core.validation.CrossSourceValidator`; this module supplies the facts it reasons over.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .enums import SourceType


class SourceFamily(str, Enum):
    """What kind of operation produced the number - the shape of its failure modes."""
    EXCHANGE = "EXCHANGE"                              # NSE, BSE: the venue itself
    REGULATOR = "REGULATOR"                            # RBI, the Fed: an official publisher
    MARKET_DATA_AGGREGATOR = "MARKET_DATA_AGGREGATOR"  # Yahoo: redistributes venue data
    NEWS_DISCOVERY = "NEWS_DISCOVERY"                  # Google News RSS: finds publishers
    AI_DISCOVERY = "AI_DISCOVERY"                      # Gemini: finds and paraphrases
    INTERNAL = "INTERNAL"                              # computed or rule-derived here
    FIXTURE = "FIXTURE"                                # synthetic demo data
    UNKNOWN = "UNKNOWN"


# Independence groups. Distinct constants rather than free strings so a typo cannot silently
# split one group into two and manufacture "independent" corroboration.
GROUP_NSE = "NSE"
GROUP_NSEIX = "NSE_IX"          # NSE International Exchange (GIFT City) - a separate venue from NSE
GROUP_RBI = "RBI"
GROUP_FED = "FEDERAL_RESERVE"
GROUP_YAHOO = "YAHOO"
GROUP_GEMINI = "GEMINI"
GROUP_INTERNAL = "INTERNAL"
GROUP_DEMO = "DEMO_FIXTURE"
GROUP_UNKNOWN = "UNKNOWN"

# Prefix for news publishers: the independence group is the publisher, not the aggregator
# that surfaced it, so two syndications of one wire story collapse into a single group.
NEWS_GROUP_PREFIX = "PUBLISHER:"


@dataclass(frozen=True)
class SourceMetadata:
    """Everything known about one source, independent of any particular observation."""
    source_name: str
    source_type: SourceType
    source_family: SourceFamily
    independence_group: str
    upstream_source: str | None = None
    retrieval_method: str = ""
    reference: str = ""
    market_timestamp_available: bool = False
    display_rights_status: str = "UNREVIEWED"
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "source_type": self.source_type.value,
            "source_family": self.source_family.value,
            "independence_group": self.independence_group,
            "upstream_source": self.upstream_source,
            "retrieval_method": self.retrieval_method,
            "reference": self.reference,
            "market_timestamp_available": self.market_timestamp_available,
            "display_rights_status": self.display_rights_status,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SourceMetadata:
        return cls(
            source_name=d["source_name"],
            source_type=SourceType(d["source_type"]),
            source_family=SourceFamily(d.get("source_family", "UNKNOWN")),
            independence_group=d.get("independence_group", GROUP_UNKNOWN),
            upstream_source=d.get("upstream_source"),
            retrieval_method=d.get("retrieval_method", ""),
            reference=d.get("reference", ""),
            market_timestamp_available=bool(d.get("market_timestamp_available", False)),
            display_rights_status=d.get("display_rights_status", "UNREVIEWED"),
            notes=d.get("notes", ""),
        )


SRC_NSE = "nse_website"
SRC_NSE_ARCHIVE = "nse_index_close_archive"
SRC_YAHOO = "yahoo_finance"
SRC_GEMINI = "gemini"
SRC_GOOGLE_NEWS = "google_news_rss"
SRC_RULE_EXPIRY = "expiry_calendar_rule"
SRC_DERIVED = "daily_byte_derived"
SRC_DEMO = "demo_fixture"
# PRE-MARKET official sources (pre-data-sources phase)
SRC_NSEIX_LIVE = "nseix_market_rate"          # GIFT Nifty live quote, NSE IX website API
SRC_NSEIX_DSP = "nseix_settlement_file"       # GIFT Nifty daily settlement price file
SRC_RBI_PRESS = "rbi_press_release"           # RBI's own press release (MPC schedule)
SRC_FED_CALENDAR = "federal_reserve_calendar"  # the Fed's own FOMC calendar page

_REGISTRY: dict[str, SourceMetadata] = {
    SRC_NSE: SourceMetadata(
        source_name=SRC_NSE, source_type=SourceType.PRIMARY, source_family=SourceFamily.EXCHANGE,
        independence_group=GROUP_NSE, retrieval_method="https GET www.nseindia.com/api",
        reference="https://www.nseindia.com", market_timestamp_available=True,
        display_rights_status="UNREVIEWED",
        notes="The venue itself. Often blocks cloud IPs, so absence is routine, not an error."),
    SRC_NSE_ARCHIVE: SourceMetadata(
        source_name=SRC_NSE_ARCHIVE, source_type=SourceType.PRIMARY,
        source_family=SourceFamily.EXCHANGE, independence_group=GROUP_NSE,
        retrieval_method="https GET nsearchives.nseindia.com/content/indices/ind_close_all_<DDMMYYYY>.csv",
        reference="https://nsearchives.nseindia.com", market_timestamp_available=True,
        display_rights_status="UNREVIEWED",
        notes="NSE's official end-of-day index file. Same independence group as nse_website - "
              "one exchange, one witness. Used only for a benchmark/sector session the primary "
              "(Yahoo) lacks, after validation (market.recover_index_gaps / "
              "recover_recap_session)."),
    SRC_YAHOO: SourceMetadata(
        source_name=SRC_YAHOO, source_type=SourceType.SECONDARY,
        source_family=SourceFamily.MARKET_DATA_AGGREGATOR, independence_group=GROUP_YAHOO,
        upstream_source="NSE via Yahoo Finance redistribution", retrieval_method="yfinance",
        reference="https://finance.yahoo.com", market_timestamp_available=False,
        display_rights_status="UNREVIEWED",
        notes="Redistributes exchange data; verified to drop sessions and to return NaN "
              "closes before backfill, so it is corroborated rather than trusted alone."),
    SRC_GEMINI: SourceMetadata(
        source_name=SRC_GEMINI, source_type=SourceType.AI, source_family=SourceFamily.AI_DISCOVERY,
        independence_group=GROUP_GEMINI, retrieval_method="generateContent + google_search grounding",
        market_timestamp_available=False, display_rights_status="UNREVIEWED",
        notes="An LLM with web search is a DISCOVERY source, never a primary one. Grounded "
              "search does not make it an exchange."),
    SRC_GOOGLE_NEWS: SourceMetadata(
        source_name=SRC_GOOGLE_NEWS, source_type=SourceType.NEWS,
        source_family=SourceFamily.NEWS_DISCOVERY, independence_group=GROUP_UNKNOWN,
        retrieval_method="Google News RSS", reference="https://news.google.com/rss",
        market_timestamp_available=True, display_rights_status="HEADLINE_ONLY",
        notes="An aggregator, not a publisher. Independence belongs to the underlying "
              "publisher - use news_source() so syndications of one story group together."),
    SRC_RULE_EXPIRY: SourceMetadata(
        source_name=SRC_RULE_EXPIRY, source_type=SourceType.DERIVED,
        source_family=SourceFamily.INTERNAL, independence_group=GROUP_INTERNAL,
        retrieval_method="F&O expiry weekday rule", market_timestamp_available=False,
        display_rights_status="OWN_CONTENT", notes="Deterministic calendar rule, no fetch."),
    SRC_DERIVED: SourceMetadata(
        source_name=SRC_DERIVED, source_type=SourceType.DERIVED,
        source_family=SourceFamily.INTERNAL, independence_group=GROUP_INTERNAL,
        retrieval_method="computed by this pipeline", display_rights_status="OWN_CONTENT",
        notes="EMA/RSI/pivots and similar. Nothing external can corroborate these."),
    SRC_DEMO: SourceMetadata(
        source_name=SRC_DEMO, source_type=SourceType.DERIVED, source_family=SourceFamily.FIXTURE,
        independence_group=GROUP_DEMO, retrieval_method="synthetic fixture",
        display_rights_status="OWN_CONTENT",
        notes="Synthetic demo data. Never to be mistaken for a real reading."),
    SRC_NSEIX_LIVE: SourceMetadata(
        source_name=SRC_NSEIX_LIVE, source_type=SourceType.PRIMARY,
        source_family=SourceFamily.EXCHANGE, independence_group=GROUP_NSEIX,
        retrieval_method="https GET www.nseix.com/api/market-rate?type=derivative",
        reference="https://www.nseix.com", market_timestamp_available=True,
        display_rights_status="UNREVIEWED",
        notes="The venue that lists GIFT Nifty (NIFTY index futures on NSE IX). Undocumented "
              "website API: each contract row carries its own last-trade timestamp (TIMESTMP). "
              "Used only for the PRE GIFT Nifty strip, only when FRESH (core.freshness)."),
    SRC_NSEIX_DSP: SourceMetadata(
        source_name=SRC_NSEIX_DSP, source_type=SourceType.PRIMARY,
        source_family=SourceFamily.EXCHANGE, independence_group=GROUP_NSEIX,
        retrieval_method="https GET www.nseix.com/api/content/daily_report/G_T_DSP_PRICE_<DDMMYYYY>.CSV",
        reference="https://www.nseix.com", market_timestamp_available=True,
        display_rights_status="UNREVIEWED",
        notes="NSE IX's official daily settlement price file. Same independence group as "
              "nseix_market_rate - one exchange, one witness. The reference a GIFT Nifty "
              "change is measured from."),
    SRC_RBI_PRESS: SourceMetadata(
        source_name=SRC_RBI_PRESS, source_type=SourceType.PRIMARY,
        source_family=SourceFamily.REGULATOR, independence_group=GROUP_RBI,
        retrieval_method="manual entry from www.rbi.org.in press release, re-checked by "
                         "verify_official_events.py", reference="https://www.rbi.org.in",
        display_rights_status="OWN_CONTENT",
        notes="The regulator's own publication. Schedule only - never a number."),
    SRC_FED_CALENDAR: SourceMetadata(
        source_name=SRC_FED_CALENDAR, source_type=SourceType.PRIMARY,
        source_family=SourceFamily.REGULATOR, independence_group=GROUP_FED,
        retrieval_method="manual entry from federalreserve.gov FOMC calendar",
        reference="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        display_rights_status="OWN_CONTENT", notes="Schedule only - never a number."),
}


def _publisher_group(publisher: str | None) -> str:
    """Independence group for a news item: its publisher, normalized.

    Two Google News entries pointing at the same wire story carry different URLs and often
    slightly different headlines, but they are one witness. Grouping by publisher is the
    cheap approximation of that; syndication across publishers is a known residual gap.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (publisher or "").strip().lower()).strip("-")
    return f"{NEWS_GROUP_PREFIX}{slug}" if slug else GROUP_UNKNOWN


def news_source(publisher: str | None) -> SourceMetadata:
    """Google News item attributed to its underlying publisher."""
    base = _REGISTRY[SRC_GOOGLE_NEWS]
    return SourceMetadata(
        source_name=base.source_name, source_type=base.source_type,
        source_family=base.source_family, independence_group=_publisher_group(publisher),
        upstream_source=publisher or None, retrieval_method=base.retrieval_method,
        reference=base.reference, market_timestamp_available=base.market_timestamp_available,
        display_rights_status=base.display_rights_status, notes=base.notes)


def source_metadata(source_name: str) -> SourceMetadata:
    """Registry lookup. An unregistered source is described honestly as UNKNOWN rather than
    being quietly assigned a plausible group - an invented independence group is exactly the
    failure this module exists to prevent."""
    known = _REGISTRY.get(source_name)
    if known:
        return known
    return SourceMetadata(source_name=source_name, source_type=SourceType.DERIVED,
                          source_family=SourceFamily.UNKNOWN, independence_group=GROUP_UNKNOWN,
                          notes="Source not in the registry; independence cannot be assumed.")


def independence_group_for(source_name: str) -> str:
    return source_metadata(source_name).independence_group


def register_source(meta: SourceMetadata) -> SourceMetadata:
    _REGISTRY[meta.source_name] = meta
    return meta


def registry_snapshot(source_names) -> dict:
    """The source records behind one report, for embedding in the report JSON."""
    out = {}
    for name in sorted(set(source_names)):
        out[name] = source_metadata(name).to_dict()
    return out


__all__ = ["SourceMetadata", "SourceFamily", "source_metadata", "independence_group_for",
           "register_source", "registry_snapshot", "news_source",
           "SRC_NSE", "SRC_YAHOO", "SRC_GEMINI", "SRC_GOOGLE_NEWS", "SRC_RULE_EXPIRY",
           "SRC_DERIVED", "SRC_DEMO", "SRC_NSEIX_LIVE", "SRC_NSEIX_DSP", "SRC_RBI_PRESS",
           "SRC_FED_CALENDAR", "GROUP_NSE", "GROUP_NSEIX", "GROUP_RBI", "GROUP_FED", "GROUP_YAHOO", "GROUP_GEMINI",
           "GROUP_INTERNAL", "GROUP_DEMO", "GROUP_UNKNOWN", "NEWS_GROUP_PREFIX"]

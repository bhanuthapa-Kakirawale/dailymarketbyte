"""Typed classification of every potentially publishable fact.

A `PublishableFact` is one statement a scene, hook, title or description could carry, with the
answers the publication policy needs: what it is about (scope), where it came from (origin),
what kind of statement it is (content_class), which way in time it points (orientation), and the
provenance a viewer must be able to see (source, data_as_of, fetched) plus the rights status of
that source. Classification is recorded where the fact is built - never guessed from rendered
text afterwards (the same rule as provenance: only the builder still knows).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum


class Scope(str, Enum):
    MARKET = "MARKET"
    INDEX = "INDEX"
    SECTOR = "SECTOR"
    SECURITY = "SECURITY"
    IPO = "IPO"


class Origin(str, Enum):
    OFFICIAL_EXCHANGE = "OFFICIAL_EXCHANGE"
    OFFICIAL_REGULATOR = "OFFICIAL_REGULATOR"
    OFFICIAL_COMPANY = "OFFICIAL_COMPANY"
    MARKET_DATA = "MARKET_DATA"
    INTERNAL_ANALYTICS = "INTERNAL_ANALYTICS"
    NEWS = "NEWS"
    AI = "AI"


OFFICIAL_ORIGINS = frozenset({Origin.OFFICIAL_EXCHANGE, Origin.OFFICIAL_REGULATOR,
                              Origin.OFFICIAL_COMPANY})


class ContentClass(str, Enum):
    MARKET_AGGREGATE = "MARKET_AGGREGATE"
    EXCHANGE_EVENT = "EXCHANGE_EVENT"
    CORPORATE_EVENT = "CORPORATE_EVENT"
    IPO_EVENT = "IPO_EVENT"
    FINANCIAL_STATISTIC = "FINANCIAL_STATISTIC"
    TECHNICAL_ANALYSIS = "TECHNICAL_ANALYSIS"
    OPINION = "OPINION"
    RECOMMENDATION = "RECOMMENDATION"


class Orientation(str, Enum):
    HISTORICAL = "HISTORICAL"
    CURRENT_FACT = "CURRENT_FACT"
    SCHEDULED_EVENT = "SCHEDULED_EVENT"
    FORWARD_LOOKING = "FORWARD_LOOKING"


class RightsStatus(str, Enum):
    APPROVED = "APPROVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


def _iso(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


@dataclass(frozen=True)
class PublishableFact:
    """One candidate statement for publication. `text` is exactly what a viewer would read.

    `security`: the named security/company when scope is SECURITY or IPO (else None).
    `ranking`: the fact places named securities in an order (top gainer, "stocks to watch").
    `tags`: free markers the policy reads, e.g. {"GMP"} for grey-market content.
    """
    fact_id: str
    text: str
    scope: Scope
    origin: Origin
    content_class: ContentClass
    orientation: Orientation
    source_name: str
    source_label: str = ""                   # what the viewer sees after "SOURCE:"
    source_reference: str = ""               # URL / file / document reference
    data_as_of: object = None                # date/datetime the fact represents
    retrieved_at: object = None              # when this system fetched it
    universe: str | None = None              # "NIFTY 200" for a market-structure count
    publication_rights_status: RightsStatus = RightsStatus.UNKNOWN
    official_document_reference: str | None = None
    verification_status: str | None = None
    security: str | None = None
    ranking: bool = False
    section: str = ""
    tags: frozenset = field(default_factory=frozenset)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("scope", "origin", "content_class", "orientation", "publication_rights_status"):
            d[k] = getattr(self, k).value
        d["data_as_of"] = _iso(self.data_as_of)
        d["retrieved_at"] = _iso(self.retrieved_at)
        d["tags"] = sorted(self.tags)
        return d


__all__ = ["Scope", "Origin", "ContentClass", "Orientation", "RightsStatus", "PublishableFact",
           "OFFICIAL_ORIGINS"]

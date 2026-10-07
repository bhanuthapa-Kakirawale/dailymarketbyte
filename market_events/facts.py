"""PublishableFact builder for Market Events content (publication/classify.py pattern).

Every fact here defaults through `strictest_rights` to REVIEW_REQUIRED (nothing here loosens
`PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK`) - exactly like `institutional_flows.facts`. Scope/
content_class differ by family so each satisfies `publication/policy.py`'s existing rules
unmodified: a named security needs an OFFICIAL event (CORPORATE_EVENT); a primary-market event
is Scope.IPO (IPO_EVENT); a government securities auction names no security (Scope.MARKET).
"""
from __future__ import annotations

from core import sources as S

from .models import BUYBACK, DELISTING, EARNINGS, GOVT_SECURITIES_AUCTION, IPO, OFS, OPEN_OFFER
from .models import ANNOUNCED, OPEN, SCHEDULED

from publication.classification import ContentClass, Orientation, Origin, PublishableFact, Scope
from publication.classify import source_label, strictest_rights

TAG_MARKET_EVENT_PREFIX = "MARKET_EVENT_"

_SECURITY_FAMILIES = frozenset({EARNINGS, BUYBACK, OPEN_OFFER, DELISTING})
_IPO_SCOPE_FAMILIES = frozenset({IPO, OFS})
_SCHEDULED_STATUSES = frozenset({ANNOUNCED, SCHEDULED, OPEN})

# official-origin assignment per family - every family here is OFFICIAL by construction (an
# exchange/company filing or a regulator notification); never AI or NEWS.
_ORIGIN_BY_FAMILY = {
    EARNINGS: Origin.OFFICIAL_EXCHANGE, BUYBACK: Origin.OFFICIAL_EXCHANGE,
    OPEN_OFFER: Origin.OFFICIAL_REGULATOR, DELISTING: Origin.OFFICIAL_EXCHANGE,
    IPO: Origin.OFFICIAL_EXCHANGE, OFS: Origin.OFFICIAL_EXCHANGE,
    GOVT_SECURITIES_AUCTION: Origin.OFFICIAL_REGULATOR,
}


def market_event_fact(event, text: str, fact_id: str, section: str = "MARKET_EVENTS"
                      ) -> PublishableFact:
    if event.family in _SECURITY_FAMILIES:
        scope, content_class, security = Scope.SECURITY, ContentClass.CORPORATE_EVENT, event.symbol
    elif event.family in _IPO_SCOPE_FAMILIES:
        scope, content_class, security = Scope.IPO, ContentClass.IPO_EVENT, event.symbol
    else:   # GOVT_SECURITIES_AUCTION - market-wide, names no security
        scope, content_class, security = Scope.MARKET, ContentClass.EXCHANGE_EVENT, None
    orientation = (Orientation.SCHEDULED_EVENT if event.status in _SCHEDULED_STATUSES
                  else Orientation.HISTORICAL)
    src = event.source_name
    return PublishableFact(
        fact_id=fact_id, text=text, scope=scope, origin=_ORIGIN_BY_FAMILY[event.family],
        content_class=content_class, orientation=orientation, source_name=src,
        source_label=source_label([src]) if src in S._REGISTRY else (src or ""),
        source_reference=event.source_reference or (S.source_metadata(src).reference if src else ""),
        data_as_of=event.data_as_of, retrieved_at=event.retrieved_at,
        publication_rights_status=strictest_rights([src] if src else []),
        security=security, section=section,
        tags=frozenset({f"{TAG_MARKET_EVENT_PREFIX}{event.family}"}))


__all__ = ["TAG_MARKET_EVENT_PREFIX", "market_event_fact"]

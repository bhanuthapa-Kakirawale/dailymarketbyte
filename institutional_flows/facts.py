"""PublishableFact builders for institutional-flow content (publication/classify.py pattern).

Every fact here carries an `OFFICIAL_*`/`MARKET_DATA` origin backed by a source that is
`UNREVIEWED` in the registry, so `publication.rights.rights_for` resolves it to
REVIEW_REQUIRED and the production publication audit BLOCKs it under the default
`PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK` - exactly like every other not-yet-reviewed official
source. Nothing here loosens that; these builders only make the classification visible and
auditable wherever institutional-flow content is shown (PRE/POST review renders, Private Desk).
"""
from __future__ import annotations

import datetime as dt

from core import sources as S

from .models import CDSL, NSDL, NSE

from publication.classification import ContentClass, Orientation, Origin, PublishableFact, Scope
from publication.classify import source_label, strictest_rights

TAG_FPI_DII_PROVISIONAL = "FPI_DII_PROVISIONAL"
TAG_FPI_DEPOSITORY_REPORTED = "FPI_DEPOSITORY_REPORTED"
TAG_FPI_SECTOR_FLOW = "FPI_SECTOR_FLOW"

_SRC_BY_SOURCE = {NSE: S.SRC_NSE_FIIDII_API, CDSL: S.SRC_CDSL_FPI_DAILY,
                 NSDL: S.SRC_NSDL_FPI_FORTNIGHTLY}


def nse_fii_dii_fact(snapshot, text: str, fact_id: str, section: str = "FLOWS") -> PublishableFact:
    src = _SRC_BY_SOURCE[NSE]
    return PublishableFact(
        fact_id=fact_id, text=text, scope=Scope.MARKET, origin=Origin.MARKET_DATA,
        content_class=ContentClass.MARKET_AGGREGATE, orientation=Orientation.HISTORICAL,
        source_name=src, source_label=source_label([src]),
        source_reference=S.source_metadata(src).reference,
        data_as_of=snapshot.data_as_of, retrieved_at=snapshot.retrieved_at,
        publication_rights_status=strictest_rights([src]), section=section,
        tags=frozenset({TAG_FPI_DII_PROVISIONAL}))


def cdsl_fact(snapshot, text: str, fact_id: str, section: str = "FLOWS") -> PublishableFact:
    src = _SRC_BY_SOURCE[CDSL]
    return PublishableFact(
        fact_id=fact_id, text=text, scope=Scope.MARKET, origin=Origin.OFFICIAL_DEPOSITORY,
        content_class=ContentClass.MARKET_AGGREGATE, orientation=Orientation.HISTORICAL,
        source_name=src, source_label=source_label([src]),
        source_reference=S.source_metadata(src).reference,
        data_as_of=snapshot.data_as_of, retrieved_at=snapshot.retrieved_at,
        publication_rights_status=strictest_rights([src]), section=section,
        tags=frozenset({TAG_FPI_DEPOSITORY_REPORTED}))


def nsdl_sector_fact(snapshot, sector: str, text: str, fact_id: str,
                     section: str = "FLOWS") -> PublishableFact:
    src = _SRC_BY_SOURCE[NSDL]
    return PublishableFact(
        fact_id=fact_id, text=text, scope=Scope.SECTOR, origin=Origin.OFFICIAL_DEPOSITORY,
        content_class=ContentClass.MARKET_AGGREGATE, orientation=Orientation.HISTORICAL,
        source_name=src, source_label=source_label([src]),
        source_reference=S.source_metadata(src).reference,
        data_as_of=snapshot.data_as_of, retrieved_at=snapshot.retrieved_at,
        publication_rights_status=strictest_rights([src]), section=section,
        universe=sector, tags=frozenset({TAG_FPI_SECTOR_FLOW}))


__all__ = ["TAG_FPI_DII_PROVISIONAL", "TAG_FPI_DEPOSITORY_REPORTED", "TAG_FPI_SECTOR_FLOW",
           "nse_fii_dii_fact", "cdsl_fact", "nsdl_sector_fact"]

"""Publication-rights registry: technical accessibility is NOT publication permission.

Every source the public Short can cite has a `publication_rights_status`:

    APPROVED         our own content (deterministic rules, derived aggregates, hand-entered
                     official schedules) - nothing third-party is redistributed as-is
    REVIEW_REQUIRED  an exchange/regulator/data-provider source whose redistribution terms
                     have NOT been reviewed. Reachable is not licensed. Never upgraded silently.
    RESTRICTED       must not be published (AI output as a source, news headlines as facts,
                     NSE IX while its rights review is OPEN - see docs/NSEIX_RIGHTS_REVIEW.md)
    UNKNOWN          not in the registry

What happens to REVIEW_REQUIRED is a CONFIGURED policy (`PUBLIC_REVIEW_REQUIRED_POLICY`):

    BLOCK            (default - conservative) nothing that relies on a REVIEW_REQUIRED source
                     reaches PRODUCTION publication: the publication audit BLOCKs and the upload
                     is refused. Review / internal / synthetic renders still show the content -
                     rights are enforced at the audit, not by emptying the storyboard.
    ATTRIBUTED_EOD   an explicit owner decision: end-of-day / official-notice facts may be
                     published with the source visibly attributed, each use listed in the
                     audit. Live/intraday readings are never covered.

This module records facts and an operating choice, not a legal conclusion.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core import sources as S

from .classification import RightsStatus


@dataclass(frozen=True)
class RightsRecord:
    source_name: str
    status: RightsStatus
    basis: str


# display_rights_status (core.sources) -> publication rights. OWN_CONTENT is our own work.
_FROM_DISPLAY = {"OWN_CONTENT": RightsStatus.APPROVED,
                 "UNREVIEWED": RightsStatus.REVIEW_REQUIRED,
                 "HEADLINE_ONLY": RightsStatus.RESTRICTED}

# Explicit overrides - stricter than the display status, never looser.
_OVERRIDES = {
    S.SRC_GEMINI: RightsRecord(S.SRC_GEMINI, RightsStatus.RESTRICTED,
                               "an LLM is never shown as the source of a market fact"),
    S.SRC_GOOGLE_NEWS: RightsRecord(S.SRC_GOOGLE_NEWS, RightsStatus.RESTRICTED,
                                    "third-party headlines are discovery, not publishable facts"),
    S.SRC_NSEIX_LIVE: RightsRecord(S.SRC_NSEIX_LIVE, RightsStatus.RESTRICTED,
                                   "NSE IX display rights OPEN (docs/NSEIX_RIGHTS_REVIEW.md); "
                                   "public display only via operations.gift_policy"),
    S.SRC_NSEIX_DSP: RightsRecord(S.SRC_NSEIX_DSP, RightsStatus.RESTRICTED,
                                  "NSE IX display rights OPEN (docs/NSEIX_RIGHTS_REVIEW.md)"),
    S.SRC_DEMO: RightsRecord(S.SRC_DEMO, RightsStatus.RESTRICTED,
                             "synthetic fixture - never published"),
}

ATTRIBUTED_EOD = "ATTRIBUTED_EOD"
BLOCK = "BLOCK"


def rights_for(source_name: str) -> RightsRecord:
    if source_name in _OVERRIDES:
        return _OVERRIDES[source_name]
    meta = S._REGISTRY.get(source_name)
    if meta is None:
        return RightsRecord(source_name, RightsStatus.UNKNOWN, "source not in the registry")
    status = _FROM_DISPLAY.get(meta.display_rights_status, RightsStatus.UNKNOWN)
    basis = {RightsStatus.APPROVED: "own content / derived by this pipeline",
             RightsStatus.REVIEW_REQUIRED: "redistribution terms not reviewed",
             RightsStatus.RESTRICTED: f"display status {meta.display_rights_status}",
             RightsStatus.UNKNOWN: f"unmapped display status {meta.display_rights_status}"}[status]
    return RightsRecord(source_name, status, basis)


def review_required_policy() -> str:
    v = (os.getenv("PUBLIC_REVIEW_REQUIRED_POLICY") or BLOCK).upper()
    return v if v in (ATTRIBUTED_EOD, BLOCK) else BLOCK   # an unknown value fails closed


def registry_table(source_names=None) -> list:
    names = sorted(source_names) if source_names is not None else sorted(S._REGISTRY)
    return [{"source_name": n, "publication_rights_status": rights_for(n).status.value,
             "basis": rights_for(n).basis} for n in names]


__all__ = ["RightsRecord", "rights_for", "review_required_policy", "registry_table",
           "ATTRIBUTED_EOD", "BLOCK"]

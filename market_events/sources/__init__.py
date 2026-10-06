"""Per-family fetchers. None of the 7 families has a live adapter in this pass: each one's
official source (NSE/BSE corporate-filings API for EARNINGS/OFS/BUYBACK/OPEN_OFFER/DELISTING,
RBI's auction calendar for GOVT_SECURITIES_AUCTION) has not been verified reachable from this
environment, and "reliability over feature count" (docs/MARKET_EVENTS_ENGINE.md) means no
adapter is written against an endpoint that was never actually probed. Every family therefore
returns `NOT_SUPPORTED_YET` - a normal, non-failure state (mirrors `official_snapshots`'s own
`NOT_SUPPORTED` for ESM) - until `validate_market_events_sources.py`'s connectivity probe
clears a specific source for a real adapter.
"""
from __future__ import annotations

from ..models import ALL_FAMILIES, EARNINGS, IPO, FamilyFetchResult, NOT_SUPPORTED_YET


def _not_supported(family: str, reason: str):
    def fetch(now_iso: str) -> FamilyFetchResult:
        return FamilyFetchResult(status=NOT_SUPPORTED_YET, events=[], reason=reason)
    fetch.__name__ = f"fetch_{family.lower()}_not_supported_yet"
    fetch.not_supported_yet = True        # explicit marker - a real adapter replacing this
                                           # entry simply doesn't set it, so Data Quality can
                                           # tell "never attempted" apart from "has a real adapter"
    return fetch


_REASON = ("no official source for this family has been verified reachable from this "
          "environment this pass - see docs/MARKET_EVENTS_ENGINE.md")

# IPO is never acquired by this engine BY DESIGN (ipo_watch/official_snapshots remains the one
# source of truth) - not a "not verified reachable" gap, so its stub carries an honest reason
# string distinct from the generic one above. Private Desk reads it via
# market_events.sources.ipo_projection, never via this stub.
_IPO_REASON = ("IPO is sourced via ipo_watch/official_snapshots by design, never acquired by "
              "this engine - see market_events/sources/ipo_projection.py")

DEFAULT_FETCHERS = {family: _not_supported(family, _REASON) for family in ALL_FAMILIES}
DEFAULT_FETCHERS[IPO] = _not_supported(IPO, _IPO_REASON)

from .earnings import fetch_earnings        # noqa: E402 - after DEFAULT_FETCHERS to avoid a
                                             # circular import (earnings.py imports core.sources,
                                             # which is safe, but keep the pattern consistent)
DEFAULT_FETCHERS[EARNINGS] = fetch_earnings

__all__ = ["DEFAULT_FETCHERS"]

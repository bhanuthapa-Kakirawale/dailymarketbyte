"""Publication boundary: PRIVATE_ANALYTICS vs PUBLIC_UNREGISTERED.

    MARKET DATA ──┬── PRIVATE_ANALYTICS    named stocks, Radar events, rankings, charts, watchlists
                  └── PUBLIC_UNREGISTERED  market/index/sector facts, market-wide aggregates with
                                           an explicit universe, official exchange/company/IPO
                                           facts - each with a visible SOURCE and DATA AS OF

`gate.PublicationGate` judges every candidate fact before a storyboard is built;
`audit.build_publication_audit` records the verdict per video and `require_publication_pass`
makes an upload impossible without a PASS. See docs/PUBLICATION_POLICY.md.
"""
from .audit import (PublicationBlocked, build_publication_audit, require_publication_pass,
                    write_publication_audit)
from .classification import (ContentClass, Orientation, Origin, PublishableFact, RightsStatus,
                             Scope)
from .gate import PublicationGate, private_gate
from .language import scan_public_text
from .policy import PolicyDecision, evaluate
from .profile import (DEFAULT_PUBLIC_PROFILE, ProfileNotAvailable, PublicationProfile,
                      resolve_profile, uploadable)

__all__ = ["PublicationProfile", "ProfileNotAvailable", "DEFAULT_PUBLIC_PROFILE",
           "resolve_profile", "uploadable", "PublishableFact", "Scope", "Origin", "ContentClass",
           "Orientation", "RightsStatus", "PublicationGate", "private_gate", "evaluate",
           "PolicyDecision", "scan_public_text", "build_publication_audit",
           "write_publication_audit", "require_publication_pass", "PublicationBlocked"]

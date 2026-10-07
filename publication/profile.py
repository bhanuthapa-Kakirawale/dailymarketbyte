"""Publication profiles: WHO the output is for decides WHAT may be in it.

    PRIVATE_ANALYTICS       research / internal review. Every Radar capability: named stocks,
                            technical events, rankings, charts, watchlists. Never uploaded.
    PUBLIC_UNREGISTERED     the public YouTube Short, published by a person who is NOT a
                            SEBI-registered Research Analyst. Market/index/sector facts,
                            market-wide aggregates, official exchange/company/IPO facts only.
    PUBLIC_RA_REGISTERED    reserved. Would need a registration this channel does not hold, so it
                            is deliberately not implemented and cannot be selected.

The DEFAULT for anything that can reach the public is PUBLIC_UNREGISTERED. Private analytics
must be asked for by name - an omitted argument can never widen what a viewer sees.
The renderer never decides compliance: the profile is applied by `publication.gate` before a
storyboard exists.
"""
from __future__ import annotations

import os
from enum import Enum


class PublicationProfile(str, Enum):
    PRIVATE_ANALYTICS = "PRIVATE_ANALYTICS"
    PUBLIC_UNREGISTERED = "PUBLIC_UNREGISTERED"
    PUBLIC_RA_REGISTERED = "PUBLIC_RA_REGISTERED"   # reserved - never selectable

    @property
    def is_public(self) -> bool:
        return self is not PublicationProfile.PRIVATE_ANALYTICS


class ProfileNotAvailable(RuntimeError):
    """A profile that exists as a name only (PUBLIC_RA_REGISTERED)."""


DEFAULT_PUBLIC_PROFILE = PublicationProfile.PUBLIC_UNREGISTERED


def resolve_profile(value=None) -> PublicationProfile:
    """`None` -> the configured default (env `PUBLICATION_PROFILE`, else PUBLIC_UNREGISTERED).
    The environment may select PRIVATE_ANALYTICS for local research renders, never the reserved
    RA profile. An unknown name is an error, not a silent fallback."""
    if value is None:
        value = os.getenv("PUBLICATION_PROFILE") or DEFAULT_PUBLIC_PROFILE.value
    profile = value if isinstance(value, PublicationProfile) else PublicationProfile(str(value).upper())
    if profile is PublicationProfile.PUBLIC_RA_REGISTERED:
        raise ProfileNotAvailable(
            "PUBLIC_RA_REGISTERED is reserved: this channel holds no SEBI Research Analyst "
            "registration, so the profile is not implemented for public use")
    return profile


def uploadable(profile: PublicationProfile) -> bool:
    """Only the public profile may ever reach an upload. Private analytics never leave."""
    return profile is PublicationProfile.PUBLIC_UNREGISTERED


__all__ = ["PublicationProfile", "ProfileNotAvailable", "DEFAULT_PUBLIC_PROFILE",
           "resolve_profile", "uploadable"]

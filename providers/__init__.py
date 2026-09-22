"""Acquisition layer: the outer edge of the system, where provenance is attached.

Providers wrap the existing market.py / news.py fetching rather than replacing it - the
strangler seam means each one can be reimplemented underneath without anything above
noticing. What they guarantee is the boundary: above this line, everything is an
Observation or a typed payload, never a raw provider dictionary.
"""
from .base import Provider, ProviderResult
from .gemini import GeminiProvider
from .news import Catalyst, EventItem, NarrativeBundle, NewsProvider
from .nse import NseProvider
from .yahoo import IndexSession, YahooProvider

__all__ = ["Provider", "ProviderResult", "YahooProvider", "IndexSession", "NseProvider",
           "NewsProvider", "NarrativeBundle", "Catalyst", "EventItem", "GeminiProvider"]

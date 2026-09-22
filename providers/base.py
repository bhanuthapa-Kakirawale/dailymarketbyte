"""Provider boundary: the only place raw provider output is allowed to exist.

Everything downstream of a provider speaks Observations and typed payloads. That is the
whole point of the boundary - so the renderer, and anything else added later, can never
reach back to a provider dictionary and read a number nobody validated.

These wrappers deliberately do NOT reimplement acquisition. market.py and news.py keep
doing the fetching; providers wrap them, attach provenance at the moment of acquisition,
and hand back canonical objects. That is the strangler seam: the legacy modules can be
replaced underneath one wrapper at a time without anything above noticing.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from core import Observation


@dataclass
class ProviderResult:
    """Observations plus any typed payload that is not itself a market number.

    `payload` carries things like the candle series a chart needs - structured data that
    belongs to the report but is not a single measured value. `notes` records what went
    wrong without raising, since a missing optional source is a normal day here.
    """
    observations: list[Observation] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def extend(self, other: ProviderResult) -> ProviderResult:
        self.observations.extend(other.observations)
        self.payload.update(other.payload)
        self.notes.extend(other.notes)
        return self


class Provider:
    """Common shape for the acquisition wrappers.

    `session_date` is the trading session being reported and `report_date` the day the
    report is published - they differ every Monday, and conflating them silently misdates
    a whole session's worth of facts.
    """
    name = "provider"

    def __init__(self, session_date: dt.date, report_date: dt.date,
                 retrieved_at: dt.datetime, demo: bool = False):
        self.session_date = session_date
        self.report_date = report_date
        self.retrieved_at = retrieved_at
        self.demo = demo


__all__ = ["Provider", "ProviderResult"]

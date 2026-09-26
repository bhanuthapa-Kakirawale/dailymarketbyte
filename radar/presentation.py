"""Radar presentation contract (Phase 4.2 Packet 6.1).

The first downstream consumer of `radar.daily_pipeline.DailyRadarResult`:

    DailyRadarResult / daily_radar_<date>.json
            |
    build_radar_presentation (this module)
            |
    RadarPresentation / radar_presentation_<date>.json
            |
    (a future renderer - NOT built in this packet)

## Hard boundary (packet spec section 4)

This module answers "what should the viewer see, in what order, for how long" - never
"which stocks are interesting". It reruns no detector, fetches no market data, computes no
indicator, ranks nothing, and never reorders, adds, or removes a story Radar's own editorial
selector already chose. Every field on `RadarStoryPresentation` is either a direct
reformatting of a `RadarStory` field, or a template string built entirely from already-
validated `RadarStory` fields - never a new fact, never inferred, never fetched from an LLM or
web search (packet spec sections 4/33/35).

## Numeric integrity (packet spec section 35)

Every number this module displays already exists on the input `RadarStory` (or its `provenance`)
- this module only reformats it (rounding to a fixed number of decimals, adding a unit suffix).
It never performs a new calculation such as "volume increased 330%" from a raw RVOL ratio.

## Content safety (packet spec section 34)

Every generated viewer-facing string is built from validated Radar text (which itself already
passed `core.content_safety.sanitize_field` once, in `radar.daily_pipeline._build_story`), but
this module re-checks every string it emits anyway, as defense in depth - a presentation whose
any field comes back BLOCKED is never returned as usable; `RadarPresentation.status` becomes
`CONTENT_SAFETY_BLOCKED` and `scenes` is empty. A SANITIZED field is used (with its neutralized
text) and recorded, per existing content-safety semantics - it is not a failure.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from enum import Enum

import config
from core.content_safety import SafetyStatus, sanitize_field

RADAR_PRESENTATION_VERSION = "1.0"
ARTIFACT_DIR = os.path.join(config.OUT_DIR, "radar", "presentation")

ASPECT_RATIO = "9:16"
TARGET_DURATION_SECONDS = 50
MIN_DURATION_SECONDS = 40
MAX_DURATION_SECONDS = 60

HOOK_DURATION_SECONDS = 5
STORY_DURATION_SECONDS = 8
CLOSING_DURATION_SECONDS = 4

MAX_DISPLAY_EVIDENCE = 3
_WORD_LIMITS = {"headline": 6, "primary_fact": 10, "context_line": 12, "evidence_label": 5}


class PresentationFormat(str, Enum):
    MARKET_RADAR_SHORT = "MARKET_RADAR_SHORT"


class SceneRole(str, Enum):
    HOOK = "HOOK"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    STORY = "STORY"
    CLOSING = "CLOSING"


class VisualType(str, Enum):
    THREE_SIGNAL_CARD = "THREE_SIGNAL_CARD"
    TECHNICAL_CHANGE_CARD = "TECHNICAL_CHANGE_CARD"
    VOLUME_STRUCTURE_CARD = "VOLUME_STRUCTURE_CARD"
    RELATIVE_STRUCTURE_CARD = "RELATIVE_STRUCTURE_CARD"
    MIXED_SIGNAL_CARD = "MIXED_SIGNAL_CARD"
    QUIET_SIGNAL_CARD = "QUIET_SIGNAL_CARD"


class PresentationStatus(str, Enum):
    OK = "OK"
    NO_STORIES = "NO_STORIES"
    REJECTED_UPSTREAM_FAILED = "REJECTED_UPSTREAM_FAILED"
    CONTENT_SAFETY_BLOCKED = "CONTENT_SAFETY_BLOCKED"


# --------------------------------------------------------------------------- label mappings
# Every mapping below is a closed, exhaustive table over an EXISTING enum in radar/models.py -
# never a guess, never a new classification (packet spec sections 15/16/21/22).
EVIDENCE_FAMILY_LABELS = {
    "VOLUME": "Unusual Volume",
    "STRUCTURE": "Technical Structure",
    "RELATIVE_PERFORMANCE": "Relative Performance",
}

TECHNICAL_EVENT_LABELS = {
    "BREAK_ABOVE_20D_RANGE": "20-session break above",
    "BREAK_BELOW_20D_RANGE": "20-session break below",
    "BREAK_ABOVE_50D_RANGE": "50-session break above",
    "BREAK_BELOW_50D_RANGE": "50-session break below",
    "CROSS_ABOVE_SMA20": "SMA20 cross above",
    "CROSS_BELOW_SMA20": "SMA20 cross below",
    "CROSS_ABOVE_SMA50": "SMA50 cross above",
    "CROSS_BELOW_SMA50": "SMA50 cross below",
    "RANGE_COMPRESSION": "range compression",
}

VOLUME_LEVEL_LABELS = {
    "ELEVATED": "Elevated Volume",
    "UNUSUAL": "Unusual Volume",
    "EXTREME": "Extreme Volume",
}

NOVELTY_LABELS = {
    "NEW_CANDIDATE": "New Radar Signal",
    "NEW_EVIDENCE_FAMILY": "New Evidence Added",
    "NEW_TECHNICAL_EVENT": "New Technical Event",
    "PERSISTENCE_TRANSITION": "Relative-Strength Shift",
    "DIRECTION_TRANSITION": "Direction Changed",
    "ATTENTION_ESCALATION": "Signal Strengthened",
    "MULTIPLE_CHANGES": "Multiple Changes",
    "CONTINUATION": "Continuing Signal",
}
# novelty types that represent an actual STATE CHANGE (never CONTINUATION/NEW_CANDIDATE, which
# have no "before" state to contrast against) - used to decide whether `novelty_reason` is
# display-worthy as the top evidence item (packet spec section 28, priority 1).
_NOVELTY_STATE_CHANGE_TYPES = frozenset({
    "NEW_EVIDENCE_FAMILY", "NEW_TECHNICAL_EVENT", "PERSISTENCE_TRANSITION",
    "DIRECTION_TRANSITION", "ATTENTION_ESCALATION", "MULTIPLE_CHANGES",
})

DIRECTION_LABELS = {
    "ALIGNED_POSITIVE": "Positive Alignment",
    "ALIGNED_NEGATIVE": "Negative Alignment",
    "MIXED": "Mixed Signals",
    "NON_DIRECTIONAL": None,  # no viewer-facing claim when the model itself makes none
}

PERSISTENCE_LABELS = {
    "PERSISTENT_POSITIVE": "Persistent Relative Strength",
    "PERSISTENT_NEGATIVE": "Persistent Relative Weakness",
    "MIXED": None, "NEUTRAL": None,  # not a display-worthy claim on their own
}


# --------------------------------------------------------------------------- formatting (no new arithmetic)
def _fmt_pct(value) -> str | None:
    return None if value is None else f"{value:+.1f}%"


def _fmt_pp(value) -> str | None:
    return None if value is None else f"{value:+.1f} pp"


def _fmt_rvol(value) -> str | None:
    return None if value is None else f"{value:.1f}x"


def _word_count(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------------------- presentation models
@dataclass
class PresentationEvidence:
    """One viewer-facing evidence line, deterministically labelled - never a new fact (packet
    spec section 27/28)."""
    label: str
    tier: str  # "PRIMARY" | "SECONDARY" | "SUPPORTING"
    source_field: str  # traceability: which RadarStory field this line was built from

    def to_dict(self) -> dict:
        return {"label": self.label, "tier": self.tier, "source_field": self.source_field}


@dataclass
class RadarStoryPresentation:
    instrument: str
    company_name: str | None
    scene_order: int
    headline: str
    primary_fact: str | None
    context_line: str | None
    evidence_items: list = field(default_factory=list)
    active_families: list = field(default_factory=list)
    novelty_label: str | None = None
    direction_label: str | None = None
    price_change_display: str | None = None
    quiet_signal: bool = False
    selection_reason: str | None = None
    visual_type: str = VisualType.TECHNICAL_CHANGE_CARD.value
    duration_seconds: int = STORY_DURATION_SECONDS
    source_selection_id: str = ""
    supporting_session_dates: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    narration_facts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "company_name": self.company_name,
            "scene_order": self.scene_order, "headline": self.headline,
            "primary_fact": self.primary_fact, "context_line": self.context_line,
            "evidence_items": [e.to_dict() for e in self.evidence_items],
            "active_families": list(self.active_families),
            "novelty_label": self.novelty_label, "direction_label": self.direction_label,
            "price_change_display": self.price_change_display,
            "quiet_signal": self.quiet_signal, "selection_reason": self.selection_reason,
            "visual_type": self.visual_type, "duration_seconds": self.duration_seconds,
            "source_selection_id": self.source_selection_id,
            "supporting_session_dates": list(self.supporting_session_dates),
            "provenance": dict(self.provenance), "narration_facts": list(self.narration_facts),
        }


@dataclass
class RadarPresentationScene:
    scene_order: int
    role: str  # SceneRole value
    duration_seconds: int
    headline: str | None = None
    subheadline: str | None = None
    story: RadarStoryPresentation | None = None

    def to_dict(self) -> dict:
        return {
            "scene_order": self.scene_order, "role": self.role,
            "duration_seconds": self.duration_seconds, "headline": self.headline,
            "subheadline": self.subheadline,
            "story": self.story.to_dict() if self.story else None,
        }


@dataclass
class PresentationQA:
    duration_in_target_range: bool
    duration_target_not_reached_due_to_story_count: bool
    story_order_preserved: bool
    no_duplicate_instruments: bool
    max_evidence_respected: bool
    every_story_maps_to_selection: bool
    content_safety_passed: bool
    text_density_violations: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (self.story_order_preserved and self.no_duplicate_instruments and
                self.max_evidence_respected and self.every_story_maps_to_selection and
                self.content_safety_passed and not self.text_density_violations)

    def to_dict(self) -> dict:
        return {
            "duration_in_target_range": self.duration_in_target_range,
            "duration_target_not_reached_due_to_story_count":
                self.duration_target_not_reached_due_to_story_count,
            "story_order_preserved": self.story_order_preserved,
            "no_duplicate_instruments": self.no_duplicate_instruments,
            "max_evidence_respected": self.max_evidence_respected,
            "every_story_maps_to_selection": self.every_story_maps_to_selection,
            "content_safety_passed": self.content_safety_passed,
            "text_density_violations": list(self.text_density_violations),
            "notes": list(self.notes), "passed": self.passed,
        }


@dataclass
class RadarPresentation:
    presentation_id: str
    presentation_version: str
    session_date: str
    generated_at: str
    format: str
    aspect_ratio: str
    target_duration_seconds: int
    estimated_duration_seconds: int
    headline: str | None
    subheadline: str | None
    scenes: list = field(default_factory=list)
    story_count: int = 0
    source_radar_artifact: str | None = None
    source_selector_version: str | None = None
    source_pipeline_status: str | None = None
    content_safety_status: str = SafetyStatus.SAFE.value
    status: str = PresentationStatus.OK.value
    qa: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "presentation_id": self.presentation_id,
            "presentation_version": self.presentation_version,
            "session_date": self.session_date, "generated_at": self.generated_at,
            "format": self.format, "aspect_ratio": self.aspect_ratio,
            "target_duration_seconds": self.target_duration_seconds,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "headline": self.headline, "subheadline": self.subheadline,
            "scenes": [s.to_dict() for s in self.scenes], "story_count": self.story_count,
            "source_radar_artifact": self.source_radar_artifact,
            "source_selector_version": self.source_selector_version,
            "source_pipeline_status": self.source_pipeline_status,
            "content_safety_status": self.content_safety_status, "status": self.status,
            "qa": dict(self.qa), "warnings": list(self.warnings),
        }


def presentation_id(session_date: str, fmt: str = PresentationFormat.MARKET_RADAR_SHORT.value,
                    version: str = RADAR_PRESENTATION_VERSION) -> str:
    """Deterministic identity (packet spec section 8) - session_date + format + version alone,
    never a random UUID. The SAME Radar input under the SAME presentation version always
    produces the SAME logical presentation id."""
    return f"{session_date}:{fmt}:{version}"


__all__ = [
    "RADAR_PRESENTATION_VERSION", "ASPECT_RATIO", "TARGET_DURATION_SECONDS",
    "MIN_DURATION_SECONDS", "MAX_DURATION_SECONDS", "PresentationFormat", "SceneRole",
    "VisualType", "PresentationStatus", "PresentationEvidence", "RadarStoryPresentation",
    "RadarPresentationScene", "PresentationQA", "RadarPresentation", "presentation_id",
    "EVIDENCE_FAMILY_LABELS", "TECHNICAL_EVENT_LABELS", "VOLUME_LEVEL_LABELS",
    "NOVELTY_LABELS", "DIRECTION_LABELS", "PERSISTENCE_LABELS",
]

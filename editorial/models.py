"""The editorial plan: what the Short actually says, and where each claim came from.

The canonical report is not the video script. This model is the script - derived
presentation state, regenerable at any time from the report and the snapshot it cites, and
never persisted as canonical fact.

Its shape encodes the rule the whole phase turns on:

    ONE SCENE = ONE PRIMARY MESSAGE

A scene has exactly one `primary_value` and one `primary_text`. Everything else is secondary
by construction, so density has to be a deliberate choice rather than an accident of having
data available.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from enum import Enum

from .config import EDITORIAL_VERSION


class SceneType(str, Enum):
    HOOK = "HOOK"
    GLOBAL = "GLOBAL"
    NIFTY = "NIFTY"
    FLOWS = "FLOWS"
    SECTORS = "SECTORS"
    MOVERS = "MOVERS"
    GAINERS = "GAINERS"
    LOSERS = "LOSERS"
    CONTEXT = "CONTEXT"
    EVENTS = "EVENTS"
    OUTRO = "OUTRO"


@dataclass
class EditorialItem:
    """One row in a scene. `value` is already formatted for display; `numeric` keeps the
    underlying number so traceability can be checked against the canonical report."""
    title: str
    value: str = ""
    label: str = ""
    note: str = ""
    rank: int | None = None               # 1-based position in a ranked scan list; None elsewhere
    numeric: float | None = None
    positive: bool | None = None          # drives colour; None means neutral
    source_fact_ids: list = field(default_factory=list)
    source_insight_ids: list = field(default_factory=list)

    def words(self) -> int:
        """Every word on screen, for QA reporting."""
        return sum(len(part.split()) for part in (self.title, self.value, self.label, self.note))

    def reading_words(self) -> int:
        """Words that are actually READ, for the time estimate.

        A formatted number and a short chip are glanced at, not read left to right, and the
        estimator already charges a fixation for each. Counting them as prose too priced a
        two-row flows card at nine seconds and caused the planner to delete its own content.
        """
        return len(self.title.split()) + len(self.note.split())

    def to_dict(self) -> dict:
        return {"title": self.title, "value": self.value, "label": self.label,
                "note": self.note, "rank": self.rank, "numeric": self.numeric,
                "positive": self.positive,
                "source_fact_ids": list(self.source_fact_ids),
                "source_insight_ids": list(self.source_insight_ids)}


@dataclass
class ScenePlan:
    """One scene, with its single dominant message and the evidence behind it."""
    scene_id: str
    scene_type: SceneType
    primary_text: str = ""
    secondary_text: str = ""
    primary_value: str = ""
    primary_numeric: float | None = None
    primary_positive: bool | None = None
    items: list = field(default_factory=list)
    caption: str = ""                      # exactly one; scenes never cycle captions
    show_ticker: bool = True
    has_chart: bool = False
    source_fact_ids: list = field(default_factory=list)
    source_insight_ids: list = field(default_factory=list)
    estimated_read_seconds: float = 0.0
    planned_duration: float = 0.0
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ measurement
    def primary_words(self) -> int:
        return len(self.primary_text.split()) + len(self.primary_value.split())

    def secondary_words(self) -> int:
        return len(self.secondary_text.split())

    def caption_words(self) -> int:
        return len(self.caption.split())

    def visible_words(self) -> int:
        """Everything on screen while this scene runs, for QA reporting."""
        return (self.primary_words() + self.secondary_words() + self.caption_words()
                + sum(item.words() for item in self.items))

    def reading_words(self) -> int:
        """Words the estimator treats as read rather than glanced - see EditorialItem."""
        return (len(self.primary_text.split()) + self.secondary_words() + self.caption_words()
                + sum(item.reading_words() for item in self.items))

    def value_count(self) -> int:
        """Large numbers on screen; each is a separate fixation for the estimator."""
        return (1 if self.primary_value else 0) + sum(1 for i in self.items if i.value)

    def card_count(self) -> int:
        return len(self.items)

    def caption_changes(self) -> int:
        """Zero by design: a scene shows one stable takeaway, never a rotating essay."""
        return 0

    def public_text(self) -> dict:
        """Every string this scene will put in front of a viewer, for the safety scan."""
        fields = {}
        for name, value in (("primary_text", self.primary_text),
                            ("primary_value", self.primary_value),
                            ("secondary_text", self.secondary_text),
                            ("caption", self.caption)):
            if value:
                fields[f"{self.scene_id}.{name}"] = value
        for index, item in enumerate(self.items):
            for name, value in (("title", item.title), ("value", item.value),
                                ("label", item.label), ("note", item.note)):
                if value:
                    fields[f"{self.scene_id}.item[{index}].{name}"] = value
            # A ranked scan row (Top 5 Gainers/Losers) draws its rank number on screen -
            # every drawn string must be declared, even a bare digit.
            if item.rank is not None:
                fields[f"{self.scene_id}.item[{index}].rank"] = str(item.rank)
        return fields

    def numeric_values(self) -> dict:
        """Displayed numbers, labelled, for the traceability check."""
        out = {}
        if self.primary_numeric is not None:
            out[f"{self.scene_id}.primary"] = self.primary_numeric
        for index, item in enumerate(self.items):
            if item.numeric is not None:
                out[f"{self.scene_id}.item[{index}]"] = item.numeric
        return out

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id, "scene_type": self.scene_type.value,
            "primary_text": self.primary_text, "primary_value": self.primary_value,
            "primary_numeric": self.primary_numeric, "secondary_text": self.secondary_text,
            "caption": self.caption, "show_ticker": self.show_ticker,
            "has_chart": self.has_chart, "items": [i.to_dict() for i in self.items],
            "source_fact_ids": list(self.source_fact_ids),
            "source_insight_ids": list(self.source_insight_ids),
            "estimated_read_seconds": round(self.estimated_read_seconds, 2),
            "planned_duration": round(self.planned_duration, 2),
            "visible_words": self.visible_words(), "cards": self.card_count(),
            "metadata": dict(self.metadata),
        }


@dataclass
class ShortsPlan:
    """The whole Short: hook first, then whatever earned its place."""
    report_id: str
    session_date: dt.date | None = None
    generated_at: dt.datetime | None = None
    scenes: list = field(default_factory=list)
    omitted: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    # publication boundary: the profile the plan was built under and the gate that judged it
    publication_profile: str = "PUBLIC_UNREGISTERED"
    gate: object | None = None

    @property
    def hook(self):
        return self.scenes[0] if self.scenes else None

    @property
    def total_duration(self) -> float:
        return round(sum(s.planned_duration for s in self.scenes), 2)

    def scene(self, scene_type) -> ScenePlan | None:
        wanted = SceneType(scene_type) if isinstance(scene_type, str) else scene_type
        return next((s for s in self.scenes if s.scene_type is wanted), None)

    def public_text(self) -> dict:
        fields = {}
        for scene in self.scenes:
            fields.update(scene.public_text())
        return fields

    def numeric_values(self) -> dict:
        out = {}
        for scene in self.scenes:
            out.update(scene.numeric_values())
        return out

    def to_dict(self) -> dict:
        return {
            "editorial_version": EDITORIAL_VERSION,
            "report_id": self.report_id,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "total_duration": self.total_duration,
            "scene_count": len(self.scenes),
            "scenes": [s.to_dict() for s in self.scenes],
            "omitted": list(self.omitted),
            "notes": list(self.notes),
            "publication_profile": self.publication_profile,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


__all__ = ["ShortsPlan", "ScenePlan", "EditorialItem", "SceneType"]

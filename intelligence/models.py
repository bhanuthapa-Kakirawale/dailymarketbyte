"""Typed output of the historical intelligence layer, and the policies it obeys.

Everything here describes what ALREADY happened. Nothing in this package predicts, ranks
securities, or recommends anything - the boundary is deliberate and load-bearing, because a
sentence like "FIIs have sold for four sessions" is a fact while "FIIs are exiting India" is
a claim about the future dressed up as one.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from enum import Enum

from core import ValidationStatus

INTELLIGENCE_SCHEMA_VERSION = "1.0"
# Bumped when a calculation changes meaning, so two snapshots are never silently compared
# across an algorithm change.
CALCULATION_VERSION = "1.0"


class InsightCategory(str, Enum):
    INDEX_MOVE = "INDEX_MOVE"
    INSTITUTIONAL_FLOW = "INSTITUTIONAL_FLOW"
    VOLATILITY = "VOLATILITY"
    SECTOR_PERSISTENCE = "SECTOR_PERSISTENCE"
    MOVER_RECURRENCE = "MOVER_RECURRENCE"
    RELATIVE_VOLUME = "RELATIVE_VOLUME"


class Strength(str, Enum):
    """How much data stands behind a statement - NOT how likely anything is to happen.

    There is no probability anywhere in this package. `FULL_HISTORY` means the window the
    metric defines was completely available; it says nothing about tomorrow.
    """
    FULL_HISTORY = "FULL_HISTORY"
    PARTIAL_HISTORY = "PARTIAL_HISTORY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


# Which persisted facts may enter a historical calculation. Centralised here so the rule is
# stated once: a number that conflicted between sources, went stale, or rests on an LLM alone
# is not evidence, and averaging it into a 20-session mean would launder it into one.
ELIGIBLE_HISTORICAL_STATUSES = frozenset({
    ValidationStatus.VERIFIED.value,
    ValidationStatus.SINGLE_SOURCE.value,
})


def is_eligible(status) -> bool:
    return getattr(status, "value", status) in ELIGIBLE_HISTORICAL_STATUSES


@dataclass
class IntelligenceInsight:
    """One factual statement about history, with everything needed to re-derive it.

    `supporting_report_ids` and `supporting_fact_ids` are not decoration: an insight that
    cannot be traced back to the canonical records behind it is an assertion, not an
    observation, and this pipeline does not publish assertions.
    """
    insight_id: str
    category: InsightCategory
    subject: str
    statement: str
    current_value: float | None = None
    comparison_value: float | None = None
    lookback_sessions: int | None = None
    sample_size: int = 0
    strength: Strength = Strength.FULL_HISTORY
    supporting_report_ids: list = field(default_factory=list)
    supporting_fact_ids: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def selection_score(self) -> float:
        """How unusual this reading is, in [0, 1]. Set by the analyser from the data itself."""
        try:
            return float(self.metadata.get("selection_score", 0.0))
        except (TypeError, ValueError):
            return 0.0

    @property
    def is_displayable(self) -> bool:
        return self.strength is not Strength.INSUFFICIENT_HISTORY and bool(self.statement)

    def to_dict(self) -> dict:
        return {
            "insight_id": self.insight_id,
            "category": self.category.value,
            "subject": self.subject,
            "statement": self.statement,
            "current_value": self.current_value,
            "comparison_value": self.comparison_value,
            "lookback_sessions": self.lookback_sessions,
            "sample_size": self.sample_size,
            "strength": self.strength.value,
            "supporting_report_ids": list(self.supporting_report_ids),
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> IntelligenceInsight:
        return cls(
            insight_id=d["insight_id"], category=InsightCategory(d["category"]),
            subject=d["subject"], statement=d["statement"],
            current_value=d.get("current_value"), comparison_value=d.get("comparison_value"),
            lookback_sessions=d.get("lookback_sessions"), sample_size=d.get("sample_size", 0),
            strength=Strength(d.get("strength", "FULL_HISTORY")),
            supporting_report_ids=list(d.get("supporting_report_ids") or []),
            supporting_fact_ids=list(d.get("supporting_fact_ids") or []),
            metadata=dict(d.get("metadata") or {}))


# Tiebreak order when two insights are equally unusual. Score comes first; this only decides
# ties, so a dramatic sector streak is never buried under a dull index reading.
CATEGORY_PRIORITY = {
    InsightCategory.INDEX_MOVE: 1,
    InsightCategory.INSTITUTIONAL_FLOW: 2,
    InsightCategory.VOLATILITY: 3,
    InsightCategory.SECTOR_PERSISTENCE: 4,
    InsightCategory.MOVER_RECURRENCE: 5,
    InsightCategory.RELATIVE_VOLUME: 6,
}

MAX_DISPLAYED_INSIGHTS = 3


@dataclass
class IntelligenceSnapshot:
    """Derived historical context for one report. NOT canonical market history.

    This is a function of (canonical report, canonical history) and can be regenerated from
    them at any time, which is exactly why it is written as its own artifact rather than
    folded back into a report that Phase 3.1 froze.
    """
    report_id: str
    session_date: dt.date | None = None
    historical_cutoff: dt.date | None = None
    generated_at: dt.datetime | None = None
    available_history: int = 0
    insights: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def displayable(self) -> list:
        return [i for i in self.insights if i.is_displayable]

    def selected(self, limit: int = MAX_DISPLAYED_INSIGHTS) -> list:
        """The insights worth screen time, chosen deterministically.

        Most unusual first, category order only as a tiebreak, then insight_id so the result
        is total and stable. No model decides what is interesting: the same snapshot always
        selects the same statements.

        At most one statement per subject: a 5-session and a 20-session reading of the same
        index are the same story told twice, and screen space is the scarcest thing here.
        """
        ordered = sorted(
            self.displayable(),
            key=lambda i: (-round(i.selection_score, 6),
                           CATEGORY_PRIORITY.get(i.category, 99), i.insight_id),
        )
        chosen, seen = [], set()
        for insight in ordered:
            key = (insight.category, insight.subject)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(insight)
            if len(chosen) >= limit:
                break
        return chosen

    def to_dict(self) -> dict:
        return {
            "intelligence_schema_version": INTELLIGENCE_SCHEMA_VERSION,
            "calculation_version": CALCULATION_VERSION,
            "report_id": self.report_id,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "historical_cutoff": (self.historical_cutoff.isoformat()
                                  if self.historical_cutoff else None),
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "available_history": self.available_history,
            "insights": [i.to_dict() for i in self.insights],
            "selected_insight_ids": [i.insight_id for i in self.selected()],
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> IntelligenceSnapshot:
        return cls(
            report_id=d["report_id"],
            session_date=(dt.date.fromisoformat(d["session_date"]) if d.get("session_date") else None),
            historical_cutoff=(dt.date.fromisoformat(d["historical_cutoff"])
                               if d.get("historical_cutoff") else None),
            generated_at=(dt.datetime.fromisoformat(d["generated_at"])
                          if d.get("generated_at") else None),
            available_history=d.get("available_history", 0),
            insights=[IntelligenceInsight.from_dict(i) for i in d.get("insights", [])],
            warnings=list(d.get("warnings") or []),
            metrics=dict(d.get("metrics") or {}))

    @classmethod
    def from_json(cls, text: str) -> IntelligenceSnapshot:
        return cls.from_dict(json.loads(text))


__all__ = ["IntelligenceSnapshot", "IntelligenceInsight", "InsightCategory", "Strength",
           "ELIGIBLE_HISTORICAL_STATUSES", "is_eligible", "CATEGORY_PRIORITY",
           "MAX_DISPLAYED_INSIGHTS", "INTELLIGENCE_SCHEMA_VERSION", "CALCULATION_VERSION"]

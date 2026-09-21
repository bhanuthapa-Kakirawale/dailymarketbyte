"""MarketReport: the contract between market intelligence and any presentation layer.

The video is one rendering of a report. A dashboard, an email or a research query would be
another. Everything a consumer needs should therefore be in here - including the provenance
that lets someone six months later ask "where did this number come from?" and get an answer.

Presentation sections (nifty, sectors, gainers, ...) stay close to the shapes the existing
renderer already uses, each carrying `fact_ids` that point back into `facts`. That keeps the
report readable without duplicating provenance into every section.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from .enums import REQUIRED_METRICS, Metric, ReportType, ValidationStatus, is_critical
from .models import Fact

REPORT_SCHEMA_VERSION = "1.0"


@dataclass
class ValidationSummary:
    """Counts by status plus the single question a publisher actually asks."""
    verified_count: int = 0
    single_source_count: int = 0
    provisional_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    missing_count: int = 0
    rejected_count: int = 0
    unvalidated_count: int = 0
    total_facts: int = 0
    publication_ready: bool = True
    blocking_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verified_count": self.verified_count,
            "single_source_count": self.single_source_count,
            "provisional_count": self.provisional_count,
            "conflict_count": self.conflict_count,
            "stale_count": self.stale_count,
            "missing_count": self.missing_count,
            "rejected_count": self.rejected_count,
            "unvalidated_count": self.unvalidated_count,
            "total_facts": self.total_facts,
            "publication_ready": self.publication_ready,
            "blocking_issues": list(self.blocking_issues),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ValidationSummary:
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


_COUNT_FIELD = {
    ValidationStatus.VERIFIED: "verified_count",
    ValidationStatus.SINGLE_SOURCE: "single_source_count",
    ValidationStatus.PROVISIONAL: "provisional_count",
    ValidationStatus.CONFLICT: "conflict_count",
    ValidationStatus.STALE: "stale_count",
    ValidationStatus.MISSING: "missing_count",
    ValidationStatus.REJECTED: "rejected_count",
    ValidationStatus.UNVALIDATED: "unvalidated_count",
}


def summarize(facts: list[Fact]) -> ValidationSummary:
    """Tally fact statuses and decide whether the set is fit to publish.

    Blocking is deliberately narrow: a required fact that is absent or unusable, or any
    critical fact whose sources actively disagree. An optional fact being MISSING is normal
    (NSE blocks cloud IPs most days) and must never stop a report from existing.
    """
    s = ValidationSummary(total_facts=len(facts))
    for f in facts:
        setattr(s, _COUNT_FIELD[f.validation_status], getattr(s, _COUNT_FIELD[f.validation_status]) + 1)

    for f in facts:
        if f.validation_status == ValidationStatus.CONFLICT and is_critical(f.metric):
            s.blocking_issues.append(
                f"{f.metric.value} ({f.instrument}): sources disagree beyond tolerance")
        elif f.metric in REQUIRED_METRICS and not f.is_publishable:
            s.blocking_issues.append(
                f"{f.metric.value} ({f.instrument}): required fact is {f.validation_status.value}")

    present = {f.metric for f in facts}
    for metric in REQUIRED_METRICS:
        if metric not in present:
            s.blocking_issues.append(f"{metric.value}: required fact absent from report")

    s.publication_ready = not s.blocking_issues
    return s


@dataclass
class MarketReport:
    """A dated, validated view of one market session, ready for any presentation layer."""
    report_date: dt.date
    report_type: ReportType = ReportType.PRE_MARKET
    generated_at: dt.datetime | None = None
    session_date: dt.date | None = None          # the trading session being described
    facts: list[Fact] = field(default_factory=list)
    nifty: dict = field(default_factory=dict)
    global_cues: list = field(default_factory=list)
    institutional_flows: dict = field(default_factory=dict)
    sectors: list = field(default_factory=list)
    gainers: list = field(default_factory=list)
    losers: list = field(default_factory=list)
    events: list = field(default_factory=list)
    technicals: dict = field(default_factory=dict)
    report_id: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.report_id:
            self.report_id = f"{self.report_date:%Y%m%d}_{self.report_type.value}"

    # ------------------------------------------------------------------ access
    @property
    def validation_summary(self) -> ValidationSummary:
        return summarize(self.facts)

    @property
    def publication_ready(self) -> bool:
        return self.validation_summary.publication_ready

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.fact_id == fact_id), None)

    def facts_for(self, metric: Metric) -> list[Fact]:
        return [f for f in self.facts if f.metric == metric]

    def add_fact(self, fact: Fact) -> Fact:
        """Add a fact, replacing any existing one with the same id so a rebuild is idempotent."""
        self.facts = [f for f in self.facts if f.fact_id != fact.fact_id] + [fact]
        return fact

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "report_id": self.report_id,
            "report_type": self.report_type.value,
            "report_date": self.report_date.isoformat(),
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "validation_summary": self.validation_summary.to_dict(),
            "nifty": self.nifty,
            "global_cues": self.global_cues,
            "institutional_flows": self.institutional_flows,
            "sectors": self.sectors,
            "gainers": self.gainers,
            "losers": self.losers,
            "events": self.events,
            "technicals": self.technicals,
            "facts": [f.to_dict() for f in self.facts],
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> MarketReport:
        report = cls(
            report_date=dt.date.fromisoformat(d["report_date"]),
            report_type=ReportType(d.get("report_type", ReportType.PRE_MARKET.value)),
            generated_at=(dt.datetime.fromisoformat(d["generated_at"]) if d.get("generated_at") else None),
            session_date=(dt.date.fromisoformat(d["session_date"]) if d.get("session_date") else None),
            facts=[Fact.from_dict(f) for f in d.get("facts", [])],
            nifty=d.get("nifty") or {},
            global_cues=d.get("global_cues") or [],
            institutional_flows=d.get("institutional_flows") or {},
            sectors=d.get("sectors") or [],
            gainers=d.get("gainers") or [],
            losers=d.get("losers") or [],
            events=d.get("events") or [],
            technicals=d.get("technicals") or {},
            report_id=d.get("report_id", ""),
            metadata=dict(d.get("metadata") or {}),
        )
        return report

    @classmethod
    def from_json(cls, text: str) -> MarketReport:
        return cls.from_dict(json.loads(text))


__all__ = ["MarketReport", "ValidationSummary", "summarize", "REPORT_SCHEMA_VERSION"]

"""Canonical data model: Observation -> Fact, with ValidationResult recording every check.

The three types here are the whole contract between data acquisition and publication.
Everything a viewer eventually sees should be traceable back through a Fact to the
Observations that produced it, and through each Observation to a named source.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace
from typing import Any

from .enums import Metric, SourceType, ValidationStatus

# Common units. Free-form strings are allowed; these exist to keep spelling consistent.
UNIT_INR = "INR"
UNIT_INR_CRORE = "INR_CRORE"
UNIT_USD = "USD"
UNIT_PERCENT = "PERCENT"
UNIT_RATIO = "RATIO"
UNIT_POINTS = "POINTS"


def _slug(text: Any) -> str:
    """Lowercase, punctuation-free token safe to embed in an id."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "na"


def _iso(value: dt.datetime | dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: str | dt.datetime | None) -> dt.datetime | None:
    if value is None or isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(value)


def _parse_date(value: str | dt.date | None) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


@dataclass(frozen=True)
class Observation:
    """Exactly what one source said, once. Immutable on purpose.

    Validation reads Observations and never rewrites them: if a number looks wrong, that
    judgement belongs in a ValidationResult on the Fact, not in an edit to the record of
    what the provider actually returned. Frozen enforces this at the language level.
    """
    metric: Metric
    instrument: str
    value: float | None
    unit: str
    market_date: dt.date
    source_name: str
    source_type: SourceType
    retrieved_at: dt.datetime
    observed_at: dt.datetime | None = None
    source_reference: str | None = None
    observation_id: str = ""
    independence_group: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.observation_id:
            ident = "-".join(_slug(x) for x in
                             (self.metric.value, self.instrument, self.source_name, self.market_date))
            object.__setattr__(self, "observation_id", f"obs_{ident}")
        if not self.independence_group:
            # Resolved once, at construction, from the source registry. Callers with better
            # information (a news item's publisher, say) pass it explicitly instead.
            from .sources import independence_group_for
            object.__setattr__(self, "independence_group", independence_group_for(self.source_name))

    @property
    def is_ai(self) -> bool:
        return self.source_type == SourceType.AI

    @property
    def source_metadata(self):
        """The registry record for this source - the SourceMetadata half of the
        Fact -> Observation -> SourceMetadata traceability chain."""
        from .sources import source_metadata
        meta = source_metadata(self.source_name)
        if meta.independence_group == self.independence_group:
            return meta
        # A per-observation group (news publisher) overrides the registry default.
        from dataclasses import replace as _replace
        return _replace(meta, independence_group=self.independence_group)

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "metric": self.metric.value,
            "instrument": self.instrument,
            "value": self.value,
            "unit": self.unit,
            "market_date": _iso(self.market_date),
            "observed_at": _iso(self.observed_at),
            "retrieved_at": _iso(self.retrieved_at),
            "source_name": self.source_name,
            "source_type": self.source_type.value,
            "independence_group": self.independence_group,
            "source_reference": self.source_reference,
            "source_metadata": self.source_metadata.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Observation:
        return cls(
            metric=Metric(d["metric"]),
            instrument=d["instrument"],
            value=d.get("value"),
            unit=d.get("unit", ""),
            market_date=_parse_date(d["market_date"]),
            source_name=d["source_name"],
            source_type=SourceType(d["source_type"]),
            retrieved_at=_parse_dt(d["retrieved_at"]),
            observed_at=_parse_dt(d.get("observed_at")),
            source_reference=d.get("source_reference"),
            observation_id=d.get("observation_id", ""),
            independence_group=d.get("independence_group", ""),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class ValidationResult:
    """One check, its verdict, and enough detail to re-argue the verdict later.

    `details` is deliberately open: a cross-source check stores both source values and the
    computed difference, a date check stores expected vs actual. Without that, a CONFLICT in
    an archived report is unactionable six months later.
    """
    validator: str
    status: ValidationStatus
    message: str = ""
    checked_at: dt.datetime | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "validator": self.validator,
            "status": self.status.value,
            "message": self.message,
            "checked_at": _iso(self.checked_at),
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ValidationResult:
        return cls(
            validator=d["validator"],
            status=ValidationStatus(d["status"]),
            message=d.get("message", ""),
            checked_at=_parse_dt(d.get("checked_at")),
            details=dict(d.get("details") or {}),
        )


@dataclass
class Fact:
    """A normalized market fact, plus every observation and check behind it.

    `value` is the published number. It is chosen from the observations by validation
    policy (preferring the most authoritative non-AI source), never averaged - averaging
    two disagreeing sources would manufacture a number no source ever reported.
    """
    metric: Metric
    instrument: str
    value: float | None
    unit: str
    market_date: dt.date
    observations: list[Observation] = field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED
    validation_results: list[ValidationResult] = field(default_factory=list)
    fact_id: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.fact_id:
            ident = "-".join(_slug(x) for x in (self.metric.value, self.instrument, self.market_date))
            self.fact_id = f"fact_{ident}"

    @classmethod
    def from_observations(cls, observations: list[Observation], **kw) -> Fact:
        """Build an unvalidated Fact from observations of the same metric/instrument."""
        if not observations:
            raise ValueError("Fact.from_observations needs at least one observation")
        head = observations[0]
        return cls(metric=head.metric, instrument=head.instrument, value=head.value,
                   unit=head.unit, market_date=head.market_date,
                   observations=list(observations), **kw)

    @property
    def sources(self) -> list[str]:
        return [o.source_name for o in self.observations]

    @property
    def is_ai_only(self) -> bool:
        """True when nothing but an LLM stands behind this number."""
        return bool(self.observations) and all(o.is_ai for o in self.observations)

    @property
    def is_publishable(self) -> bool:
        from .enums import PUBLISHABLE_STATUSES
        return self.validation_status in PUBLISHABLE_STATUSES and self.value is not None

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "metric": self.metric.value,
            "instrument": self.instrument,
            "value": self.value,
            "unit": self.unit,
            "market_date": _iso(self.market_date),
            "validation_status": self.validation_status.value,
            "observations": [o.to_dict() for o in self.observations],
            "validation_results": [r.to_dict() for r in self.validation_results],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Fact:
        return cls(
            metric=Metric(d["metric"]),
            instrument=d["instrument"],
            value=d.get("value"),
            unit=d.get("unit", ""),
            market_date=_parse_date(d["market_date"]),
            observations=[Observation.from_dict(o) for o in d.get("observations", [])],
            validation_status=ValidationStatus(d.get("validation_status", "UNVALIDATED")),
            validation_results=[ValidationResult.from_dict(r) for r in d.get("validation_results", [])],
            fact_id=d.get("fact_id", ""),
            metadata=dict(d.get("metadata") or {}),
        )


__all__ = ["Observation", "Fact", "ValidationResult", "replace",
           "UNIT_INR", "UNIT_INR_CRORE", "UNIT_USD", "UNIT_PERCENT", "UNIT_RATIO", "UNIT_POINTS"]

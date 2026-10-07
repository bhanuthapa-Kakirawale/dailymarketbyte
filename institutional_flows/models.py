"""Institutional flow snapshot vocabulary.

A snapshot is one official report as published, from one of the three sources, captured
immutably. `report_key` is deliberately source-specific - NSE's stated trade date, CDSL's
reporting date, NSDL's fortnight-end date - because these three sources do NOT describe the
same period, and forcing them onto one shared "session date" would misstate what each one says.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "institutional-flow-snapshot-1.0"

NSE, CDSL, NSDL = "NSE", "CDSL", "NSDL"
SOURCES = (NSE, CDSL, NSDL)

# SourceClass: the semantic weight of the figure, never upgraded past what the source itself
# claims. NSE is same-day and explicitly provisional; CDSL is a custodian-reported daily
# figure; NSDL is an official fortnightly regulatory publication.
EXCHANGE_PROVISIONAL = "EXCHANGE_PROVISIONAL"
DEPOSITORY_REPORTED = "DEPOSITORY_REPORTED"
DEPOSITORY_FORTNIGHTLY = "DEPOSITORY_FORTNIGHTLY"
SOURCE_CLASS = {NSE: EXCHANGE_PROVISIONAL, CDSL: DEPOSITORY_REPORTED, NSDL: DEPOSITORY_FORTNIGHTLY}

# acquisition status of one snapshot attempt
SUCCESS = "SUCCESS"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"      # the request itself failed
PARSE_ERROR = "PARSE_ERROR"                    # answered; shape did not match what we parse
VALIDATION_FAILED = "VALIDATION_FAILED"        # parsed; internal arithmetic/date check failed
VALIDATED = frozenset({SUCCESS})
FAILED = frozenset({SOURCE_UNAVAILABLE, PARSE_ERROR, VALIDATION_FAILED})

# change state vs the previously stored snapshot for the same source
NEW_REPORT = "NEW_REPORT"
UNCHANGED = "UNCHANGED"
REVISED = "REVISED"

# how a PRE/POST/Desk consumer obtained an input
PERSISTED_SNAPSHOT = "PERSISTED_SNAPSHOT"
CAPTURED_THIS_RUN = "CAPTURED_THIS_RUN"
HISTORICAL_SNAPSHOT_UNAVAILABLE = "HISTORICAL_SNAPSHOT_UNAVAILABLE"
NOT_CAPTURED = "NOT_CAPTURED"
SYNTHETIC = "SYNTHETIC"

# represented-period basis - what we actually know about what the figure covers
SOURCE_STATED = "SOURCE_STATED"
UNKNOWN = "UNKNOWN"

REPORT_JOB, PRE_FALLBACK = "REPORT_JOB", "PRE_FALLBACK"


def records_checksum(records) -> str:
    """sha256 over the canonical JSON of the normalized facts - never the raw page. A page's
    viewstate/session token changing on every fetch must never make an unchanged report look
    new; only the facts we actually extracted decide that."""
    blob = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def raw_checksum(raw) -> str:
    blob = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class RepresentedPeriod:
    """What the figure actually covers, preserved verbatim rather than guessed."""
    start: str | None = None            # ISO date, only when the source itself states it
    end: str | None = None
    basis: str = UNKNOWN                 # SOURCE_STATED | UNKNOWN
    source_note: str = ""                # the source's own sentence, kept verbatim

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RepresentedPeriod":
        return cls(start=d.get("start"), end=d.get("end"), basis=d.get("basis", UNKNOWN),
                   source_note=d.get("source_note", ""))


@dataclass
class InstitutionalFlowSnapshot:
    schema_version: str
    source: str                          # NSE | CDSL | NSDL
    source_class: str
    report_type: str                     # e.g. "fii_dii_daily", "fpi_daily", "fpi_fortnightly_sector"
    report_key: str                      # source-specific identity: date or fortnight-end, ISO
    report_date: str | None              # the date the source itself prints as "the report"
    data_as_of: str | None               # best single "as of" date for display
    represented_period: RepresentedPeriod
    retrieved_at: str
    first_retrieved_at: str              # the first time THIS report_key was ever captured
    status: str
    facts: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    source_url: str = ""
    source_reference: str = ""
    raw_sha256: str = ""
    records_checksum_value: str = ""
    parser_version: str = "1.0"
    revision: int = 1
    bootstrap: bool = False              # True: no prior snapshot of this source existed at all
    capture_mode: str = REPORT_JOB
    reason: str = ""
    connectivity_status: str = "NOT_ATTEMPTED"

    def seal(self) -> "InstitutionalFlowSnapshot":
        self.records_checksum_value = records_checksum(self.facts)
        return self

    @property
    def validated(self) -> bool:
        return self.status in VALIDATED

    def to_dict(self) -> dict:
        d = asdict(self)
        d["represented_period"] = self.represented_period.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InstitutionalFlowSnapshot":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        known["represented_period"] = RepresentedPeriod.from_dict(d.get("represented_period") or {})
        return cls(**known)

    def summary(self) -> dict:
        return {"source": self.source, "source_class": self.source_class,
                "report_type": self.report_type, "report_key": self.report_key,
                "report_date": self.report_date, "data_as_of": self.data_as_of,
                "represented_period": self.represented_period.to_dict(),
                "retrieved_at": self.retrieved_at, "first_retrieved_at": self.first_retrieved_at,
                "status": self.status, "revision": self.revision, "bootstrap": self.bootstrap,
                "capture_mode": self.capture_mode,
                "records_checksum": self.records_checksum_value,
                "fact_count": len(self.facts), "reason": self.reason[:300]}


__all__ = ["SCHEMA_VERSION", "NSE", "CDSL", "NSDL", "SOURCES", "EXCHANGE_PROVISIONAL",
           "DEPOSITORY_REPORTED", "DEPOSITORY_FORTNIGHTLY", "SOURCE_CLASS", "SUCCESS",
           "SOURCE_UNAVAILABLE", "PARSE_ERROR", "VALIDATION_FAILED", "VALIDATED", "FAILED",
           "NEW_REPORT", "UNCHANGED", "REVISED", "PERSISTED_SNAPSHOT", "CAPTURED_THIS_RUN",
           "HISTORICAL_SNAPSHOT_UNAVAILABLE", "NOT_CAPTURED", "SYNTHETIC", "SOURCE_STATED",
           "UNKNOWN", "REPORT_JOB", "PRE_FALLBACK", "records_checksum", "raw_checksum",
           "RepresentedPeriod", "InstitutionalFlowSnapshot"]

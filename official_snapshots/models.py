"""Official daily snapshot vocabulary.

A snapshot is the COMPLETE validated state of one official list for one trading session, as the
exchange published it - captured whether or not anything on it is ever selected for a video.
Selection (IPO events, Exchange Watch changes) happens later, from the stored snapshot.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "official-snapshot-1.0"
MANIFEST_SCHEMA_VERSION = "official-snapshot-manifest-1.0"

# snapshot kinds and their file stems (revision 1 = <stem>.json, later = <stem>.r<N>.json)
IPO, FNO_BAN, ASM, GSM, ESM = "IPO", "FNO_BAN", "ASM", "GSM", "ESM"
KINDS = (IPO, FNO_BAN, ASM, GSM, ESM)
EXCHANGE_KINDS = (FNO_BAN, ASM, GSM, ESM)
FILE_STEM = {IPO: "ipo_snapshot", FNO_BAN: "fno_ban_snapshot", ASM: "asm_snapshot",
             GSM: "gsm_snapshot", ESM: "esm_snapshot"}
MANIFEST_NAME = "official_snapshot_manifest.json"

# acquisition status of one snapshot
SUCCESS = "SUCCESS"                        # read + validated, >= 1 record
NO_DATA = "NO_DATA"                        # read + validated, the list is empty
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"  # the request actually failed (timeout/blocked/HTTP)
PARSE_ERROR = "PARSE_ERROR"                # the source answered; the payload did not parse
VALIDATION_FAILED = "VALIDATION_FAILED"    # parsed, but failed validation (e.g. a stale date)
NOT_SUPPORTED = "NOT_SUPPORTED"            # no adapter yet - never queried (not a failure)
VALIDATED = frozenset({SUCCESS, NO_DATA})
FAILED = frozenset({SOURCE_UNAVAILABLE, PARSE_ERROR, VALIDATION_FAILED})

# how a consumer (POST / PRE / replay) obtained an input
PERSISTED_SNAPSHOT = "PERSISTED_SNAPSHOT"            # read back from durable state
CAPTURED_THIS_RUN = "CAPTURED_THIS_RUN"              # captured by this run (fallback), persisted
SNAPSHOT_NOT_CAPTURED = "SNAPSHOT_NOT_CAPTURED"      # current session, none stored
HISTORICAL_SNAPSHOT_UNAVAILABLE = "HISTORICAL_SNAPSHOT_UNAVAILABLE"   # replay, none preserved
SYNTHETIC = "SYNTHETIC"

# who captured it
REPORT_JOB, POST_FALLBACK, PRE_FALLBACK = "REPORT_JOB", "POST_FALLBACK", "PRE_FALLBACK"


def records_checksum(records) -> str:
    """sha256 over the canonical JSON of the normalized records - what "the same snapshot"
    means when a later acquisition is compared with a stored one."""
    blob = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class OfficialSnapshot:
    kind: str
    session_date: str                 # the trading session this capture belongs to (D)
    source_date: str | None           # the date the official list is FOR (its own date)
    source_name: str
    source_reference: str
    retrieved_at: str | None
    status: str
    connectivity_status: str          # operations.connectivity verdict, or NOT_ATTEMPTED
    reason: str = ""
    records: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    capture_mode: str = REPORT_JOB
    expected_list_date: str | None = None
    revision: int = 1
    schema_version: str = SCHEMA_VERSION
    record_count: int = 0
    checksum: str = ""

    def seal(self) -> "OfficialSnapshot":
        self.record_count = len(self.records)
        self.checksum = records_checksum(self.records)
        return self

    @property
    def validated(self) -> bool:
        return self.status in VALIDATED

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OfficialSnapshot":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    def summary(self, file: str | None = None) -> dict:
        """The manifest / audit view - no records."""
        return {"file": file, "revision": self.revision, "status": self.status,
                "connectivity_status": self.connectivity_status, "reason": self.reason[:300],
                "source_date": self.source_date, "source_name": self.source_name,
                "source_reference": self.source_reference, "retrieved_at": self.retrieved_at,
                "record_count": self.record_count, "checksum": self.checksum,
                "capture_mode": self.capture_mode, "schema_version": self.schema_version}


__all__ = ["SCHEMA_VERSION", "MANIFEST_SCHEMA_VERSION", "KINDS", "EXCHANGE_KINDS", "FILE_STEM",
           "MANIFEST_NAME", "IPO", "FNO_BAN", "ASM", "GSM", "ESM", "SUCCESS", "NO_DATA",
           "SOURCE_UNAVAILABLE", "PARSE_ERROR", "VALIDATION_FAILED", "NOT_SUPPORTED",
           "VALIDATED", "FAILED", "PERSISTED_SNAPSHOT", "CAPTURED_THIS_RUN",
           "SNAPSHOT_NOT_CAPTURED", "HISTORICAL_SNAPSHOT_UNAVAILABLE", "SYNTHETIC",
           "REPORT_JOB", "POST_FALLBACK", "PRE_FALLBACK", "records_checksum",
           "OfficialSnapshot"]

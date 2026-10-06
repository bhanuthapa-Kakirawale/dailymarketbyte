"""Market Events Engine V1 vocabulary.

A `MarketEvent` is one official filing/notification - a board-meeting intimation, an IPO/OFS
dated event, a government securities auction, a buyback, an open offer, a delisting - captured
immutably by `event_key` (never a shared "session date": each filing has its own identity and
can itself be revised later, exactly like `institutional_flows.InstitutionalFlowSnapshot`, just
keyed by (family, event_key) instead of (source, report_key) because one family carries many
live events at once rather than one snapshot per source).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum

SCHEMA_VERSION = "market-events-1.0"


# Plain string constants (institutional_flows.NSE/CDSL/NSDL convention) so callers never need
# `EventFamily.EARNINGS.value` - the enum below is the same values, for anyone who wants typing.
EARNINGS = "EARNINGS"                             # board-meeting / result-date filing only
IPO = "IPO"                                        # not acquired here - see docs: ipo_watch
OFS = "OFS"                                        # offer-for-sale, own event type
GOVT_SECURITIES_AUCTION = "GOVT_SECURITIES_AUCTION"  # RBI T-Bill / G-Sec / SDL
BUYBACK = "BUYBACK"
OPEN_OFFER = "OPEN_OFFER"                          # takeover / SAST open offer
DELISTING = "DELISTING"


class EventFamily(str, Enum):
    EARNINGS = EARNINGS
    IPO = IPO
    OFS = OFS
    GOVT_SECURITIES_AUCTION = GOVT_SECURITIES_AUCTION
    BUYBACK = BUYBACK
    OPEN_OFFER = OPEN_OFFER
    DELISTING = DELISTING


ALL_FAMILIES = (EARNINGS, IPO, OFS, GOVT_SECURITIES_AUCTION, BUYBACK, OPEN_OFFER, DELISTING)

# auction sub_type - only ever set when the source itself names the instrument
TBILL_91, TBILL_182, TBILL_364 = "TBILL_91", "TBILL_182", "TBILL_364"
GSEC_NEW, GSEC_REISSUE, SDL = "GSEC_NEW", "GSEC_REISSUE", "SDL"
AUCTION_INSTRUMENTS = (TBILL_91, TBILL_182, TBILL_364, GSEC_NEW, GSEC_REISSUE, SDL)

# lifecycle status - a family only ever uses the subset its own filing can actually state
ANNOUNCED = "ANNOUNCED"
SCHEDULED = "SCHEDULED"
REVISED_DATE = "REVISED_DATE"
OPEN = "OPEN"
CLOSED = "CLOSED"
COMPLETED = "COMPLETED"
WITHDRAWN = "WITHDRAWN"
CANCELLED = "CANCELLED"
TERMINAL_STATUSES = frozenset({COMPLETED, WITHDRAWN, CANCELLED})

# acquisition-attempt status - institutional_flows vocabulary plus NOT_SUPPORTED_YET, which
# mirrors official_snapshots.NOT_SUPPORTED: a family this pass never attempts, not a failure.
SUCCESS = "SUCCESS"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"      # the request itself failed
PARSE_ERROR = "PARSE_ERROR"                    # answered; shape did not match what we parse
VALIDATION_FAILED = "VALIDATION_FAILED"        # parsed; internal date/field check failed
NOT_SUPPORTED_YET = "NOT_SUPPORTED_YET"        # no adapter attempted this pass - not a failure
VALIDATED = frozenset({SUCCESS})
FAILED = frozenset({SOURCE_UNAVAILABLE, PARSE_ERROR, VALIDATION_FAILED})

# change state vs the previously stored revision of the SAME event_key
NEW_EVENT = "NEW_EVENT"
UNCHANGED = "UNCHANGED"
REVISED = "REVISED"
CANCELLED_CHANGE = "CANCELLED_CHANGE"      # a REVISED write whose new status is terminal/adverse

# how a PRE/POST/Desk consumer obtained an input
PERSISTED_SNAPSHOT = "PERSISTED_SNAPSHOT"
CAPTURED_THIS_RUN = "CAPTURED_THIS_RUN"
HISTORICAL_SNAPSHOT_UNAVAILABLE = "HISTORICAL_SNAPSHOT_UNAVAILABLE"
NOT_CAPTURED = "NOT_CAPTURED"
SYNTHETIC = "SYNTHETIC"

REPORT_JOB, PRE_FALLBACK = "REPORT_JOB", "PRE_FALLBACK"


def event_checksum(facts) -> str:
    """sha256 over the canonical JSON of the normalized facts - never the raw page, so a page's
    viewstate/session-token churn can never make an unchanged filing look new."""
    blob = json.dumps(facts, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def raw_checksum(raw) -> str:
    blob = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


@dataclass
class MarketEvent:
    schema_version: str
    family: str                          # EventFamily value
    event_key: str                       # f"{family}:{symbol or 'MARKET'}:{natural_id}" -
                                          # natural_id is the filing's OWN reference (NSE seqnum,
                                          # RBI release no.) where stated, else a deterministic
                                          # hash of (symbol, family, first-seen date) - never a
                                          # sequence number this pipeline assigns itself
    symbol: str | None                   # None for GOVT_SECURITIES_AUCTION (market-wide)
    company: str
    status: str
    sub_type: str | None                 # an AUCTION_INSTRUMENTS member, or None
    data_as_of: str | None               # the date the filing/notification is dated/for
    source_name: str
    source_reference: str
    facts: list = field(default_factory=list)      # family-specific payload rows, the exchange's
                                                    # own field names/values ONLY - a missing
                                                    # figure is omitted, never inferred (mirrors
                                                    # IPOEvent's rule)
    source_text: str = ""                # the filing's own line, kept verbatim where useful
    retrieved_at: str = ""
    first_retrieved_at: str = ""
    status_capture: str = SUCCESS        # acquisition SUCCESS/SOURCE_UNAVAILABLE/.../NOT_SUPPORTED_YET
    raw_sha256: str = ""
    records_checksum_value: str = ""
    parser_version: str = "1.0"
    revision: int = 1
    bootstrap: bool = False              # True: no prior revision of this event_key existed
    capture_mode: str = REPORT_JOB
    reason: str = ""
    connectivity_status: str = "NOT_ATTEMPTED"
    publication_rights_status: str = "REVIEW_REQUIRED"   # conservative default

    def seal(self) -> "MarketEvent":
        # Unlike institutional_flows (acquisition status only), a MarketEvent carries a business
        # lifecycle status (SCHEDULED/OPEN/CANCELLED/...) that is itself part of what changed -
        # a status-only revision (e.g. a filing getting cancelled) must never checksum as
        # UNCHANGED just because `facts` didn't move.
        self.records_checksum_value = event_checksum({"status": self.status, "facts": self.facts})
        return self

    @property
    def validated(self) -> bool:
        return self.status_capture in VALIDATED

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MarketEvent":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    def summary(self) -> dict:
        return {"family": self.family, "event_key": self.event_key, "symbol": self.symbol,
                "company": self.company, "status": self.status, "sub_type": self.sub_type,
                "data_as_of": self.data_as_of, "retrieved_at": self.retrieved_at,
                "first_retrieved_at": self.first_retrieved_at, "status_capture": self.status_capture,
                "revision": self.revision, "bootstrap": self.bootstrap,
                "capture_mode": self.capture_mode,
                "records_checksum": self.records_checksum_value, "fact_count": len(self.facts),
                "reason": (self.reason or "")[:300]}


@dataclass
class FamilyFetchResult:
    """What one family's fetcher returns per capture attempt."""
    status: str                          # SUCCESS / SOURCE_UNAVAILABLE / PARSE_ERROR /
                                          # VALIDATION_FAILED / NOT_SUPPORTED_YET
    events: list = field(default_factory=list)      # [MarketEvent], only when status == SUCCESS
    connectivity: str = "NOT_ATTEMPTED"
    reason: str = ""


__all__ = ["SCHEMA_VERSION", "EARNINGS", "IPO", "OFS", "GOVT_SECURITIES_AUCTION", "BUYBACK",
           "OPEN_OFFER", "DELISTING", "EventFamily", "ALL_FAMILIES", "TBILL_91", "TBILL_182",
           "TBILL_364", "GSEC_NEW", "GSEC_REISSUE", "SDL", "AUCTION_INSTRUMENTS", "ANNOUNCED",
           "SCHEDULED",
           "REVISED_DATE", "OPEN", "CLOSED", "COMPLETED", "WITHDRAWN", "CANCELLED",
           "TERMINAL_STATUSES", "SUCCESS", "SOURCE_UNAVAILABLE", "PARSE_ERROR",
           "VALIDATION_FAILED", "NOT_SUPPORTED_YET", "VALIDATED", "FAILED", "NEW_EVENT",
           "UNCHANGED", "REVISED", "CANCELLED_CHANGE", "PERSISTED_SNAPSHOT", "CAPTURED_THIS_RUN",
           "HISTORICAL_SNAPSHOT_UNAVAILABLE", "NOT_CAPTURED", "SYNTHETIC", "REPORT_JOB",
           "PRE_FALLBACK", "event_checksum", "raw_checksum", "MarketEvent", "FamilyFetchResult"]

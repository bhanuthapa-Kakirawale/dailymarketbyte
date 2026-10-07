"""IPO WATCH domain: official, source-backed primary-market facts. Missing values are OMITTED,
never inferred. There is deliberately no field for grey-market premium, a rating, a fair value,
an expected listing price or any score - V1 cannot represent them, so it cannot publish them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum


class BoardType(str, Enum):
    MAINBOARD = "MAINBOARD"
    SME = "SME"


class IPOStatus(str, Enum):
    UPCOMING = "UPCOMING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    ALLOTMENT = "ALLOTMENT"
    LISTING = "LISTING"
    LISTED = "LISTED"


@dataclass(frozen=True)
class Subscription:
    """Bid / subscription multiples as the EXCHANGE published them. `as_of` is the exchange's
    own update time - without it nothing here is published (no DATA AS OF, no number)."""
    total: float | None = None
    retail: float | None = None
    qib: float | None = None
    nii: float | None = None
    employee: float | None = None
    as_of: dt.datetime | None = None
    source_name: str = ""
    source_reference: str = ""


@dataclass(frozen=True)
class FinancialRow:
    label: str                 # "Revenue from operations", "Profit after tax"
    period: str                # "FY26"
    value_crore: float
    document: str              # "RHP"
    page_reference: str        # "RHP p. 312 (Restated Financial Information)"


@dataclass(frozen=True)
class RiskFact:
    category: str              # CUSTOMER_CONCENTRATION / DEBT / LITIGATION / ...
    text: str                  # objective, sourced: "Top three customers were 48% of revenue."
    document: str
    page_reference: str


@dataclass(frozen=True)
class ListingOutcome:
    """Historical facts, only AFTER the listing session: never a judgement."""
    issue_price: float
    listing_price: float | None = None     # the listing-session open
    close_price: float | None = None
    session: dt.date | None = None
    source_name: str = ""
    source_reference: str = ""

    def change_pct(self, price: float | None) -> float | None:
        if price is None or not self.issue_price:
            return None
        return (price / self.issue_price - 1) * 100


@dataclass
class IPOEvent:
    company_name: str
    board_type: BoardType
    status: IPOStatus
    source_name: str
    source_reference: str
    data_as_of: dt.date
    symbol: str | None = None
    issue_open_date: dt.date | None = None
    issue_close_date: dt.date | None = None
    allotment_date: dt.date | None = None
    listing_date: dt.date | None = None
    issue_type: str | None = None             # "Book built", "Fixed price"
    fresh_issue_crore: float | None = None
    ofs_crore: float | None = None
    issue_size_crore: float | None = None
    price_band_low: float | None = None
    price_band_high: float | None = None
    lot_size: int | None = None
    subscription: Subscription | None = None
    objects_of_issue: list = field(default_factory=list)     # official "objects of the issue"
    financials: list = field(default_factory=list)            # [FinancialRow]
    risk_facts: list = field(default_factory=list)            # [RiskFact]
    listing: ListingOutcome | None = None
    official_document_reference: str | None = None            # "RHP dated 18 Sep 2026, sebi.gov.in/..."
    retrieved_at: str | None = None
    validation_status: str = "UNVALIDATED"
    publication_rights_status: str = "REVIEW_REQUIRED"
    notes: list = field(default_factory=list)
    # bid multiples exactly as the NSE issue list showed them ({category, value}). The list
    # carries NO update timestamp, so these are stored for the record and NEVER published
    # (a number without its DATA AS OF is not a publishable fact).
    unpublished_bid_multiples: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("board_type", "status"):
            d[k] = getattr(self, k).value
        return d

    # the exchange-list fields an official snapshot stores (offer-document figures are a
    # separate, hand-verified source applied at planning time, never captured into it)
    SNAPSHOT_FIELDS = ("company_name", "symbol", "board_type", "status", "issue_open_date",
                       "issue_close_date", "allotment_date", "listing_date", "issue_type",
                       "price_band_low", "price_band_high", "lot_size", "source_name",
                       "source_reference", "data_as_of", "retrieved_at", "notes",
                       "unpublished_bid_multiples")

    def to_snapshot_record(self) -> dict:
        out = {}
        for k in self.SNAPSHOT_FIELDS:
            v = getattr(self, k)
            out[k] = (v.value if isinstance(v, Enum) else
                      v.isoformat() if isinstance(v, dt.date) else
                      list(v) if isinstance(v, list) else v)
        return out

    @classmethod
    def from_snapshot_record(cls, d: dict) -> "IPOEvent":
        def day(k):
            return dt.date.fromisoformat(d[k]) if d.get(k) else None
        return cls(company_name=d["company_name"], board_type=BoardType(d["board_type"]),
                   status=IPOStatus(d["status"]), source_name=d["source_name"],
                   source_reference=d["source_reference"], data_as_of=day("data_as_of"),
                   symbol=d.get("symbol"), issue_open_date=day("issue_open_date"),
                   issue_close_date=day("issue_close_date"), allotment_date=day("allotment_date"),
                   listing_date=day("listing_date"), issue_type=d.get("issue_type"),
                   price_band_low=d.get("price_band_low"), price_band_high=d.get("price_band_high"),
                   lot_size=d.get("lot_size"), retrieved_at=d.get("retrieved_at"),
                   notes=list(d.get("notes") or []),
                   unpublished_bid_multiples=list(d.get("unpublished_bid_multiples") or []))


__all__ = ["BoardType", "IPOStatus", "Subscription", "FinancialRow", "RiskFact",
           "ListingOutcome", "IPOEvent"]

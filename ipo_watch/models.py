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

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("board_type", "status"):
            d[k] = getattr(self, k).value
        return d


__all__ = ["BoardType", "IPOStatus", "Subscription", "FinancialRow", "RiskFact",
           "ListingOutcome", "IPOEvent"]

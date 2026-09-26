"""EXCHANGE WATCH domain: official exchange/company events that name a security.

An `ExchangeEvent` is one row of an OFFICIAL list (the exchange's own file or page), dated by
that list, with its reference. It states a status the exchange published ("in the F&O ban
period", "on the long-term ASM list, Stage I") - never an interpretation of what the status
means for the price.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum


class EventFamily(str, Enum):
    FNO_BAN = "FNO_BAN"                          # implemented (fo_secban.csv)
    SURVEILLANCE_ASM = "SURVEILLANCE_ASM"        # implemented (parser; NSE website API)
    SURVEILLANCE_GSM = "SURVEILLANCE_GSM"        # implemented (parser; NSE website API)
    CORPORATE_EVENT = "CORPORATE_EVENT"          # model + policy; adapter planned
    BOARD_MEETING = "BOARD_MEETING"              # planned
    PRICE_BAND_CHANGE = "PRICE_BAND_CHANGE"      # planned
    TRADE_TO_TRADE = "TRADE_TO_TRADE"            # planned
    MWPL = "MWPL"                                # planned
    BULK_BLOCK_DEAL = "BULK_BLOCK_DEAL"          # planned
    CLARIFICATION = "CLARIFICATION"              # planned


IMPLEMENTED = (EventFamily.FNO_BAN, EventFamily.SURVEILLANCE_ASM, EventFamily.SURVEILLANCE_GSM)


class Change(str, Enum):
    NEW = "NEW"                  # on today's list, not on the previous stored list
    CONTINUING = "CONTINUING"    # on both
    UNKNOWN = "UNKNOWN"          # no previous stored list - "new" is never claimed


@dataclass(frozen=True)
class ExchangeEvent:
    event_id: str
    family: EventFamily
    symbol: str
    company: str
    status: str                  # the exchange's own words: "IN BAN PERIOD", "Stage I"
    detail: str                  # the exchange's own description (survDesc) - never ours
    data_as_of: dt.date          # the date the official list is FOR (trade date / list date)
    source_name: str
    source_reference: str
    retrieved_at: str | None = None
    change: Change = Change.UNKNOWN
    validation_status: str = "UNVALIDATED"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["family"] = self.family.value
        d["change"] = self.change.value
        d["data_as_of"] = self.data_as_of.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExchangeEvent":
        return cls(event_id=d["event_id"], family=EventFamily(d["family"]), symbol=d["symbol"],
                   company=d.get("company", ""), status=d.get("status", ""),
                   detail=d.get("detail", ""), data_as_of=dt.date.fromisoformat(d["data_as_of"]),
                   source_name=d["source_name"], source_reference=d["source_reference"],
                   retrieved_at=d.get("retrieved_at"), change=Change(d.get("change", "UNKNOWN")),
                   validation_status=d.get("validation_status", "UNVALIDATED"))


@dataclass
class SourceResult:
    source_name: str
    status: str                  # OK / UNAVAILABLE / INVALID / STALE
    events: list = field(default_factory=list)
    reason: str = ""
    list_date: dt.date | None = None
    retrieved_at: str | None = None

    def to_dict(self) -> dict:
        return {"source_name": self.source_name, "status": self.status, "reason": self.reason,
                "list_date": self.list_date.isoformat() if self.list_date else None,
                "retrieved_at": self.retrieved_at, "events": [e.to_dict() for e in self.events]}


__all__ = ["EventFamily", "ExchangeEvent", "Change", "SourceResult", "IMPLEMENTED"]

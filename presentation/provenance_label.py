"""The viewer-facing provenance label: SOURCE / DATA AS OF / FETCHED.

    SOURCE       the origin of the fact                      "NSE", "SEBI RHP", "RBI"
    DATA AS OF   the market/event time the fact represents   "25 SEP 2026 · 3:30 PM IST"
    FETCHED      when THIS system retrieved it - shown only for live / freshness-sensitive data

The three are never conflated: a session close is "as of" the 3:30 PM IST close no matter when
it was fetched, and a live reading carries both its exchange time and our fetch time. Plain text
attribution only - no exchange logos, nothing implying endorsement.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

from config import IST

NSE_CLOSE = dt.time(15, 30)


def _clock(t: dt.datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d %b %Y").lstrip("0").upper()


def fmt_session_close(d: dt.date) -> str:
    """An NSE end-of-day value represents the 3:30 PM IST close of its session."""
    return f"{fmt_date(d)} · {_clock(dt.datetime.combine(d, NSE_CLOSE))} IST"


def fmt_datetime(t: dt.datetime) -> str:
    if t.tzinfo is not None:
        t = t.astimezone(IST)
    return f"{fmt_date(t.date())} · {_clock(t)} IST"


def fmt_time(t: dt.datetime) -> str:
    if t.tzinfo is not None:
        t = t.astimezone(IST)
    return f"{_clock(t)} IST"


@dataclass(frozen=True)
class ProvenanceLabel:
    source: str                         # "NSE · YAHOO FINANCE"
    data_as_of: str                     # "25 SEP 2026 · 3:30 PM IST"
    as_of_label: str = "DATA AS OF"     # or "EVENT DATE", "FILED", "TRADE DATE"
    fetched: str | None = None          # "7:39 AM IST" - live data only

    def lines(self) -> list:
        out = [f"SOURCE: {self.source.upper()}", f"{self.as_of_label}: {self.data_as_of.upper()}"]
        if self.fetched:
            out[-1] += f" · FETCHED: {self.fetched.upper()}"
        return out

    def to_dict(self) -> dict:
        return asdict(self)


def session_label(source: str, session: dt.date) -> ProvenanceLabel:
    return ProvenanceLabel(source=source, data_as_of=fmt_session_close(session))


def live_label(source: str, market_time: dt.datetime, fetched_at: dt.datetime) -> ProvenanceLabel:
    return ProvenanceLabel(source=source, data_as_of=fmt_time(market_time),
                           fetched=fmt_time(fetched_at))


def event_label(source: str, event_date: dt.date, label: str = "EVENT DATE") -> ProvenanceLabel:
    return ProvenanceLabel(source=source, data_as_of=fmt_date(event_date), as_of_label=label)


__all__ = ["ProvenanceLabel", "fmt_date", "fmt_session_close", "fmt_datetime", "fmt_time",
           "session_label", "live_label", "event_label"]

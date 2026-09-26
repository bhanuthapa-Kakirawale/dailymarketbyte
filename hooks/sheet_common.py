"""Shared builders for fact sheets: turning one validated value into a `HookFact` (with the exact
number strings and the claims it licenses) and one validated series into a teaser-beat
payload. Formatting only - nothing here derives a market value."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

from config import fmt_in

from . import policy
from .models import BeatKind, HookFact, TeaserBeatOption
from .text import number_tokens

NONTEXT_KEYS = {"series", "volumes", "ma", "band", "numeric", "positive", "direction", "candles",
                "open", "high", "low", "close", "stock_norm", "bench_norm", "value_numeric"}

EVENT_PHRASE = {
    "BREAK_ABOVE_20D_RANGE": "Closed above its 20-day high",
    "BREAK_BELOW_20D_RANGE": "Closed below its 20-day low",
    "BREAK_ABOVE_50D_RANGE": "Closed above its 50-day high",
    "BREAK_BELOW_50D_RANGE": "Closed below its 50-day low",
    "CROSS_ABOVE_SMA20": "Moved above its 20-day average",
    "CROSS_BELOW_SMA20": "Slipped below its 20-day average",
    "CROSS_ABOVE_SMA50": "Moved above its 50-day average",
    "CROSS_BELOW_SMA50": "Slipped below its 50-day average",
    "RANGE_COMPRESSION": "Trading range has tightened",
}
EVENT_CLAIM = {
    "BREAK_ABOVE_20D_RANGE": "breakout", "BREAK_ABOVE_50D_RANGE": "breakout",
    "BREAK_BELOW_20D_RANGE": "breakdown", "BREAK_BELOW_50D_RANGE": "breakdown",
    "CROSS_ABOVE_SMA20": "cross_above", "CROSS_ABOVE_SMA50": "cross_above",
    "CROSS_BELOW_SMA20": "cross_below", "CROSS_BELOW_SMA50": "cross_below",
}
EVENT_CALLOUT = {"breakout": "Broke out here", "breakdown": "Fell out here",
                 "cross_above": "Crossed above here", "cross_below": "Crossed below here"}
EVENT_PRIORITY = ("BREAK_ABOVE_50D_RANGE", "BREAK_BELOW_50D_RANGE", "BREAK_ABOVE_20D_RANGE",
                  "BREAK_BELOW_20D_RANGE", "CROSS_ABOVE_SMA50", "CROSS_BELOW_SMA50",
                  "CROSS_ABOVE_SMA20", "CROSS_BELOW_SMA20", "RANGE_COMPRESSION")
FAMILY_CHIP = {"STRUCTURE": "Price", "VOLUME": "Volume", "RELATIVE_PERFORMANCE": "vs Nifty"}


def primary_event(events) -> str | None:
    return next((e for e in EVENT_PRIORITY if e in (events or [])), None)


def event_window(event: str | None) -> int | None:
    if not event:
        return None
    return 50 if "50" in event else (20 if "20" in event else None)


def event_direction(event: str | None) -> int:
    return 1 if event and "ABOVE" in event else (-1 if event and "BELOW" in event else 0)


def sign(v) -> int | None:
    if v is None:
        return None
    return 1 if v > 0 else (-1 if v < 0 else 0)


def pct(v: float, dec: int = 2) -> str:
    return f"{v:+.{dec}f}%"


def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s.upper()).strip("_")


def weekday(d: dt.date) -> str:
    return d.strftime("%A")


# --------------------------------------------------------------------------- facts
def fact(fact_id, kind, entity, statement, display, value=None, polarity=None, aliases=(),
         claims=(), anchor_free=False, refs=(), extra_numbers=()) -> HookFact:
    return HookFact(fact_id=fact_id, kind=kind, entity=entity, statement=statement,
                    display=display, value=value, polarity=polarity,
                    numbers=number_tokens(display, *extra_numbers),
                    aliases=tuple(dict.fromkeys(a for a in (entity, *aliases) if a)),
                    claims=frozenset(claims), anchor_free=anchor_free, source_refs=tuple(refs))


def move_claims(pct_value: float, quiet: float | None, big: float | None) -> set:
    out = set()
    if quiet is not None and abs(pct_value) < quiet:
        out.add("quiet")
    if big is not None and abs(pct_value) >= big:
        out.add("big")
    return out


def session_fact(session: dt.date, label: str = "session") -> HookFact:
    """The session's weekday and date, so a line may say "Monday's session"."""
    wd = weekday(session)
    display = f"{wd} {session.day} {session.strftime('%b')}"
    f = fact("session.day", "SESSION", wd, f"The {label} this Short covers: {session:%A %d %B %Y}.",
             display, aliases=(wd[:3],), anchor_free=True)
    return replace(f, unit_words=(session.strftime("%b").lower(), session.strftime("%B").lower()))


def count_fact(fact_id, n: int, noun: str, statement: str, refs=(), units=None) -> HookFact:
    """A count licenses exactly one number - the count itself - and only in front of its own
    noun ("5 stocks"), whatever the rest of its label says."""
    f = fact(fact_id, "COUNT", f"{n} {noun}", statement, f"{n} {noun}", value=float(n),
             anchor_free=True, refs=refs)
    return replace(f, numbers=(str(n),), aliases=(),
                   unit_words=tuple(units or (noun.split()[0],)))


# --------------------------------------------------------------------------- beats
def strings_of(payload) -> tuple:
    out = []

    def walk(v, key=None):
        if key in NONTEXT_KEYS:
            return
        if isinstance(v, str):
            if v:
                out.append(v)
        elif isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, k)
        elif isinstance(v, (list, tuple)):
            for vv in v:
                walk(vv)
    walk(payload)
    return tuple(dict.fromkeys(out))


def beat(beat_id, kind: BeatKind, description, fact_ids, payload) -> TeaserBeatOption:
    return TeaserBeatOption(beat_id=beat_id, kind=kind, description=description,
                            fact_ids=tuple(fact_ids), payload=payload, strings=strings_of(payload))


def breakout_payload(symbol, change, positive, closes, event, band=None, ma=None,
                     edge_label=None) -> dict:
    claim = EVENT_CLAIM.get(event or "")
    return {"symbol": symbol, "change": change, "positive": positive,
            "series": [float(v) for v in closes],
            "band": [float(band[0]), float(band[1])] if band else None,
            "ma": [None if v is None else float(v) for v in ma] if ma else None,
            "direction": event_direction(event), "callout": EVENT_CALLOUT.get(claim, ""),
            "edge_label": edge_label or "", "event_label": EVENT_PHRASE.get(event or "", "")}


def rvol_label(rvol: float) -> str:
    return f"{rvol:.1f}× normal volume"


def price(v, dec=0) -> str:
    return fmt_in(v, dec)


__all__ = ["fact", "move_claims", "session_fact", "count_fact", "beat", "strings_of",
           "breakout_payload", "rvol_label", "pct", "price", "sign", "slug", "weekday",
           "primary_event", "event_window", "event_direction", "EVENT_PHRASE", "EVENT_CLAIM",
           "FAMILY_CHIP", "policy"]

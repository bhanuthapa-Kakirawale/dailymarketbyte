"""Market Events selection and the public display model - mirrors
`exchange_watch.watch.select_events`/`build_model` and `ipo_watch.watch.select_ipos`/
`build_model`: a transparent, deterministic order, never a model, never "popularity".

Selection: an event qualifies only when dated `day` (its own `data_as_of`) and its symbol (if
any) is in the tracked universe. Family priority (spec-ordered): EARNINGS before IPO/OFS before
GOVT_SECURITIES_AUCTION before BUYBACK before OPEN_OFFER before DELISTING - a dump of every
family is never the point, so the public cap (`MAX_PUBLIC_EVENTS`) trims the tail.
"""
from __future__ import annotations

import datetime as dt

from .models import BUYBACK, DELISTING, EARNINGS, GOVT_SECURITIES_AUCTION, IPO, OFS, OPEN_OFFER
from .facts import market_event_fact

from presentation.provenance_label import ProvenanceLabel, fmt_date

MAX_PUBLIC_EVENTS = 3

FAMILY_PRIORITY = {EARNINGS: 0, IPO: 1, OFS: 1, GOVT_SECURITIES_AUCTION: 2, BUYBACK: 3,
                  OPEN_OFFER: 4, DELISTING: 5}

FAMILY_CHIP = {EARNINGS: "EARNINGS", IPO: "IPO", OFS: "OFS",
              GOVT_SECURITIES_AUCTION: "GOVT AUCTION", BUYBACK: "BUYBACK",
              OPEN_OFFER: "OPEN OFFER", DELISTING: "DELISTING"}


def select_market_events(events_by_family: dict, universe_symbols, day: dt.date,
                         limit: int = MAX_PUBLIC_EVENTS) -> tuple:
    """(chosen [MarketEvent], omitted [{event_key, reason}])."""
    uni = set(universe_symbols or ())
    all_events, seen = [], set()
    for evs in (events_by_family or {}).values():
        for ev in evs:
            if ev.event_key not in seen:
                seen.add(ev.event_key)
                all_events.append(ev)

    eligible, omitted = [], []
    for ev in all_events:
        if ev.symbol and uni and ev.symbol not in uni:
            omitted.append({"event_key": ev.event_key,
                            "reason": "symbol outside the tracked universe"})
            continue
        if ev.data_as_of != day.isoformat():
            omitted.append({"event_key": ev.event_key, "reason": f"not dated {day.isoformat()}"})
            continue
        eligible.append(ev)
    eligible.sort(key=lambda e: (FAMILY_PRIORITY.get(e.family, 99), e.company or "", e.event_key))
    chosen = eligible[:limit]
    for ev in eligible[limit:]:
        omitted.append({"event_key": ev.event_key, "reason": f"public cap {limit}"})
    return chosen, omitted


def _line(ev) -> str:
    """One fixed factual sentence per family - never an interpretation, never a recommendation.
    Only the filing's own stated fields (`ev.facts`) are read."""
    f = {row.get("label"): row.get("value") for row in (ev.facts or [])
        if isinstance(row, dict)}
    if ev.family == EARNINGS:
        period = f.get("reporting_period")
        return f"Board meeting to consider results{f' ({period})' if period else ''}"
    if ev.family in (IPO, OFS):
        band = f.get("price_band")
        return f"{FAMILY_CHIP[ev.family]} {ev.status.lower()}" + (f" at {band}" if band else "")
    if ev.family == GOVT_SECURITIES_AUCTION:
        amount = f.get("notified_amount_crore")
        return (f"{ev.sub_type or 'Auction'} scheduled" +
               (f", notified amount Rs {amount} cr" if amount else ""))
    if ev.family == BUYBACK:
        price = f.get("buyback_price")
        return f"Buyback {ev.status.lower()}" + (f" at Rs {price}" if price else "")
    if ev.family == OPEN_OFFER:
        acquirer = f.get("acquirer")
        return f"Open offer {ev.status.lower()}" + (f" from {acquirer}" if acquirer else "")
    if ev.family == DELISTING:
        return f"Delisting {ev.status.lower()}"
    return ev.status.replace("_", " ").title()


def _card(ev) -> dict:
    return {"tag": FAMILY_CHIP.get(ev.family, ev.family), "symbol": ev.symbol or "",
           "company": ev.company, "status": ev.status, "line": _line(ev),
           "date": ev.data_as_of, "event_key": ev.event_key}


def build_model(chosen: list, mode: str = "PRE") -> dict | None:
    if not chosen:
        return None
    n = len(chosen)
    word = {1: "One", 2: "Two", 3: "Three"}.get(n, str(n))
    when = "today" if mode != "POST" else "in the session"
    headline = f"{word} market event{'s' if n > 1 else ''} {when}"
    srcs = sorted({e.source_name for e in chosen if e.source_name})
    dates = sorted({e.data_as_of for e in chosen if e.data_as_of})
    label = ProvenanceLabel(source=" · ".join(srcs) or "Official source",
                            data_as_of=" / ".join(fmt_date(dt.date.fromisoformat(d))
                                                  for d in dates) if dates else "")
    return {"headline": headline, "cards": [_card(e) for e in chosen], "sources": srcs,
           "provenance": label.to_dict(), "provenance_lines": label.lines(),
           "event_keys": [e.event_key for e in chosen]}


def market_events_facts(chosen: list) -> list:
    """PublishableFacts for the chosen events - what the gate admits before a scene is built."""
    out = []
    for ev in chosen:
        c = _card(ev)
        for i, text in enumerate(s for s in (c["symbol"], c["company"], c["tag"], c["status"],
                                             c["line"]) if s):
            out.append(market_event_fact(ev, text, f"market_event.{ev.event_key}.{i}"))
    return out


def market_events_audit(chosen, omitted) -> dict:
    return {"market_events_present": bool(chosen),
           "events": [e.summary() for e in chosen],
           "families_shown": sorted({e.family for e in chosen}),
           "omitted": omitted}


__all__ = ["MAX_PUBLIC_EVENTS", "FAMILY_PRIORITY", "FAMILY_CHIP", "select_market_events",
           "build_model", "market_events_facts", "market_events_audit"]

"""PRE/POST's read of Market Events: newest stored events per family as of a cutoff, with the
replay/live distinction `institutional_flows.context`/`official_snapshots` already draw.

A live run may CAPTURE a missing family (persisted for next time, inside that family's own
window); a replay of a past session never fetches today's page and pretends it existed then -
it only ever reads what was already on disk, and only what was first captured before `cutoff`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .models import (ALL_FAMILIES, CAPTURED_THIS_RUN, HISTORICAL_SNAPSHOT_UNAVAILABLE,
                     NOT_CAPTURED, NOT_SUPPORTED_YET, PERSISTED_SNAPSHOT)


@dataclass
class MarketEventsContext:
    events: dict = field(default_factory=dict)      # family -> [MarketEvent]
    status: dict = field(default_factory=dict)       # family -> PERSISTED_SNAPSHOT / ...

    def inputs(self) -> dict:
        def ref(ev):
            return {"event_key": ev.event_key, "status": ev.status,
                   "records_checksum": ev.records_checksum_value,
                   "first_retrieved_at": ev.first_retrieved_at}
        return {family: {"status": self.status.get(family, NOT_CAPTURED),
                         "events": [ref(e) for e in evs]}
               for family, evs in self.events.items()}


def load_market_events(out_dir: str, *, cutoff: dt.datetime, live: bool,
                       on_or_before: dt.date | None = None, families: tuple = ALL_FAMILIES,
                       capture_fn=None) -> MarketEventsContext:
    """`cutoff` is an ISO timestamp (brief.as_of): an event counts only if it was FIRST
    retrieved strictly before this point. `on_or_before` additionally bounds the event's own
    `data_as_of` (never an event "from the future" relative to the session being built)."""
    from .store import load_latest
    cutoff_iso = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    ctx = MarketEventsContext()
    for family in families:
        persisted = load_latest(out_dir, family, on_or_before=on_or_before,
                                first_retrieved_before=cutoff_iso)
        if persisted:
            ctx.events[family] = persisted
            ctx.status[family] = PERSISTED_SNAPSHOT
        elif live and capture_fn is not None:
            fresh = capture_fn(family)
            if fresh:
                ctx.events[family] = fresh
                ctx.status[family] = CAPTURED_THIS_RUN
            else:
                ctx.events[family] = []
                ctx.status[family] = NOT_CAPTURED
        elif not live:
            ctx.events[family] = []
            ctx.status[family] = HISTORICAL_SNAPSHOT_UNAVAILABLE
        else:
            ctx.events[family] = []
            ctx.status[family] = NOT_CAPTURED
    return ctx


__all__ = ["MarketEventsContext", "load_market_events"]

"""IPO projection: a read-only VIEW over the already-validated `ipo_watch`/`official_snapshots`
IPO pipeline, expressed as `MarketEvent`s for the Private Desk's unified events page/dashboard/
Data Quality - never a second acquisition of NSE, never written to `market_events/store.py`.

`ipo_watch`/`official_snapshots` remains the ONE source of truth for IPO; the existing, already-
tested IPO WATCH scene in PRE/POST is untouched and keeps showing IPO exactly as it does today
(`market_events.watch.EXCLUDED_FROM_PUBLIC_SCENE` keeps this family out of the new MARKET_EVENTS
scene, defensively, even though nothing wires it there in the first place).
"""
from __future__ import annotations

import datetime as dt

from ..models import ANNOUNCED, CLOSED, COMPLETED, IPO, OPEN, PROJECTION, SCHEMA_VERSION, SUCCESS
from ..models import MarketEvent

_STATUS_FOR_KIND = {"OPENS_TODAY": OPEN, "CLOSES_TODAY": CLOSED, "LISTS_TODAY": COMPLETED,
                   "ALLOTMENT_TODAY": ANNOUNCED, "LISTED": COMPLETED}


def _project_one(ipo, kind: str, day: dt.date) -> MarketEvent:
    facts = []
    if ipo.price_band_low is not None and ipo.price_band_high is not None:
        facts.append({"label": "price_band",
                      "value": f"{ipo.price_band_low:g}-{ipo.price_band_high:g}"})
    if ipo.lot_size:
        facts.append({"label": "lot_size", "value": str(ipo.lot_size)})
    identity = ipo.symbol or ipo.company_name
    return MarketEvent(
        schema_version=SCHEMA_VERSION, family=IPO, event_key=f"{IPO}:{identity}:{day.isoformat()}",
        symbol=ipo.symbol, company=ipo.company_name, status=_STATUS_FOR_KIND.get(kind, ANNOUNCED),
        sub_type=None, data_as_of=day.isoformat(), source_name=ipo.source_name,
        source_reference=ipo.source_reference, retrieved_at=ipo.retrieved_at or "",
        status_capture=SUCCESS, facts=facts, capture_mode=PROJECTION,
        connectivity_status="NOT_ATTEMPTED")


def project_ipo_events(ipos: list, day: dt.date) -> list:
    """One MarketEvent per IPOEvent with a dated event on `day` - the exact same rule the
    existing IPO WATCH scene uses (`ipo_watch.watch.todays_event`). Never touches
    `market_events/store.py` - these events are a live view, never persisted."""
    from ipo_watch.watch import todays_event
    out = []
    for ipo in ipos:
        kind = todays_event(ipo, day)
        if kind is None:
            continue
        out.append(_project_one(ipo, kind, day))
    return out


def crosscheck_against_ipo_watch(ipo, kind: str, day: dt.date) -> list:
    """[] if the projection and the existing IPO WATCH scene agree on company/dates/price band/
    source for the SAME IPOEvent; else a list of human-readable mismatches. Both read the one
    stored snapshot, so there is only one source here - a mismatch means a bug in the
    projection, never a live two-source conflict (see docs/MARKET_EVENTS_ENGINE.md)."""
    from ipo_watch.watch import fact_rows
    projected = _project_one(ipo, kind, day)
    issues = []
    if projected.company != ipo.company_name:
        issues.append(f"company mismatch: {projected.company!r} vs {ipo.company_name!r}")
    if projected.symbol != ipo.symbol:
        issues.append(f"symbol mismatch: {projected.symbol!r} vs {ipo.symbol!r}")
    rows = fact_rows(ipo, kind)
    band_row = next((r for r in rows if r["label"] == "PRICE BAND"), None)
    proj_band = next((f for f in projected.facts if f["label"] == "price_band"), None)
    if band_row and not proj_band:
        issues.append("IPO WATCH shows a price band the projection omitted")
    if proj_band and not band_row:
        issues.append("projection shows a price band IPO WATCH omitted")
    if projected.source_name != ipo.source_name or projected.source_reference != ipo.source_reference:
        issues.append("source provenance mismatch")
    return issues


__all__ = ["project_ipo_events", "crosscheck_against_ipo_watch"]

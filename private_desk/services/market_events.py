"""Market Events Engine V1 - Private Desk read-only views.

Every value here is already-stored `MarketEvent` data (`market_events.store`, read-only). No
fetch happens on a page request - acquisition only ever runs in the REPORT job. A family with
no verified-reachable source this pass (`market_events.sources.DEFAULT_FETCHERS`) simply shows
no events - NOT_SUPPORTED_YET, never a failure - exactly like the engine's own PRE/POST posture.
"""
from __future__ import annotations

import datetime as dt

from market_events.models import ALL_FAMILIES, BUYBACK, DELISTING, EARNINGS, OPEN_OFFER

STOCK_FAMILIES = (EARNINGS, BUYBACK, OPEN_OFFER, DELISTING)


def _safe_date(iso: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _bucket(events: list, session: dt.date) -> dict:
    tomorrow = session + dt.timedelta(days=1)
    week_end = session + dt.timedelta(days=7)
    out = {"today": [], "tomorrow": [], "next_7_days": [], "recently_announced": []}
    for ev in sorted(events, key=lambda e: (e.data_as_of or "", e.company)):
        d = _safe_date(ev.data_as_of)
        if d is None:
            continue
        if d == session:
            out["today"].append(ev)
        elif d == tomorrow:
            out["tomorrow"].append(ev)
        elif session < d <= week_end:
            out["next_7_days"].append(ev)
        elif d < session:
            out["recently_announced"].append(ev)
    return out


def family_section(repo, family: str, session: dt.date) -> dict:
    events = repo.market_events_latest(family)
    buckets = _bucket(events, session)
    return {"status": "OK" if events else "UNAVAILABLE", "count": len(events), **buckets}


def all_sections(repo, session: dt.date) -> dict:
    return {family: family_section(repo, family, session) for family in ALL_FAMILIES}


def dashboard_section(repo, session: dt.date) -> dict:
    """Compact cross-family summary for the main dashboard panel - context only, never read by
    attention.py/candidates.py/the Radar order/Market Regime/PrivateCandidatePacket."""
    sections = all_sections(repo, session)
    today_counts = {family: len(s["today"]) for family, s in sections.items() if s["today"]}
    return {"sections": sections, "today_counts": today_counts,
           "any_today": sum(today_counts.values())}


def stock_official_rows(repo, symbol: str, session: dt.date) -> list:
    """Rows in the SAME shape `candidates.official_index()` already uses for FNO_BAN/ASM/GSM/
    IPO, so the stock page's existing "Official events" card can show these alongside them
    rather than needing a second, parallel card."""
    out = []
    for family in STOCK_FAMILIES:
        for ev in repo.market_events_latest(family, symbol=symbol, on_or_before=session):
            out.append({"kind": family, "status": ev.status,
                        "detail": (ev.facts[0].get("value") if ev.facts else "") or ev.status,
                        "list": None, "row_date": ev.data_as_of,
                        "source_name": ev.source_name, "source_reference": ev.source_reference,
                        "source_date": ev.data_as_of, "issue_open_date": None,
                        "issue_close_date": None, "listing_date": None})
    return out


def quality_rows(repo, session: dt.date | None) -> dict:
    """Data Quality panel: per-family latest/status, record count, NOT_SUPPORTED_YET flags,
    and recent capture attempts."""
    from market_events.sources import DEFAULT_FETCHERS
    out = {"families": {}, "attempts": []}
    if session is not None:
        for family in ALL_FAMILIES:
            events = repo.market_events_latest(family, on_or_before=session)
            out["families"][family] = {
                "status": "OK" if events else "NO_DATA", "count": len(events),
                "not_supported_yet": getattr(DEFAULT_FETCHERS.get(family), "not_supported_yet",
                                             False)}
    out["attempts"] = repo.market_events_attempts(days=3)[:10]
    return out


__all__ = ["STOCK_FAMILIES", "family_section", "all_sections", "dashboard_section",
           "stock_official_rows", "quality_rows"]

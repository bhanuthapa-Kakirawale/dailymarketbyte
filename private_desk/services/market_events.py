"""Market Events Engine V1 - Private Desk read-only views.

Every value here is already-stored `MarketEvent` data (`market_events.store`, read-only). No
fetch happens on a page request - acquisition only ever runs in the REPORT job. A family with
no verified-reachable source this pass (`market_events.sources.DEFAULT_FETCHERS`) simply shows
no events - NOT_SUPPORTED_YET, never a failure - exactly like the engine's own PRE/POST posture.
"""
from __future__ import annotations

import datetime as dt

from market_events.models import ALL_FAMILIES, BUYBACK, DELISTING, EARNINGS, IPO, OFS, OPEN_OFFER

# OFS names a specific company's shares (unlike GOVT_SECURITIES_AUCTION, which is market-wide),
# so - like EARNINGS/BUYBACK/OPEN_OFFER/DELISTING - it belongs on that symbol's stock page. IPO
# is deliberately excluded here: it already has its OWN stock-page presence via
# official_snapshots (candidates.official_index()'s OFFICIAL_KINDS), so including it here too
# would show it twice.
STOCK_FAMILIES = (EARNINGS, BUYBACK, OPEN_OFFER, DELISTING, OFS)


def _ipo_market_events(repo, session: dt.date) -> list:
    """IPO is never acquired by this engine (ipo_watch/official_snapshots remains the one
    source of truth) - this reads the ALREADY-CAPTURED official IPO snapshot (the same data
    the stock page's "Official events" card already reads) and projects it into MarketEvents
    for the Desk's unified pages. Never re-fetches NSE, never writes to market_events/store."""
    from ipo_watch import IPOEvent, apply_offer_document, load_offer_documents
    from market_events.sources.ipo_projection import project_ipo_events

    snap = repo.official_snapshot(session, IPO)
    if snap is None or snap.status not in ("SUCCESS", "NO_DATA"):
        return []
    ipos = [IPOEvent.from_snapshot_record(r) for r in snap.records]
    docs, _ = load_offer_documents()
    by_key = {(d.get("symbol") or d.get("company_name") or "").upper(): d for d in docs}
    for ipo in ipos:
        d = by_key.get((ipo.symbol or ipo.company_name).upper())
        if d:
            apply_offer_document(ipo, d)
    # project_ipo_events is single-day (mirrors ipo_watch.todays_event exactly); the Desk's
    # TODAY/TOMORROW/NEXT 7 DAYS/RECENTLY ANNOUNCED buckets need a window, so project each day
    # in it and union the results - a different date for the same IPO is a genuinely different
    # dated milestone (opens vs. closes vs. lists), never a duplicate.
    out, seen = [], set()
    for offset in range(-3, 8):
        for ev in project_ipo_events(ipos, session + dt.timedelta(days=offset)):
            if ev.event_key not in seen:
                seen.add(ev.event_key)
                out.append(ev)
    return out


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
    if family == IPO:
        events = _ipo_market_events(repo, session)
        buckets = _bucket(events, session)
        return {"status": "VIEW_ONLY", "count": len(events), "source": "ipo_watch_projection",
               **buckets}
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
            if family == IPO:
                events = _ipo_market_events(repo, session)
                out["families"][family] = {"status": "VIEW_ONLY", "count": len(events),
                                           "source": "ipo_watch_projection",
                                           "not_supported_yet": False}
                continue
            events = repo.market_events_latest(family, on_or_before=session)
            out["families"][family] = {
                "status": "OK" if events else "NO_DATA", "count": len(events),
                "not_supported_yet": getattr(DEFAULT_FETCHERS.get(family), "not_supported_yet",
                                             False)}
    out["attempts"] = repo.market_events_attempts(days=3)[:10]
    return out


__all__ = ["STOCK_FAMILIES", "family_section", "all_sections", "dashboard_section",
           "stock_official_rows", "quality_rows"]

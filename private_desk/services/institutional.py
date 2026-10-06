"""Institutional Flow Intelligence V1 - Private Desk read-only views.

Every value here is already-stored `InstitutionalFlowSnapshot` data (`institutional_flows.store`,
read-only) or the deterministic materiality context (`intelligence.flow_materiality`, pure
arithmetic over already-eligible facts). No fetch happens on a page request - acquisition only
ever runs in the REPORT/PRE jobs. Freshness here is its own, source-specific cadence rule (NSE
daily, CDSL daily, NSDL fortnightly) - deliberately NOT folded into `services.freshness`, which
would flag a fortnightly report as STALE every single day it is not published.
"""
from __future__ import annotations

import datetime as dt

from institutional_flows.models import CDSL, InstitutionalFlowSnapshot, NSDL, NSE
from institutional_flows.sector_flow import sector_flows

NSE_STALE_AFTER_DAYS = 1
CDSL_STALE_AFTER_DAYS = 3
NSDL_STALE_AFTER_DAYS = 20   # a fortnight plus processing slack


def _freshness(latest_date: dt.date | None, session: dt.date, stale_after: int) -> str:
    if latest_date is None:
        return "MISSING"
    age = (session - latest_date).days
    if age < 0:
        return "AHEAD"
    return "FRESH" if age <= stale_after else "STALE"


def nse_section(repo, session: dt.date) -> dict:
    snaps = [s for k, _p, s in repo.institutional_snapshots(NSE) if s.validated
            and _safe_date(k) and _safe_date(k) <= session]
    snaps.sort(key=lambda s: s.report_key, reverse=True)
    latest = snaps[0] if snaps else None
    history = snaps[:10]
    rows = []
    for snap in history:
        row = {"report_key": snap.report_key}
        for f in snap.facts:
            if f["metric"] == "net_investment":
                row[f["participant"].lower()] = f["value"]
        rows.append(row)
    return {"status": "OK" if latest else "UNAVAILABLE",
           "freshness": _freshness(_safe_date(latest.report_key) if latest else None, session,
                                   NSE_STALE_AFTER_DAYS),
           "latest": _nse_row(latest) if latest else None, "history": rows}


def _nse_row(snap: InstitutionalFlowSnapshot) -> dict:
    out = {"report_key": snap.report_key, "report_date": snap.report_date,
          "source": "NSE", "represented_period": snap.represented_period.source_note}
    for f in snap.facts:
        out[f"{f['participant'].lower()}_{f['metric']}"] = f["value"]
    return out


def cdsl_section(repo, session: dt.date) -> dict:
    snap = repo.institutional_latest(CDSL, on_or_before=session)
    if not snap:
        return {"status": "UNAVAILABLE", "freshness": "MISSING", "latest": None, "categories": []}
    categories = {}
    for f in snap.facts:
        if f["unit"] != "INR_CRORE":
            continue
        key = (f["category"], f["route"])
        categories.setdefault(key, {})[f["metric"]] = f["value"]
    rows = [{"category": cat, "route": route, "gross_purchases": v.get("gross_purchases"),
            "gross_sales": v.get("gross_sales"), "net_investment": v.get("net_investment")}
           for (cat, route), v in sorted(categories.items())]
    return {"status": "OK",
           "freshness": _freshness(_safe_date(snap.report_key), session, CDSL_STALE_AFTER_DAYS),
           "latest": {"report_key": snap.report_key, "report_date": snap.report_date,
                     "represented_period_basis": snap.represented_period.basis,
                     "represented_period_note": snap.represented_period.source_note,
                     "bootstrap": snap.bootstrap},
           "categories": rows}


def nsdl_section(repo, session: dt.date) -> dict:
    latest = repo.institutional_latest(NSDL, on_or_before=session)
    if not latest:
        return {"status": "UNAVAILABLE", "freshness": "MISSING", "latest": None,
               "previous_period": None, "sectors": []}
    all_nsdl = sorted((k for k, _p, s in repo.institutional_snapshots(NSDL)
                       if s.validated and _safe_date(k) and _safe_date(k) <= session),
                      reverse=True)
    previous_key = next((k for k in all_nsdl if k != latest.report_key), None)
    flows = sector_flows(latest)
    sectors = sorted(({"sector": f.display_sector, "nsdl_sector": f.nsdl_sector,
                      "current_cr": f.current_cr, "previous_cr": f.previous_cr,
                      "change_cr": f.change_cr, "direction": f.direction}
                     for f in flows if f.display_sector),
                    key=lambda r: r["sector"])
    auc = {f["sector"]: {"current": None, "previous": None} for f in latest.facts
          if f["metric"] == "auc"}
    for f in latest.facts:
        if f["metric"] == "auc" and f["sector"] in auc:
            auc[f["sector"]][f["period"]] = f["value"]
    return {"status": "OK",
           "freshness": _freshness(_safe_date(latest.report_key), session, NSDL_STALE_AFTER_DAYS),
           "latest": {"report_key": latest.report_key,
                     "period_note": latest.represented_period.source_note,
                     "bootstrap": latest.bootstrap},
           "previous_period": previous_key, "sectors": sectors,
           "auc": {s: v for s, v in auc.items() if s in {r["nsdl_sector"] for r in sectors}}}


def dashboard_section(repo, session: dt.date) -> dict:
    """Compact cross-source summary for the main dashboard panel."""
    return {"nse": nse_section(repo, session), "cdsl": cdsl_section(repo, session),
           "nsdl": nsdl_section(repo, session)}


def sector_context(repo, session: dt.date, display_sector: str) -> dict:
    """What `services.stock.stock_detail`'s panel F needs for one display sector - the
    mapped sector's latest/previous fortnight flow, or an explicit UNAVAILABLE/UNMAPPED."""
    if not display_sector:
        return {"status": "UNAVAILABLE", "reason": "stock has no mapped sector"}
    nsdl = nsdl_section(repo, session)
    if nsdl["status"] != "OK":
        return {"status": "UNAVAILABLE", "reason": "no NSDL fortnightly data stored yet"}
    row = next((r for r in nsdl["sectors"] if r["sector"] == display_sector), None)
    if row is None:
        return {"status": "UNMAPPED", "reason": f"sector {display_sector!r} is not in the "
                                                "latest NSDL fortnightly report"}
    return {"status": "OK", "period_note": nsdl["latest"]["period_note"], **row}


def quality_rows(repo, session: dt.date | None) -> dict:
    """Data Quality panel: per-source latest/previous, status, last attempt, NSDL discovery
    status + option count, and the mapped/unmapped sector split."""
    out = {"nse": None, "cdsl": None, "nsdl": None, "sector_mapping": None, "attempts": []}
    if session is not None:
        out["nse"] = nse_section(repo, session)
        out["cdsl"] = cdsl_section(repo, session)
        nsdl = nsdl_section(repo, session)
        out["nsdl"] = nsdl
        if nsdl["status"] == "OK":
            from institutional_flows.sector_map import NSDL_TO_SECTOR, NOT_A_SECTOR, UNMAPPED
            mapped = sum(1 for v in NSDL_TO_SECTOR.values() if v not in (UNMAPPED, NOT_A_SECTOR))
            unmapped = sum(1 for v in NSDL_TO_SECTOR.values() if v == UNMAPPED)
            out["sector_mapping"] = {"mapped": mapped, "unmapped": unmapped,
                                     "unmapped_labels": sorted(k for k, v in NSDL_TO_SECTOR.items()
                                                               if v == UNMAPPED)}
    out["attempts"] = repo.institutional_attempts(days=3)[:10]
    return out


def _safe_date(key: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(key)
    except (TypeError, ValueError):
        return None


__all__ = ["nse_section", "cdsl_section", "nsdl_section", "dashboard_section",
           "sector_context", "quality_rows"]

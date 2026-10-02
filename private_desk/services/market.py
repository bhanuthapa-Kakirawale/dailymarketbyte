"""Market DIAGNOSTICS and sector pulse - the facts of exactly one session.

The regime LABEL is not computed here: it comes from the deterministic classifier in
`private_desk.regime` (docs/PRIVATE_MARKET_REGIME.md). This module shows the plain facts beside it:
* index / VIX / flows from the canonical report of exactly this session (its validation status
  travels with each number);
* NIFTY 200 breadth, unusual volume and range events from the Market Structure artifact of
  exactly this session, re-aggregated and reconciled on load (`market_structure.store.
  load_snapshot` raises if the stored counts don't reproduce);
* per-sector counts from that same artifact (sector = NSE's Industry column), plus the number of
  recorded Radar candidates per sector.

Universes never mix: NSE sectoral INDEX moves (from the report) are a separate table from the
NIFTY 200 industry-sector breadth (from Market Structure).
"""
from __future__ import annotations

import datetime as dt
import statistics

from ..repository import DeskRepository

METRICS = ("ADVANCES", "DECLINES", "UNUSUAL_VOLUME", "RANGE_UP", "RANGE_DOWN")


def _fact_status(report: dict, metric: str, instrument: str) -> str | None:
    for f in report.get("facts") or []:
        if f.get("metric") == metric and f.get("instrument") == instrument:
            return f.get("validation_status")
    return None


def report_diagnostics(repo: DeskRepository, session: dt.date) -> dict:
    report, row, status = repo.report(session)
    if report is None:
        return {"status": status, "report_id": (row or {}).get("report_id")}
    n = report.get("nifty") or {}
    flows = report.get("institutional_flows") or {}
    return {
        "status": status, "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at"),
        "publication_ready": (report.get("validation_summary") or {}).get("publication_ready"),
        "nifty": {"close": n.get("close"), "change_pct": n.get("change_pct"),
                  "change_points": n.get("change_points"), "open": n.get("open"),
                  "high": n.get("high"), "low": n.get("low"),
                  "previous_close": n.get("previous_close"),
                  "close_status": _fact_status(report, "INDEX_CLOSE", "NIFTY 50")},
        "bank_nifty_change_pct": n.get("bank_nifty_change_pct"),
        "india_vix": n.get("india_vix"),
        "vix_status": _fact_status(report, "VOLATILITY_INDEX", "INDIA VIX"),
        "fii_net_cr": flows.get("fii_net_cash_cr"), "dii_net_cr": flows.get("dii_net_cash_cr"),
        "fii_status": _fact_status(report, "FII_NET_CASH", "FII"),
        "sector_indices": [{"name": s.get("name"), "change_pct": s.get("change_pct"),
                            "status": _fact_status(report, "SECTOR_CHANGE_PCT", s.get("name"))}
                           for s in report.get("sectors") or []],
        "global_cues": [{"label": g.get("label"), "value": g.get("value"),
                         "change_pct": g.get("change_pct")} for g in report.get("global_cues") or []],
        "technicals": report.get("technicals") or {},
    }


def market_structure(repo: DeskRepository, session: dt.date) -> dict:
    data, path = repo.market_structure(session)
    if not data:
        return {"status": "UNAVAILABLE", "reason": f"no Market Structure artifact for {session}"}
    reconciled, reason = True, ""
    try:
        from market_structure.store import load_snapshot
        load_snapshot(path)
    except Exception as exc:           # ReconciliationError or a malformed file
        reconciled, reason = False, f"{type(exc).__name__}: {exc}"
    snap = data.get("snapshot") or {}
    metrics = snap.get("metrics") or {}
    return {
        "status": "OK" if reconciled else "RECONCILIATION_FAILED", "reason": reason,
        "universe_label": snap.get("universe_label"),
        "constituent_count": snap.get("constituent_count"),
        "metrics": {k: metrics.get(k) for k in METRICS},
        "definitions": snap.get("definitions") or {},
        "constituents_by_sector": (snap.get("sector_mapping") or {}).get("constituents_by_sector") or {},
        "sector_source": (snap.get("sector_mapping") or {}).get("source"),
        "universe_source": snap.get("universe_source") or {},
        "observations": data.get("observations") or [],
        "subsets": snap.get("subsets") or {},
    }


def sector_table(ms: dict, candidate_views: list) -> list:
    """One row per NIFTY 200 industry sector. Counts come straight from the reconciled
    artifact's `by_sector`; the median 1D change is desk-derived from the same artifact's
    per-constituent `price_change_pct` (labelled as such)."""
    if ms.get("status") == "UNAVAILABLE":
        return []
    metrics = ms["metrics"]
    obs_by_sector: dict = {}
    for o in ms["observations"]:
        obs_by_sector.setdefault(o.get("sector") or "Unclassified", []).append(o)
    radar_by_sector: dict = {}
    for v in candidate_views:
        radar_by_sector.setdefault(v.get("sector") or "Unclassified", []).append(v["symbol"])
    rows = []
    for sector, size in sorted(ms["constituents_by_sector"].items()):
        def cnt(key):
            m = metrics.get(key) or {}
            return (m.get("by_sector") or {}).get(sector, 0) if m else None
        changes = [o["price_change_pct"] for o in obs_by_sector.get(sector, [])
                   if o.get("breadth_covered") and o.get("price_change_pct") is not None]
        adv, dec = cnt("ADVANCES"), cnt("DECLINES")
        rows.append({
            "sector": sector, "constituents": size, "advances": adv, "declines": dec,
            "unchanged": (len(changes) - adv - dec) if (adv is not None and dec is not None) else None,
            "breadth_covered": len(changes),
            "unusual_volume": cnt("UNUSUAL_VOLUME"), "range_up": cnt("RANGE_UP"),
            "range_down": cnt("RANGE_DOWN"),
            "median_change_pct": statistics.median(changes) if changes else None,
            "radar_candidates": len(radar_by_sector.get(sector, [])),
            "radar_symbols": sorted(radar_by_sector.get(sector, [])),
        })
    return rows


def regime_diagnostics(report_diag: dict, ms: dict, sectors: list) -> dict:
    """Plain counts for the market-context facts row; no classification (see private_desk.regime)."""
    out = {}
    if ms.get("status") != "UNAVAILABLE":
        m = ms["metrics"]
        def num(k):
            return (m.get(k) or {}).get("numerator")
        def den(k):
            return (m.get(k) or {}).get("denominator")
        out = {"advances": num("ADVANCES"), "declines": num("DECLINES"),
               "breadth_denominator": den("ADVANCES"),
               "unusual_volume": num("UNUSUAL_VOLUME"), "unusual_volume_den": den("UNUSUAL_VOLUME"),
               "range_up": num("RANGE_UP"), "range_down": num("RANGE_DOWN"),
               "range_den": den("RANGE_UP"),
               "sectors_more_adv": sum(1 for s in sectors if (s["advances"] or 0) > (s["declines"] or 0)),
               "sectors_more_dec": sum(1 for s in sectors if (s["declines"] or 0) > (s["advances"] or 0)),
               "sector_count": len(sectors)}
        if out["advances"] is not None and out["declines"] is not None and out["breadth_denominator"]:
            out["unchanged"] = out["breadth_denominator"] - out["advances"] - out["declines"]
    return out


__all__ = ["report_diagnostics", "market_structure", "sector_table", "regime_diagnostics"]

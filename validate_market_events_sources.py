"""Market Events source reachability diagnostic -> output/market_events_connectivity/.

    python validate_market_events_sources.py                   # all probed families
    python validate_market_events_sources.py --family EARNINGS

Answers ONE question per family: can this machine reach its official source right now, and
does the real adapter's own parser still classify what comes back the way it did when it was
built? Diagnostic only - never gates anything, never writes to market_events/ (read-only, no
`MarketEventsService.capture()` call), mirrors `validate_pre_production.py --connectivity-only`
/ `operations/connectivity.py`'s fails-safe-per-probe posture. Uses `market.NSE()` (the
warm-up-cookie client), not `operations.connectivity`'s generic raw-`requests` probe - these
undocumented NSE website APIs need the Akamai warm-up that generic probe doesn't do.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import config
from config import IST

DEFAULT_OUT = os.path.join(config.OUT_DIR, "market_events_connectivity")


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    return path


def _check_earnings(now_iso: str) -> dict:
    from operations.connectivity import classify_exception
    try:
        from market import NSE
        nse = NSE()
        payload = nse.get("/api/corporate-board-meetings?index=equities")
    except Exception as exc:
        return {"check": "EARNINGS", "status": "SOURCE_UNAVAILABLE",
               "connectivity": classify_exception(exc), "detail": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, list):
        return {"check": "EARNINGS", "status": "PARSE_ERROR", "connectivity": "REACHABLE",
               "detail": "payload is not a list"}
    from market_events.sources.earnings import parse_board_meetings
    events, notes = parse_board_meetings(payload, now_iso)
    return {"check": "EARNINGS", "status": "REACHABLE", "connectivity": "REACHABLE",
           "row_count": len(payload), "results_classified_count": len(events),
           "sample_field_keys": sorted(payload[0].keys()) if payload else [],
           "notes": notes[:10]}


def _check_ofs(now_iso: str) -> dict:
    """OFS has NO identified live source (see docs/MARKET_EVENTS_ENGINE.md Known Limitations):
    a full 5-week pull of NSE's corporate-announcements `desc` taxonomy (109 categories,
    19,307 rows, verified 2026-10-06) contains no "Offer for Sale"/"OFS" category, and every
    dedicated OFS endpoint guess returned a genuine 404. This probe still runs (to catch a
    future taxonomy change) but reports the family as NOT_SUPPORTED_YET, never invents a
    classification."""
    from operations.connectivity import classify_exception
    try:
        from market import NSE
        nse = NSE()
        payload = nse.get("/api/corporate-announcements?index=equities")
    except Exception as exc:
        return {"check": "OFS", "status": "SOURCE_UNAVAILABLE",
               "connectivity": classify_exception(exc), "detail": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, list):
        return {"check": "OFS", "status": "PARSE_ERROR", "connectivity": "REACHABLE",
               "detail": "payload is not a list"}
    import re
    ofs_re = re.compile(r"offer\s+for\s+sale|\bofs\b", re.I)
    matches = [r for r in payload if ofs_re.search(" ".join(str(v) for v in r.values()))]
    return {"check": "OFS", "status": "NOT_SUPPORTED_YET", "connectivity": "REACHABLE",
           "row_count": len(payload), "rows_matching_ofs_pattern": len(matches),
           "detail": "corporate-announcements feed is reachable; no OFS category/pattern "
                     "found this pull - no dedicated OFS endpoint has been located either "
                     "(every guess returned a genuine 404). See docs/MARKET_EVENTS_ENGINE.md."}


def _check_ipo() -> dict:
    return {"check": "IPO", "status": "N/A",
           "detail": "sourced via ipo_watch/official_snapshots by design - never fetched by "
                     "this engine. See ipo_watch's own connectivity path for IPO reachability."}


def _check_skipped(family: str) -> dict:
    return {"check": family, "status": "SKIPPED",
           "detail": "no endpoint configured - NOT_SUPPORTED_YET by design this pass"}


CHECKS = {"EARNINGS": _check_earnings, "OFS": _check_ofs}


def run_diagnostic(families=None, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(IST)
    now_iso = now.isoformat()
    families = families or ["EARNINGS", "IPO", "OFS", "GOVT_SECURITIES_AUCTION", "BUYBACK",
                            "OPEN_OFFER", "DELISTING"]
    checks = []
    for family in families:
        try:
            if family == "IPO":
                checks.append(_check_ipo())
            elif family in CHECKS:
                checks.append(CHECKS[family](now_iso))
            else:
                checks.append(_check_skipped(family))
        except Exception as exc:
            checks.append({"check": family, "status": "DIAGNOSTIC_ERROR",
                          "detail": f"{type(exc).__name__}: {exc}"})
    return {"version": "market-events-connectivity-1.0", "checked_at": now_iso, "checks": checks,
           "policy": "diagnostic only - never gates anything, never writes to market_events/"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", action="append", dest="families",
                    help="probe only this family (repeatable)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    result = run_diagnostic(args.families)
    run_dir = os.path.join(args.out, dt.datetime.now(IST).strftime("%Y%m%dT%H%M%S"))
    path = _dump(os.path.join(run_dir, "connectivity.json"), result)
    for c in result["checks"]:
        print(f"  {c['check']:<10} {c['status']:<16} {c.get('detail', '')}"[:160])
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

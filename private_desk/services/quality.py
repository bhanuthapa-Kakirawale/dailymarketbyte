"""Data-quality panel: is what the desk shows complete and current?"""
from __future__ import annotations

import datetime as dt

from ..db import DB_FILES, SourceUnavailable
from ..repository import BENCHMARK_SYMBOL, DeskRepository
from .. import replay as rp


def data_quality(repo: DeskRepository, session: dt.date | None, replay: dict | None,
                 freshness) -> dict:
    out = {"session": session, "freshness": freshness}
    out["databases"] = [repo.db_health(name) for name in DB_FILES]

    try:
        out["radar_runs"] = list(reversed(repo.radar_run_markers()))[:15]
    except SourceUnavailable as exc:
        out["radar_runs"], out["radar_runs_error"] = [], str(exc)

    art, path = repo.radar_artifact(session) if session else (None, None)
    out["radar_artifact"] = None if not art else {
        "pipeline_status": art.get("pipeline_status"), "generated_at": art.get("generated_at"),
        "universe_requested": art.get("universe_requested"),
        "universe_usable": art.get("universe_usable"),
        "composite_candidate_count": art.get("composite_candidate_count"),
        "editorial_selection_count": art.get("editorial_selection_count"),
        "coverage": art.get("coverage_diagnostics") or {},
        "issues": art.get("issues") or [], "issue_counts": art.get("issue_counts") or {}}

    universe, uni_session = repo.universe(session) if session else ({}, None)
    out["universe_session"] = uni_session
    try:
        st = repo.ohlcv_status()
        latest = st["latest_by_symbol"]
        missing, stale = [], []
        for sym in sorted(universe):
            d = latest.get(sym)
            if d is None:
                missing.append(sym)
            elif session and d < session:
                stale.append({"symbol": sym, "latest": d})
        out["ohlcv"] = {"row_count": st["row_count"], "quality_counts": st["quality_counts"],
                        "max_retrieved_at": st["max_retrieved_at"],
                        "benchmark_latest": latest.get(BENCHMARK_SYMBOL),
                        "symbols": len(latest), "universe_size": len(universe),
                        "missing": missing, "stale": stale}
    except SourceUnavailable as exc:
        out["ohlcv"] = {"error": str(exc)}

    data, _ = repo.market_structure(session) if session else (None, None)
    if data:
        snap = data.get("snapshot") or {}
        out["market_structure"] = {
            k: {"numerator": m.get("numerator"), "denominator": m.get("denominator"),
                "universe_size": m.get("universe_size"), "coverage_pct": m.get("coverage_pct"),
                "status": m.get("status")}
            for k, m in (snap.get("metrics") or {}).items()}
    else:
        out["market_structure"] = None

    if replay is not None and session:
        stored = repo.candidates(session)
        recon = rp.reconcile(replay if replay.get("status") == "OK" else None, stored)
        counts: dict = {}
        for v in recon.values():
            counts[v] = counts.get(v, 0) + 1
        out["replay"] = {"status": replay.get("status"), "reason": replay.get("reason"),
                         "usable_symbols": replay.get("usable_symbols"),
                         "universe_size": replay.get("universe_size"),
                         "universe_source_session": replay.get("universe_source_session"),
                         "skipped_symbols": replay.get("skipped_symbols") or {},
                         "reconciliation": counts,
                         "not_matched": sorted(k for k, v in recon.items() if v != rp.MATCHED),
                         "as_of": replay.get("as_of")}

    out["official"] = None
    if session:
        manifest = repo.official_manifest(session)
        if manifest:
            out["official"] = {k: {"status": e.get("status"), "validated": e.get("validated"),
                                   "record_count": e.get("record_count"),
                                   "source_date": e.get("source_date"),
                                   "retrieved_at": e.get("retrieved_at"),
                                   "reason": e.get("reason")}
                               for k, e in (manifest.get("snapshots") or {}).items()}
    try:
        _, row, status = repo.report(session) if session else (None, None, "MISSING")
        out["report"] = {"status": status, "report_id": (row or {}).get("report_id"),
                         "publication_ready": (row or {}).get("publication_ready")}
        out["runs"] = repo.publication_runs(8)
    except SourceUnavailable as exc:
        out["report"], out["runs"] = {"status": "HISTORY_UNAVAILABLE", "error": str(exc)}, []
    return out


def regime_quality(svc, session: dt.date | None) -> dict:
    """Regime classifier status for the Data Quality page: version, freshness vs the latest
    completed session, inputs present / missing, recent-window coverage and the last historical
    validation run (research artifact, when it has been built)."""
    import json
    import os

    from ..regime import rules as rr
    from ..regime.model import CALCULATION_VERSION, INSUFFICIENT_DATA, SCHEMA_VERSION
    from ..regime.research import RESEARCH_DIRNAME
    out = {"calculation_version": CALCULATION_VERSION, "schema_version": SCHEMA_VERSION,
           "required": "TREND and BREADTH, plus at least one of SECTORS / VOLUME / VOLATILITY",
           "session": session}
    if session is None:
        out["status"] = "NO_SESSION"
        return out
    r = svc.regime(session)
    snap, hist = r["snapshot"], r["history"]
    out.update({
        "regime": snap.regime, "regime_session": snap.session_date, "stale": r["stale"],
        "latest_completed": r["latest_completed"],
        "available": [d.key for d in snap.dimensions if d.available],
        "unavailable": list(snap.missing_dimensions),
        "membership": snap.universe,
        "window": len(hist),
        "window_insufficient": sum(1 for s in hist if s.regime == INSUFFICIENT_DATA),
        "window_counts": {k: sum(1 for s in hist if s.regime == k) for k in
                          ("BULLISH", "BEARISH", "NEUTRAL", "TRANSITIONAL", INSUFFICIENT_DATA)},
        "stored_files": svc.regimes.stored_versions(),
        "dimension_roles": {k: rr.CLASSIFIERS[k][1] for k in rr.DIMENSION_ORDER},
    })
    out["status"] = ("STALE" if r["stale"] else
                     "INSUFFICIENT_DATA" if snap.regime == INSUFFICIENT_DATA else "OK")
    path = os.path.join(svc.settings.out_dir, "private_desk", RESEARCH_DIRNAME,
                        "validation_summary.json")
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        out["validation"] = {
            "status": "CURRENT" if v.get("calculation_version") == CALCULATION_VERSION
            else "OUTDATED_VERSION", "calculation_version": v.get("calculation_version"),
            "generated_at": v.get("generated_at"), "end_session": v.get("end_session"),
            "sessions": v.get("sessions_total"), "eligible": v.get("eligible_sessions"),
            "insufficient": v.get("insufficient_sessions"), "switches": v.get("regime_switches"),
            "counts": v.get("counts_by_regime")}
    except (OSError, ValueError):
        out["validation"] = {"status": "NOT_BUILT", "hint": "python -m private_desk.regime.research"}
    return out


__all__ = ["data_quality", "regime_quality"]

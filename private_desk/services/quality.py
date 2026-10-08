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
    out["editorial"] = editorial_decisions(repo)
    return out


def editorial_decisions(repo: DeskRepository) -> list:
    """Editorial Planner V3 transparency: the latest POST production manifest's and the latest
    PRE shadow section plan's decision trace - which stories were selected, which were not, and
    why (tier, duplicate, family, slots, budget). Read-only JSON, never a fetch or a write."""
    import glob
    import json
    import os
    out = []
    sources = (("POST", "post", "*", "production_manifest.json", ("editorial_trace",)),
               ("PRE", "pre_shadow", "*", "pre_section_plan_*.json", ("editorial",)))
    for edition, folder, sub, pattern, keys in sources:
        files = [f for f in glob.glob(repo.path(folder, sub, pattern))
                 if "DEMO" not in f and "replay" not in f]
        if not files:
            out.append({"edition": edition, "status": "NO_ARTIFACT", "rows": []})
            continue
        path = max(files, key=lambda f: (os.path.basename(os.path.dirname(f)), f))
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            out.append({"edition": edition, "status": "UNREADABLE", "error": str(exc),
                        "rows": []})
            continue
        ed = data.get(keys[0]) or {}
        out.append({"edition": edition, "file": os.path.relpath(path, repo.out_dir),
                    "status": "OK" if ed.get("trace") else "NO_V3_TRACE",
                    "policy": ed.get("policy"), "lead": ed.get("lead"),
                    "runtime_estimate": ed.get("runtime_estimate"),
                    "order": ed.get("order") or [],
                    "rows": [{k: r.get(k) for k in ("candidate_id", "family", "tier", "quality",
                                                    "relevance", "decision", "reason",
                                                    "duplicate_of", "displaced_by",
                                                    "merged_into", "cost_s")}
                             for r in ed.get("trace") or []]})
    return out


def regime_quality(svc, session: dt.date | None) -> dict:
    """Regime classifier status for the Data Quality page: version, freshness vs the latest
    completed session, inputs present / missing, recent-window coverage and the last historical
    validation run (research artifact, when it has been built)."""
    import json
    import os

    from ..regime import rules as rr
    from ..regime.data import UNIVERSE_QUALITIES, earliest_point_in_time_session
    from ..regime.eligibility import research_eligibility
    from ..regime.model import (CALCULATION_VERSION, INSUFFICIENT_DATA, MODEL_STATUS,
                                SCHEMA_VERSION)
    from ..regime.research import RESEARCH_DIRNAME
    out = {"calculation_version": CALCULATION_VERSION, "schema_version": SCHEMA_VERSION,
           "model_status": MODEL_STATUS, "volume_threshold_status": rr.VOLUME_THRESHOLD_STATUS,
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
        "universe_quality": snap.universe_quality,
        "universe_note": (snap.universe or {}).get("universe_note"),
        "point_in_time_from": earliest_point_in_time_session(svc.repo),
        "window_universe_coverage": {q: sum(1 for s in hist if s.universe_quality == q)
                                     for q in UNIVERSE_QUALITIES},
        "research_eligible": research_eligibility(snap)[0],
        "research_ineligible_reasons": research_eligibility(snap)[1],
        "dimension_roles": {k: rr.CLASSIFIERS[k][1] for k in rr.DIMENSION_ORDER},
    })
    out["status"] = ("STALE" if r["stale"] else
                     "INSUFFICIENT_DATA" if snap.regime == INSUFFICIENT_DATA else "OK")
    path = os.path.join(svc.settings.out_dir, "private_desk", RESEARCH_DIRNAME,
                        "validation_summary.json")
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        groups = v.get("groups") or {}
        pit = groups.get("POINT_IN_TIME_VALIDATED") or {}
        current = (v.get("calculation_version") == CALCULATION_VERSION
                   and v.get("schema_version") == SCHEMA_VERSION)
        out["validation"] = {
            "status": "CURRENT" if current else "OUTDATED_VERSION",
            "calculation_version": v.get("calculation_version"),
            "generated_at": v.get("generated_at"), "end_session": v.get("end_session"),
            "sessions": v.get("sessions_total"), "insufficient": v.get("insufficient_sessions"),
            "universe_quality_counts": v.get("universe_quality_counts") or {},
            "point_in_time_from": v.get("earliest_point_in_time_session"),
            "pit_classifiable": pit.get("classifiable"), "pit_counts": pit.get("counts_by_regime"),
            "pit_meaningful": v.get("primary_statistics_meaningful"),
            "min_sessions": v.get("min_sessions_for_statistics"),
            "research_eligible": v.get("research_eligible_sessions")}
    except (OSError, ValueError):
        out["validation"] = {"status": "NOT_BUILT", "hint": "python -m private_desk.regime.research"}
    return out


__all__ = ["data_quality", "regime_quality"]

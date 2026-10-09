"""The `refresh` orchestration: latest-session catch-up, then a 30-day (or overridden) price-
chain-only self-heal, oldest session first. See package docstring for the architectural
reasoning; see `coverage.py`/`price_chain_backfill.py` for what each step actually touches.
"""
from __future__ import annotations

import datetime as dt
import socket
import sys

from . import coverage, lock as lock_module, manifest, models, price_chain_backfill, registry, scan
from .latest_session import ensure_latest


def _now_ist():
    import config
    return config.now_ist()


def run_refresh(*, out_dir: str | None = None, now: dt.datetime | None = None, days: int = 30,
                from_date: dt.date | None = None, to_date: dt.date | None = None,
                session_date: dt.date | None = None, resume: bool = False,
                dry_run: bool = False, no_network: bool = False, universe_name: str = "NIFTY200",
                calendar=None, report_job_fn=None) -> models.RunManifest:
    import config
    out_dir = out_dir or config.OUT_DIR
    now = now or _now_ist()
    argv = list(sys.argv)

    run_id = manifest.new_run_id(now)
    man = models.RunManifest(
        run_id=run_id, command="refresh",
        cli={"days": days, "from": str(from_date) if from_date else None,
            "to": str(to_date) if to_date else None,
            "session_date": str(session_date) if session_date else None,
            "resume": resume, "dry_run": dry_run, "no_network": no_network},
        host={"hostname": socket.gethostname(), "pid": None},
        git_commit=manifest.git_commit(config.BASE_DIR))

    run_lock = lock_module.RunLock(out_dir, now=lambda: now)
    try:
        man.reclaimed_stale_lock = run_lock.acquire(run_id, cli_argv=argv)
    except lock_module.LockConflict as exc:
        man.finalize(models.LOCKED)
        man.blocking_reasons = [str(exc)]
        manifest.write_manifest(out_dir, man)
        return man

    try:
        if resume:
            leftover = manifest.running_manifest_to_resume(out_dir)
            if leftover is not None:
                man.resumed_from_run_id = leftover.get("run_id")

        latest_result = ensure_latest(out_dir, now, dry_run=(dry_run or no_network),
                                      report_job_fn=report_job_fn, calendar=calendar)
        man.latest_session_result = latest_result

        historical_sessions, _latest = scan.sessions_to_audit(
            out_dir, now, days=days, from_date=from_date, to_date=to_date,
            session_date=session_date, calendar=calendar)

        session_results = _run_price_chain(
            historical_sessions, out_dir=out_dir, universe_name=universe_name, now=now,
            dry_run=dry_run, no_network=no_network, warnings=man.warnings)

        man.session_results = session_results
        man.summary = _summarize(historical_sessions, session_results)
        man.orchestrator_status = _derive_status(latest_result, session_results)
        man.completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    except Exception as exc:
        man.failure = {"reason": f"{type(exc).__name__}: {exc}"}
        man.finalize(models.FAILED)
    finally:
        run_lock.release()

    manifest.write_manifest(out_dir, man)
    return man


def _run_price_chain(historical_sessions: list, *, out_dir: str, universe_name: str,
                     now: dt.datetime, dry_run: bool, no_network: bool, warnings: list) -> list:
    coverage_map = {s: coverage.scan_session(out_dir, s, now=now) for s in historical_sessions}

    if dry_run or no_network:
        for s, comps in coverage_map.items():
            for key in registry.PRICE_CHAIN_KEYS:
                if comps[key] == registry.MISSING:
                    comps[key] = registry.WOULD_BACKFILL
        return _to_session_results(historical_sessions, coverage_map)

    missing_structure = [s for s in historical_sessions
                         if coverage_map[s]["market_structure"] == registry.MISSING]
    missing_candidates = [s for s in historical_sessions
                          if coverage_map[s]["radar_candidate_history"] == registry.MISSING]

    ctx = None
    if missing_structure or missing_candidates:
        ctx = price_chain_backfill.fetch_benchmark_context(out_dir, universe_name)
        if ctx.get("status") != "OK":
            warnings.append(f"price-chain backfill skipped this run: {ctx.get('reason')}")
            ctx = None

    for s in missing_structure:
        result = price_chain_backfill.backfill_market_structure(s, universe_name, out_dir)
        coverage_map[s]["market_structure"] = result["status"]

    if missing_candidates and ctx is not None:
        result = price_chain_backfill.backfill_radar_candidates(
            missing_candidates, spine=ctx["spine"], universe=ctx["universe"],
            benchmark_series=ctx["bench"], out_dir=out_dir)
        for s in result.processed_sessions:
            coverage_map[s]["radar_candidate_history"] = registry.BACKFILLED
        for s in result.already_complete_sessions:
            coverage_map[s]["radar_candidate_history"] = registry.PRESENT
        for s in result.failed_sessions:
            coverage_map[s]["radar_candidate_history"] = registry.FAILED
        warnings.extend(result.warnings)

    for s in historical_sessions:
        if coverage_map[s]["ohlcv_benchmark"] == registry.MISSING:
            present = coverage._ohlcv_benchmark_present(out_dir, s)
            coverage_map[s]["ohlcv_benchmark"] = registry.BACKFILLED if present else registry.MISSING

    return _to_session_results(historical_sessions, coverage_map)


def _to_session_results(historical_sessions: list, coverage_map: dict) -> list:
    out = []
    for s in historical_sessions:
        comps = coverage_map[s]
        price_statuses = [comps[k] for k in registry.PRICE_CHAIN_KEYS]
        if all(st in (registry.PRESENT, registry.BACKFILLED) for st in price_statuses):
            session_status = "COMPLETE"
        else:
            session_status = "PARTIAL"
        out.append({"session": s.isoformat(), "components": comps,
                   "session_status": session_status})
    return out


def _summarize(historical_sessions: list, session_results: list) -> dict:
    component_counts: dict = {}
    for row in session_results:
        for status in row["components"].values():
            component_counts[status] = component_counts.get(status, 0) + 1
    complete = sum(1 for r in session_results if r["session_status"] == "COMPLETE")
    partial = sum(1 for r in session_results if r["session_status"] == "PARTIAL")
    return {"sessions_audited": len(historical_sessions), "sessions_fully_recoverable": complete,
           "sessions_partially_recoverable": partial, "component_counts": component_counts}


def _derive_status(latest_result: dict, session_results: list) -> str:
    if latest_result.get("report_job_run_status") == "BLOCKED":
        return models.BLOCKED
    if latest_result.get("reason") == "SESSION_UNKNOWN":
        return models.BLOCKED
    for row in session_results:
        for status in row["components"].values():
            if status == registry.FAILED:
                return models.DEGRADED
    # HISTORICAL_UNAVAILABLE/UNSUPPORTED_HISTORICALLY on a non-price component is expected
    # every run (registry.LATEST_ONLY_KEYS/STATIC_KEYS for any non-latest session) and never a
    # degradation by itself.
    return models.SUCCESS


__all__ = ["run_refresh"]

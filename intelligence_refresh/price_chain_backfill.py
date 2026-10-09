"""The three price-derived components, for historical (non-latest) sessions. Each function is
a thin call to an existing, unmodified engine - none of the actual acquisition/detection logic
lives here.

Fetch-once efficiency: `market_structure.build.build_for_session` has no way to accept a
pre-fetched benchmark/universe (it fetches its own internally on every call, so backfilling N
historical sessions means N redundant benchmark fetches - an accepted, documented limitation of
reusing that tool unmodified, see docs/INTELLIGENCE_REFRESH.md). `radar.candidate_history_
backfill.backfill_candidate_history` DOES take `spine`/`universe`/`benchmark_series` as
parameters and accepts a whole list of sessions in one call - so `fetch_benchmark_context` is
called ONCE per `refresh()` run (not once per session) and its result is reused for every
session's candidate-history backfill in a single batched call.
"""
from __future__ import annotations

import datetime as dt

from . import registry


def fetch_benchmark_context(out_dir: str, universe_name: str = "NIFTY200") -> dict:
    """ONE network-bearing fetch of the benchmark series + universe + canonical spine, shared
    across every session's `backfill_radar_candidates` call in this run. Never raises - a
    failure here means the price-chain backfill for this run simply attempts nothing further
    (recorded as a warning, not a run-level failure)."""
    import market
    from radar import relative_acquisition, session_alignment

    bench = relative_acquisition.build_market_benchmark_series(period="1y", out_dir=out_dir)
    if not bench:
        return {"status": "FAILED", "reason": "benchmark series unavailable"}
    spine = session_alignment.canonical_session_list(bench)
    universe = market.get_universe(universe_name)
    return {"status": "OK", "bench": bench, "spine": spine, "universe": universe}


def backfill_market_structure(session: dt.date, universe_name: str, out_dir: str) -> dict:
    """Thin call to `market_structure.build.build_for_session` - true no-op on unchanged
    content (checksum-compared inside `market_structure.store.save_snapshot`)."""
    from market_structure.build import build_for_session
    try:
        result = build_for_session(session, universe_name, out_dir=out_dir)
    except Exception as exc:
        return {"status": registry.FAILED, "detail": f"{type(exc).__name__}: {exc}"}
    if result.get("status") == "SKIPPED":
        return {"status": registry.FAILED, "detail": result.get("reason")}
    return {"status": registry.BACKFILLED, "artifact": result.get("artifact")}


def backfill_radar_candidates(sessions: list, *, spine: list, universe: dict,
                              benchmark_series: list, out_dir: str, store=None):
    """ONE batched call to `radar.candidate_history_backfill.backfill_candidate_history` for
    every session in `sessions` - that function already sorts oldest-first, skips sessions
    already COMPLETE, and persists candidates before the COMPLETE marker. Returns its
    `CandidateHistoryBackfillResult` unchanged."""
    from radar.candidate_history_backfill import backfill_candidate_history
    return backfill_candidate_history(spine=spine, universe=universe,
                                      benchmark_series=benchmark_series, sessions=sessions,
                                      out_dir=out_dir, store=store)


__all__ = ["fetch_benchmark_context", "backfill_market_structure", "backfill_radar_candidates"]

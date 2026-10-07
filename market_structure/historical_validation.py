"""Market Structure V2 - bounded historical validation (~60 sessions), NOT threshold tuning.

Reuses the production calculation path (`radar.daily_pipeline._run_detectors` +
`market_structure.build_observations`/`aggregate` - never a second, simplified aggregation) over
the most recent `--sessions` (default 60) canonical sessions the local benchmark spine confirms
traded. Session selection reuses `radar.historical_validation.select_session_dates` rather than
a second implementation of "last N sessions with a known prior session".

Writes NOTHING to `output/market_structure/` (the production snapshot path) - this is a
read/report-only diagnostic, never a source of persisted Market Structure state. Its own report
goes to `output/market_structure_validation/`.

One-time deep backfill: the 52-week metric needs >= 253 PRIOR sessions of per-symbol history.
Production's own wiring (`radar.daily_pipeline._market_structure_step`) deliberately never tops
this up itself (`fetch_on_gap=False` - see `radar/ohlcv_service.py`), so a cold local store would
otherwise make this entire validation run report INSUFFICIENT_HISTORY for almost everything,
which is an honest but useless result. This module does ONE validation-run-scoped deep fetch
(`market.get_universe_technical_series(..., period="2y")`, called once, for the OLDEST session
under test) before the per-session loop - writing through to the SAME production OHLCV store via
the existing seam (not a new acquisition path), which also means a validation run leaves the
store better-seeded for future production runs. Documented explicitly as a validation-script-only
convenience; daily production never does this on its own.

    python -m market_structure.historical_validation [--sessions 60] [--universe NIFTY200]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import time

import config
import market
import market_structure as ms
from radar import daily_pipeline, ohlcv_service, relative_acquisition, session_alignment
from radar.historical_validation import select_session_dates

ARTIFACT_DIR = os.path.join(config.OUT_DIR, "market_structure_validation")
DEFAULT_SESSIONS = 60


def _nifty_pct(bench_by_date: dict, session_date: dt.date, prev_date: dt.date) -> float | None:
    """Day-over-day Nifty 50 % change straight from the benchmark series already fetched for
    session selection - never a second index fetch, never positional (date-matched both ends,
    matching `market_structure.observations._dated_change`'s own discipline)."""
    cur, prev = bench_by_date.get(session_date), bench_by_date.get(prev_date)
    if cur is None or prev is None or not prev:
        return None
    return (float(cur) / float(prev) - 1) * 100


def _session_metrics(snap: ms.MarketStructureSnapshot, nifty_pct: float | None) -> dict:
    insights, reasons = ms.select_insights(snap, nifty_pct)
    high, low = snap.metric("NEW_52W_HIGH"), snap.metric("NEW_52W_LOW")
    return {
        "session_date": snap.session_date,
        "eligible_count": snap.metric("ADVANCES").denominator if snap.metric("ADVANCES") else 0,
        "new_52w_high": high.numerator if high else 0,
        "new_52w_low": low.numerator if low else 0,
        "new_52w_high_status": high.status if high else "UNKNOWN",
        "new_52w_low_status": low.status if low else "UNKNOWN",
        "shown_kinds": [i.kind for i in insights],
        "fifty_two_week_reason": reasons.get("FIFTY_TWO_WEEK", ""),
    }


def run_validation(sessions: int = DEFAULT_SESSIONS, universe_name: str = "NIFTY200",
                   out_dir: str | None = None, seed_backfill: bool = True) -> dict:
    out_dir = out_dir or config.OUT_DIR
    universe = market.get_universe(universe_name)
    bench = relative_acquisition.build_market_benchmark_series(period="1y")
    selected, prev_dates, warnings = select_session_dates(bench, sessions)
    if not selected:
        return {"status": "SKIPPED", "reason": "no sessions available on the canonical spine",
                "warnings": warnings}
    full_spine = session_alignment.canonical_session_list(bench)

    if seed_backfill:
        # ONE validation-run-scoped deep fetch, oldest session under test - see module docstring.
        market.get_universe_technical_series(universe, selected[0], prev_dates[0], period="2y")

    bench_by_date = {r["date"]: r["close"] for r in bench}
    per_session = []
    for session_date, prev_date in zip(selected, prev_dates):
        det = daily_pipeline._run_detectors(session_date, prev_date, universe, full_spine, bench,
                                            out_dir=out_dir, as_of=dt.datetime.now(dt.timezone.utc))
        fiftytwo_dataset = ohlcv_service.load_universe(
            universe, session_date, prev_date, required_lookback=ms.FIFTY_TWO_WEEK_LOOKBACK_SESSIONS,
            spine=full_spine, out_dir=out_dir, fetch_on_gap=False)
        uni = ms.from_market_meta(market.UNIVERSE_META[universe_name])
        obs = ms.build_observations(uni, session_date, prev_date, det.volume_snapshot,
                                    det.technical_snapshot, det.dataset.series_by_symbol,
                                    fiftytwo_dataset.series_by_symbol)
        snap = ms.aggregate(obs, uni, session_date)
        nifty_pct = _nifty_pct(bench_by_date, session_date, prev_date)
        per_session.append(_session_metrics(snap, nifty_pct))

    highs = [r["new_52w_high"] for r in per_session if r["new_52w_high_status"] != "SUPPRESSED"]
    lows = [r["new_52w_low"] for r in per_session if r["new_52w_low_status"] != "SUPPRESSED"]
    eligible = [r["eligible_count"] for r in per_session]
    kinds_shown = [k for r in per_session for k in r["shown_kinds"]]
    artifact = {
        "status": "OK", "universe": universe_name, "sessions_requested": sessions,
        "sessions_evaluated": len(per_session),
        "session_dates": [r["session_date"] for r in per_session],
        "warnings": warnings,
        "summary": {
            "avg_eligible_universe": round(statistics.fmean(eligible), 1) if eligible else None,
            "avg_new_52w_high": round(statistics.fmean(highs), 2) if highs else None,
            "avg_new_52w_low": round(statistics.fmean(lows), 2) if lows else None,
            "max_new_52w_high": max(highs) if highs else None,
            "max_new_52w_low": max(lows) if lows else None,
            "sessions_with_fifty_two_week_shown": sum(1 for k in kinds_shown
                                                      if k == "FIFTY_TWO_WEEK"),
            "sessions_with_insufficient_history": sum(
                1 for r in per_session
                if r["new_52w_high_status"] == "SUPPRESSED" and r["new_52w_low_status"] == "SUPPRESSED"),
        },
        "per_session": per_session,
    }
    return artifact


def save_artifact(artifact: dict, out_dir: str | None = None) -> str:
    out_dir = out_dir or ARTIFACT_DIR
    os.makedirs(out_dir, exist_ok=True)
    end = artifact["session_dates"][-1] if artifact.get("session_dates") else "unknown"
    path = os.path.join(out_dir, f"market_structure_validation_{end}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Market Structure V2 bounded historical validation "
                                             "- analysis only, never a source of persisted state.")
    ap.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    ap.add_argument("--universe", default="NIFTY200")
    ap.add_argument("--no-seed-backfill", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.time()
    artifact = run_validation(args.sessions, args.universe, seed_backfill=not args.no_seed_backfill)
    if artifact["status"] != "OK":
        print(artifact)
        return 1
    path = save_artifact(artifact)
    print(f"[market_structure.historical_validation] {artifact['sessions_evaluated']} sessions "
         f"in {time.time() - t0:.1f}s -> {path}")
    print(json.dumps(artifact["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_validation", "save_artifact", "main", "ARTIFACT_DIR", "DEFAULT_SESSIONS"]

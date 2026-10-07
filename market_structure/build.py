"""Build (or rebuild) one session's Market Structure snapshot from the SAME detector path the daily
Radar uses - without persisting any Radar state (no candidate history, no editorial selections,
no Radar artifact). For backfills and review renders; production builds it inside
`radar.daily_pipeline.run_daily_radar`.

    python -m market_structure.build --session 2026-09-24 [--universe NIFTY200]

Reads/writes the normal OHLCV cache (output/data/market_ohlcv.db) exactly like the Radar does.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import config


def build_for_session(session: dt.date, universe_name: str = "NIFTY200",
                      subset_name: str | None = "NIFTY100", out_dir: str | None = None) -> dict:
    import market
    import market_structure as ms
    from radar import relative_acquisition, session_alignment
    from radar.daily_pipeline import _run_detectors

    out_dir = out_dir or config.OUT_DIR
    bench = relative_acquisition.build_market_benchmark_series(period="1y")
    spine = session_alignment.canonical_session_list(bench)
    if session not in spine:
        return {"status": "SKIPPED", "reason": f"{session} is not on the canonical spine"}
    prev = spine[spine.index(session) - 1]
    universe = market.get_universe(universe_name)
    uni = ms.from_market_meta(market.UNIVERSE_META[universe_name])
    uni.require_official()
    subsets = []
    if subset_name:
        market.get_universe(subset_name)
        sub = ms.from_market_meta(market.UNIVERSE_META[subset_name])
        if sub.official and sub.symbols() < uni.symbols():
            subsets.append(sub)
    det = _run_detectors(session, prev, universe, spine, bench, out_dir=out_dir,
                         as_of=dt.datetime.now(dt.timezone.utc))
    obs = ms.build_observations(uni, session, prev, det.volume_snapshot, det.technical_snapshot,
                                det.dataset.series_by_symbol)
    snap = ms.aggregate(obs, uni, session, subsets)
    path = ms.save_snapshot(snap, obs, uni, out_dir, subsets)
    return {"status": "OK", "artifact": path, "universe": uni.label, "previous_session": str(prev),
            "metrics": {k: m.denominator_text for k, m in snap.metrics.items()},
            "coverage": {k: m.coverage_pct for k, m in snap.metrics.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--universe", default="NIFTY200")
    args = ap.parse_args(argv)
    res = build_for_session(dt.date.fromisoformat(args.session), args.universe)
    print(res)
    return 0 if res["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())

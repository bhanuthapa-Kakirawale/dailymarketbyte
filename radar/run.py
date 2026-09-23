"""Standalone Market Intelligence Radar runner (Phase 4.2 Packet 5).

`python -m radar.run` - real-market validation only. Answers one question: when run against
real Indian market data, does the Radar discover stocks whose evidence is genuinely useful and
non-obvious? This is a product-validation exercise, not a new capability - no new detectors, no
threshold tuning, no video/editorial/upload integration.

Boundaries, same as every other Packet-5-adjacent module:

* Does NOT invoke `main.py`, `video.py`, `editorial/`, or YouTube upload - intelligence-only.
* Uses ONLY the three existing evidence families (`radar.volume`, `radar.technical`,
  `radar.relative`) composed by `radar.composite.build_candidates` - no RSI/MACD/ADX/Bollinger/
  candlestick patterns/fundamentals/filings/news/options/LLM.
* Sector-relative stays unused: no reliable stock->sector mapping exists in this codebase yet
  (see docs/MARKET_INTELLIGENCE_RADAR.md, Packet 3), and this module does not invent one.
* Thresholds are read from `radar.thresholds`' own defaults, unchanged - this run observes,
  it does not calibrate.

`assemble_radar_report` is the pure, offline-testable half: given already-built detector
snapshots and the raw acquired series, it composes the derived validation artifact (coverage,
candidates, rejected-large-movers diagnostic, distribution, manual-review selection) with no
network access. `main()` is the thin, real-IO half that acquires data and calls it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time

import market
from config import IST, OUT_DIR, UNIVERSE_LABEL
from core import Metric
from storage import MarketHistory, default_db_path

from . import (acquisition, composite, ohlcv_service, relative, relative_acquisition,
              session_alignment, technical, volume)
from .models import AnomalyLevel, AttentionLevel, DirectionCompatibility, RADAR_SCHEMA_VERSION

DERIVED_SCHEMA_VERSION = "1.0"
ARTIFACT_DIR = os.path.join(OUT_DIR, "radar")

# Rejected-large-movers diagnostic: how many big absolute movers to sample (section 10 of the
# packet spec) and what "large" means for inclusion, kept generous since this is diagnostic only.
REJECTED_MOVERS_SAMPLE = 10
REJECTED_MOVERS_MIN_ABS_PCT = 3.0

MANUAL_REVIEW_MAX = 10


# ----------------------------------------------------------------------------- pure assembly
def price_changes_from_series(series_by_symbol: dict) -> dict:
    """`{symbol: pct}` from each symbol's own last two closes in its acquired OHLC series -
    never re-fetched, never re-derived from a different source than the technical detector
    already used for its own SMA/range windows."""
    out = {}
    for symbol, rows in series_by_symbol.items():
        if len(rows) < 2:
            continue
        prev_close, cur_close = rows[-2]["close"], rows[-1]["close"]
        if prev_close:
            out[symbol] = (cur_close / prev_close - 1) * 100
    return out


def rvol_by_symbol_from_scan_report(scan_report) -> dict:
    """Every scanned symbol's raw RVOL reading, not just the ones that cleared the anomaly bar -
    `VolumeRadarSnapshot.anomalies` only holds flagged readings, so the rejected-large-movers
    diagnostic (section 10) needs the RVOL context of movers that were NOT flagged, read
    straight from the RADAR_SCAN report's own facts instead of re-fetching or re-computing."""
    out = {}
    for fact in scan_report.facts_for(Metric.STOCK_RELATIVE_VOLUME):
        if fact.value is not None:
            out[fact.instrument] = float(fact.value)
    return out


def assemble_radar_report(*, universe_name: str, universe: dict, session_date: dt.date,
                          benchmark_session: dt.date | None,
                          volume_snapshot, technical_snapshot, relative_snapshot,
                          composite_snapshot, series_by_symbol: dict, scan_report,
                          data_problems: dict, timings: dict,
                          generated_at: dt.datetime | None = None) -> dict:
    """Build the derived Radar validation artifact (section 8) plus the product-validation
    diagnostics (sections 9-14). Pure - takes only already-computed inputs, touches no network,
    mutates none of the snapshots it is given."""
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)
    price_changes = price_changes_from_series(series_by_symbol)
    rvol_readings = rvol_by_symbol_from_scan_report(scan_report) if scan_report is not None else {}

    candidate_symbols = {c.instrument for c in composite_snapshot.candidates}
    top_movers = sorted(price_changes.items(), key=lambda kv: -abs(kv[1]))[:5]
    top_mover_symbols = {s for s, _ in top_movers}

    outside_top_movers = sum(1 for c in composite_snapshot.candidates
                             if c.instrument not in top_mover_symbols)
    small_move_multi_family = sum(
        1 for c in composite_snapshot.candidates
        if c.price_change_pct is not None and abs(c.price_change_pct) < 2.0)
    three_family = sum(1 for c in composite_snapshot.candidates if c.independent_signal_count == 3)
    two_family = sum(1 for c in composite_snapshot.candidates if c.independent_signal_count == 2)

    rejected = _rejected_large_movers(price_changes, candidate_symbols, rvol_readings,
                                      volume_snapshot, technical_snapshot, relative_snapshot)
    distribution = _distribution(composite_snapshot.candidates)
    manual_review = _manual_review_selection(composite_snapshot.candidates, universe)

    report = {
        "schema_version": DERIVED_SCHEMA_VERSION,
        "radar_schema_version": RADAR_SCHEMA_VERSION,
        "calculation_version": composite_snapshot.calculation_version,
        "generated_at": generated_at.isoformat(),
        "session_date": session_date.isoformat(),
        "benchmark_session": benchmark_session.isoformat() if benchmark_session else None,
        "universe": {
            "name": universe_name,
            "requested": len(universe),
            "volume_acquired": len(volume_snapshot.scanned),
            "technical_acquired": len(technical_snapshot.scanned),
            "relative_acquired": len(relative_snapshot.scanned),
            "volume_skipped": len(volume_snapshot.skipped),
            "technical_skipped": len(technical_snapshot.skipped),
            "relative_skipped": len(relative_snapshot.skipped),
        },
        "detector_coverage": {
            "volume_scanned": len(volume_snapshot.scanned),
            "volume_anomalies": len(volume_snapshot.anomalies),
            "technical_scanned": len(technical_snapshot.scanned),
            "technical_flagged": len(technical_snapshot.flagged),
            "relative_scanned": len(relative_snapshot.scanned),
            "candidate_count": composite_snapshot.candidate_count,
            "non_candidates_count": composite_snapshot.non_candidates_count,
            "high_interest_count": sum(1 for c in composite_snapshot.candidates
                                       if c.attention_level == AttentionLevel.HIGH_INTEREST),
            "notable_count": sum(1 for c in composite_snapshot.candidates
                                 if c.attention_level == AttentionLevel.NOTABLE),
            "three_family_candidates": three_family,
            "two_family_candidates": two_family,
        },
        "product_validation": {
            "candidates_outside_top5_movers": outside_top_movers,
            "candidates_with_small_move_under_2pct": small_move_multi_family,
            "three_family_convergence_count": three_family,
            "top5_movers_by_abs_pct": [{"symbol": s, "price_change_pct": p} for s, p in top_movers],
        },
        "candidate_distribution": distribution,
        "data_problems": data_problems,
        "warnings": (list(volume_snapshot.warnings) + list(technical_snapshot.warnings) +
                    list(relative_snapshot.warnings) + list(composite_snapshot.warnings)),
        "candidates": [c.to_dict() for c in composite_snapshot.candidates],
        "rejected_large_movers": rejected,
        "manual_review": manual_review,
        "runtime": timings,
    }
    return report


def _rejected_large_movers(price_changes: dict, candidate_symbols: set, rvol_readings: dict,
                           volume_snapshot, technical_snapshot, relative_snapshot) -> list:
    """A sample of large absolute movers the Radar did NOT surface as candidates (section 10) -
    proof the Radar is not merely reproducing a gainers/losers page. "Meaningful volume" here is
    the exact same bar `radar.composite` uses (`AnomalyLevel.UNUSUAL`/`EXTREME` only) - not a
    raw RVOL cutoff of its own, so this diagnostic can never disagree with why a symbol was
    actually rejected."""
    volume_by_symbol = {a.instrument: a for a in volume_snapshot.anomalies}
    technical_by_symbol = {s.instrument: s for s in technical_snapshot.flagged}
    relative_by_symbol = {r.instrument: r for r in relative_snapshot.results}

    large_movers = sorted(
        ((s, p) for s, p in price_changes.items()
         if s not in candidate_symbols and abs(p) >= REJECTED_MOVERS_MIN_ABS_PCT),
        key=lambda kv: -abs(kv[1]))[:REJECTED_MOVERS_SAMPLE]

    out = []
    for symbol, pct in large_movers:
        families = []
        vol = volume_by_symbol.get(symbol)
        if vol is not None and vol.level in (AnomalyLevel.UNUSUAL, AnomalyLevel.EXTREME):
            families.append("volume")
        tech = technical_by_symbol.get(symbol)
        if tech is not None and tech.events:
            families.append("structure")
        rel = relative_by_symbol.get(symbol)
        rel_state = rel.persistence_state.value if rel is not None and rel.persistence_state else None
        if rel_state in ("PERSISTENT_POSITIVE", "PERSISTENT_NEGATIVE"):
            families.append("relative")
        families = [f for f in families if f]
        out.append({
            "symbol": symbol, "price_change_pct": pct,
            "rvol": rvol_readings.get(symbol),
            "structural_events": [e.event_type.value for e in tech.events] if tech else [],
            "relative_persistence": rel_state,
            "meaningful_families": len(set(families)),
            "reason": (f"Only {len(set(families))} meaningful evidence family/families "
                      f"cleared the eligibility bar."),
        })
    return out


def _distribution(candidates: list) -> dict:
    by_family_count: dict = {}
    by_compat: dict = {c.value: 0 for c in DirectionCompatibility}
    by_combination: dict = {}
    for c in candidates:
        by_family_count[c.independent_signal_count] = by_family_count.get(c.independent_signal_count, 0) + 1
        if c.direction_compatibility is not None:
            by_compat[c.direction_compatibility.value] += 1
        combo = " + ".join(sorted(c.active_families))
        by_combination[combo] = by_combination.get(combo, 0) + 1
    return {"by_family_count": by_family_count, "direction_compatibility": by_compat,
           "evidence_combinations": by_combination}


def _manual_review_selection(candidates: list, universe: dict) -> list:
    """Up to `MANUAL_REVIEW_MAX` candidates, 3-family first then 2-family, deterministic tie
    order (instrument name) - an internal validation report, not a public stock ranking
    (section 13)."""
    ordered = sorted(candidates, key=lambda c: (-c.independent_signal_count, c.instrument))
    out = []
    for c in ordered[:MANUAL_REVIEW_MAX]:
        out.append({
            "symbol": c.instrument, "company_name": universe.get(c.instrument),
            "price_change_pct": c.price_change_pct,
            "independent_signal_count": c.independent_signal_count,
            "active_families": list(c.active_families),
            "rvol": c.volume_anomaly.relative_volume if c.volume_anomaly else None,
            "structural_events": ([e.event_type.value for e in c.technical_anomaly.events]
                                  if c.technical_anomaly else []),
            "relative_1d_pp": c.relative_strength.market_relative_1d_pp if c.relative_strength else None,
            "relative_5d_pp": c.relative_strength.market_relative_5d_pp if c.relative_strength else None,
            "relative_20d_pp": c.relative_strength.market_relative_20d_pp if c.relative_strength else None,
            "direction_compatibility": (c.direction_compatibility.value
                                        if c.direction_compatibility else None),
            "attention_level": c.attention_level.value if c.attention_level else None,
            "evidence": list(c.evidence),
        })
    return out


def save_radar_report(report: dict, out_dir: str = OUT_DIR) -> str:
    directory = os.path.join(out_dir, "radar")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"radar_{report['session_date']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
    return path


# ----------------------------------------------------------------------------- real-IO main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Market Intelligence Radar - standalone, "
                                                 "intelligence-only real-market validation run. "
                                                 "No video, no editorial, no upload.")
    parser.add_argument("--universe", default="NIFTY200",
                        help="Constituent list name market.get_universe understands "
                             "(NIFTY50/NIFTY100/NIFTY200/NIFTY500). Default NIFTY200 (~200 "
                             "symbols, inside the 200-500 target range).")
    args = parser.parse_args(argv)

    t_start = time.time()
    print(f"[radar.run] requested run date (wall clock, IST): {dt.datetime.now(IST).date()}")

    print("[1/8] Fetching latest completed Nifty session (benchmark + session-date source)...")
    t0 = time.time()
    m = market.get_market()
    recap_date, prev_date = m["recap_date"], m["prev_date"]
    t_nifty = time.time() - t0
    print(f"      actual market session analyzed: {recap_date}  (prev session: {prev_date})")

    print(f"[2/8] Fetching universe constituents ({args.universe})...")
    t0 = time.time()
    universe = market.get_universe(args.universe)
    t_universe = time.time() - t0
    print(f"      universe requested: {len(universe)} symbols "
         f"({UNIVERSE_LABEL.get(args.universe, args.universe)})")
    if len(universe) < 150:
        print("      WARNING: universe source returned fewer than 150 symbols - the constituent "
             "list source may have degraded to the built-in Nifty 50 fallback. Continuing, but "
             "this is NOT a 200-500 stock scan; see the fallback note in market.py.")

    today = dt.datetime.now(IST).date()
    history = MarketHistory(default_db_path(OUT_DIR))

    print("[3/8] Market benchmark (^NSEI) - fetched first so its canonical trading-session "
         "spine can be filtered into OHLCV acquisition BEFORE date-gap validation runs "
         "(Phase 4.2 Packet 5.3E: a provider holiday-placeholder row must not manufacture a "
         "false date_gap)...")
    t0 = time.time()
    benchmark_series = relative_acquisition.build_market_benchmark_series()
    benchmark_session = benchmark_series[-1]["date"] if benchmark_series else None
    spine = session_alignment.canonical_session_spine(benchmark_series)
    t_benchmark = time.time() - t0
    print(f"      spine sessions {len(spine)}, latest benchmark session: {benchmark_session} "
         f"({t_benchmark:.2f}s)")
    if benchmark_session is not None and benchmark_session != recap_date:
        print(f"      WARNING: benchmark session {benchmark_session} does not match the "
             f"stock session {recap_date} - market-relative figures needing that date will be "
             f"unavailable per symbol, not silently substituted.")

    print("[4/8] Shared OHLCV acquisition (read-through market_ohlcv.db, spine-aware, Yahoo "
         "only for what's missing)...")
    t0 = time.time()
    dataset = ohlcv_service.load_universe(universe, recap_date, prev_date, spine=spine)
    t_ohlcv = time.time() - t0
    print(f"      usable {len(dataset.series_by_symbol)}, skipped {len(dataset.skipped_symbols)}, "
         f"cache-complete {dataset.coverage.cache_complete}, "
         f"freshly-fetched {dataset.coverage.freshly_fetched}, "
         f"Yahoo calls {dataset.coverage.network_call_count} ({t_ohlcv:.1f}s)")
    # Defense-in-depth, kept even though the store path above is now already spine-filtered:
    # the Yahoo-fallback path (`ohlcv_service._fallback_full_fetch`, used when the store is
    # unavailable/unreadable) builds its series directly from `market.get_universe_technical_
    # series` without going through `apply_session_spine`, so a holiday placeholder could still
    # reach `dataset.series_by_symbol` from that path alone.
    t0 = time.time()
    alignment_report = session_alignment.align_dataset_to_spine(dataset, benchmark_series)
    t_alignment = time.time() - t0
    print(f"      post-hoc alignment (fallback-path safety net): symbols with a holiday row "
         f"filtered {alignment_report.symbols_filtered}, rows dropped "
         f"{alignment_report.rows_dropped}, newly skipped {len(alignment_report.newly_skipped)} "
         f"({t_alignment:.2f}s)")
    series_by_symbol, tech_skip_reasons = dataset.series_by_symbol, dataset.skipped_symbols

    print("[5/8] Volume: RVOL detection from the shared, session-aligned dataset...")
    t0 = time.time()
    scan_report, vol_skip_reasons, persisted = acquisition.acquire_universe_scan_report(
        history, universe, report_date=today, recap_date=recap_date, prev_date=prev_date,
        dataset=dataset)
    volume_snapshot = volume.scan_universe(scan_report, history, list(universe.keys()),
                                           acquisition_skip_reasons=vol_skip_reasons)
    t_volume = time.time() - t0
    print(f"      volume scanned {len(volume_snapshot.scanned)}, "
         f"anomalies {len(volume_snapshot.anomalies)}, skipped {len(volume_snapshot.skipped)} "
         f"({t_volume:.1f}s, persisted={persisted})")

    print("[6/8] Technical: structure detection from the shared, session-aligned dataset...")
    t0 = time.time()
    technical_snapshot = technical.scan_technical_universe(
        list(universe.keys()), series_by_symbol, recap_date, skip_reasons=tech_skip_reasons)
    t_technical = time.time() - t0
    print(f"      technical scanned {len(technical_snapshot.scanned)}, "
         f"flagged {len(technical_snapshot.flagged)}, skipped {len(technical_snapshot.skipped)} "
         f"({t_technical:.1f}s)")

    print("[7/8] Relative performance: detection from the shared, session-aligned dataset "
         "(benchmark already fetched in step 4; sector-relative skipped - no stock->sector "
         "mapping exists yet)...")
    t0 = time.time()
    relative_snapshot = relative.scan_relative_performance_universe(
        list(universe.keys()), series_by_symbol, benchmark_series, recap_date,
        skip_reasons=tech_skip_reasons)
    t_relative = time.time() - t0
    print(f"      relative scanned {len(relative_snapshot.scanned)}, "
         f"skipped {len(relative_snapshot.skipped)} ({t_relative:.1f}s)")

    print("[8/8] Composing evidence across all three families (no acquisition, no mutation)...")
    t0 = time.time()
    composite_snapshot = composite.build_candidates(volume_snapshot, technical_snapshot,
                                                     relative_snapshot, recap_date)
    t_composite = time.time() - t0
    print(f"      composite candidates {composite_snapshot.candidate_count} "
         f"(universe considered: {composite_snapshot.universe_size}) ({t_composite:.2f}s)")

    data_problems = {
        "volume_skip_reasons": _tally(volume_snapshot.skipped),
        "technical_skip_reasons": _tally(technical_snapshot.skipped),
        "relative_skip_reasons": _tally(relative_snapshot.skipped),
        "benchmark_session_mismatch": (benchmark_session != recap_date
                                       if benchmark_session else "benchmark unavailable"),
    }
    timings = {
        "nifty_session_fetch_s": round(t_nifty, 2), "universe_fetch_s": round(t_universe, 2),
        "ohlcv_acquisition_s": round(t_ohlcv, 2), "session_alignment_s": round(t_alignment, 2),
        "volume_s": round(t_volume, 2), "technical_s": round(t_technical, 2),
        "relative_s": round(t_relative, 2), "composite_s": round(t_composite, 2),
        "total_s": round(time.time() - t_start, 2),
    }

    report = assemble_radar_report(
        universe_name=args.universe, universe=universe, session_date=recap_date,
        benchmark_session=benchmark_session, volume_snapshot=volume_snapshot,
        technical_snapshot=technical_snapshot, relative_snapshot=relative_snapshot,
        composite_snapshot=composite_snapshot, series_by_symbol=series_by_symbol,
        scan_report=scan_report, data_problems=data_problems, timings=timings)

    path = save_radar_report(report)
    print(f"\n[radar.run] derived artifact written: {path}")
    print(f"[radar.run] total runtime: {timings['total_s']}s")
    _print_summary(report)
    return 0


def _tally(skipped: dict) -> dict:
    out: dict = {}
    for reason in skipped.values():
        out[reason] = out.get(reason, 0) + 1
    return out


def _print_summary(report: dict) -> None:
    u, d, pv = report["universe"], report["detector_coverage"], report["product_validation"]
    print("\n=== Coverage ===")
    print(f"Universe requested        {u['requested']}")
    print(f"Volume acquired           {u['volume_acquired']}")
    print(f"Technical acquired        {u['technical_acquired']}")
    print(f"Relative acquired         {u['relative_acquired']}")
    print(f"Composite candidates      {d['candidate_count']}  "
         f"(high-interest {d['high_interest_count']}, notable {d['notable_count']})")
    print("\n=== Product validation ===")
    print(f"Candidates outside top-5 movers by |%|: {pv['candidates_outside_top5_movers']} "
         f"of {d['candidate_count']}")
    print(f"Candidates with <2% move but multi-family evidence: "
         f"{pv['candidates_with_small_move_under_2pct']}")
    print(f"Three-family convergence: {pv['three_family_convergence_count']}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["assemble_radar_report", "save_radar_report", "main", "price_changes_from_series",
          "rvol_by_symbol_from_scan_report"]

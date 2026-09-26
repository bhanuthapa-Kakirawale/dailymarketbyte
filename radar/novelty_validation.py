"""Phase 4.2 Packet 5.4A: historical validation of deterministic event-novelty classification.

Applies `radar.novelty.classify_history` retrospectively to the exact corrected Packet 5.3E
20-session candidate history (561 raw candidates) and reports the measured novelty distribution.
Analysis only - the 561 raw candidates are never altered, reordered, or dropped; novelty is
purely additional metadata layered on top, exactly like `radar.novelty` itself promises.

Reused verbatim, never duplicated: `radar.historical_validation.select_session_dates` /
`run_one_session` (the same spine-aware, network-free session acquisition Packet 5.3E fixed),
`radar.novelty.classify_history` (the classification itself), `radar.historical_validation.
candidate_record` (the same per-candidate JSON shape 5.3D already defined).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import time
from collections import Counter

import market
from config import OUT_DIR
from storage.ohlcv_repository import OHLCVStore, default_db_path

from . import historical_validation as hv
from . import relative_acquisition, session_alignment
from .models import NoveltyType
from .novelty import classify_history
from .thresholds import DEFAULT_NOVELTY_THRESHOLDS, NoveltyThresholds

SCHEMA_VERSION = "1.0"
ARTIFACT_DIR = os.path.join(OUT_DIR, "radar_validation")
QUIET_MOVE_PCT = 2.0
QUIET_MAX_EXAMPLES = 15
NOVELTY_TYPES_ORDER = [t for t in NoveltyType]  # declaration order, deterministic


# ------------------------------------------------------------------ pure assembly
def combined_records(results: list, novelty_by_date: dict) -> list:
    """One dict per raw candidate, combining `hv.candidate_record`'s existing shape with its
    novelty classification - same order as each session's own candidate list, one-to-one."""
    records = []
    for r in results:
        session_date = r["session_date"]
        price_changes = r["price_changes"]
        top_movers = sorted(price_changes.items(), key=lambda kv: -abs(kv[1]))[:hv.TOP_MOVERS_N]
        top_mover_symbols = {s for s, _ in top_movers}
        candidates = r["composite_snapshot"].candidates
        novelties = novelty_by_date[session_date]
        if len(candidates) != len(novelties):
            raise AssertionError(f"{session_date}: candidate/novelty count mismatch "
                                 f"({len(candidates)} vs {len(novelties)})")
        for c, n in zip(candidates, novelties):
            rec = hv.candidate_record(c, session_date, top_mover_symbols)
            rec.update({
                "novelty_type": n.novelty_type.value,
                "previous_candidate_date": n.previous_candidate_date.isoformat()
                                          if n.previous_candidate_date else None,
                "sessions_since_previous_candidate": n.sessions_since_previous_candidate,
                "new_families": list(n.new_families), "lost_families": list(n.lost_families),
                "new_technical_events": list(n.new_technical_events),
                "previous_persistence": n.previous_persistence, "current_persistence": n.current_persistence,
                "previous_direction": n.previous_direction, "current_direction": n.current_direction,
                "previous_attention": n.previous_attention, "current_attention": n.current_attention,
                "novelty_change_reason_codes": list(n.change_reason_codes),
                "novelty_reason": n.reason,
            })
            records.append(rec)
    return records


def session_novelty_summary(session_date: dt.date, records: list) -> dict:
    novel = [r for r in records if r["novelty_type"] != NoveltyType.CONTINUATION.value]
    continuation = [r for r in records if r["novelty_type"] == NoveltyType.CONTINUATION.value]
    return {
        "session_date": session_date.isoformat(),
        "raw_candidate_count": len(records),
        "novel_candidate_count": len(novel),
        "continuation_count": len(continuation),
        "novelty_type_counts": _tally_types(records),
    }


def _tally_types(records: list) -> dict:
    out: dict = {t.value: 0 for t in NOVELTY_TYPES_ORDER}
    for r in records:
        out[r["novelty_type"]] += 1
    return out


def build_novelty_artifact(results: list, all_records: list, *,
                           thresholds: NoveltyThresholds = DEFAULT_NOVELTY_THRESHOLDS,
                           generated_at: dt.datetime | None = None,
                           elapsed_s: float | None = None) -> dict:
    """Pure assembly (packet spec sections 18-25) from already-classified `all_records`. No IO,
    no network, no mutation - `results`/`all_records` come from a prior
    `run_one_session`/`combined_records` call the caller already made."""
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)

    by_session: dict = {}
    for r in all_records:
        by_session.setdefault(r["date"], []).append(r)
    session_dates = [r["session_date"].isoformat() for r in results]

    session_summaries = [session_novelty_summary(dt.date.fromisoformat(d), by_session.get(d, []))
                         for d in session_dates]

    overall_type_counts = _tally_types(all_records)
    total = len(all_records)
    overall_type_pct = {k: (round(100.0 * v / total, 1) if total else None)
                        for k, v in overall_type_counts.items()}

    novel_counts_per_session = [s["novel_candidate_count"] for s in session_summaries]
    n = len(novel_counts_per_session)
    distribution_buckets = {"0": 0, "1-3": 0, "4-5": 0, "6-10": 0, "11-20": 0, ">20": 0}
    for c in novel_counts_per_session:
        if c == 0:
            distribution_buckets["0"] += 1
        elif c <= 3:
            distribution_buckets["1-3"] += 1
        elif c <= 5:
            distribution_buckets["4-5"] += 1
        elif c <= 10:
            distribution_buckets["6-10"] += 1
        elif c <= 20:
            distribution_buckets["11-20"] += 1
        else:
            distribution_buckets[">20"] += 1

    raw_recurrence = Counter(r["symbol"] for r in all_records)
    novel_recurrence = Counter(r["symbol"] for r in all_records
                               if r["novelty_type"] != NoveltyType.CONTINUATION.value)
    recurrence = {
        "raw_candidate_recurrence": _recurrence_buckets(raw_recurrence),
        "novel_event_recurrence": _recurrence_buckets(novel_recurrence),
        "raw_3plus_symbols": sorted(s for s, c in raw_recurrence.items() if c >= 3),
        "novel_3plus_symbols": sorted(s for s, c in novel_recurrence.items() if c >= 3),
    }

    three_family = [r for r in all_records if r["independent_signal_count"] == 3]
    three_family_breakdown = _tally_types(three_family)

    quiet = [r for r in all_records if r["price_change_pct"] is not None
            and abs(r["price_change_pct"]) < QUIET_MOVE_PCT]
    quiet_novel = [r for r in quiet if r["novelty_type"] != NoveltyType.CONTINUATION.value]
    quiet_continuation = [r for r in quiet if r["novelty_type"] == NoveltyType.CONTINUATION.value]
    quiet_novel_examples = sorted(quiet_novel, key=lambda r: (r["date"], r["symbol"]),
                                  reverse=True)[:QUIET_MAX_EXAMPLES]

    representative = _representative_transitions(all_records)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "metadata": {
            "sessions_analyzed": len(results), "session_dates": session_dates,
            "raw_candidate_total": total,
            "novelty_configuration": {"lookback_sessions": thresholds.lookback_sessions},
            "runtime_s": elapsed_s,
            "limitations": [
                "The novelty lookback for the FIRST session in this 20-session window (no "
                "prior in-scope session exists) means every candidate on that first date is "
                "unconditionally NEW_CANDIDATE, even if it was also a candidate in an earlier, "
                "out-of-scope real session - this is a boundary artifact of validating over a "
                "fixed 20-session slice, not a property of the novelty rule itself.",
                "Novelty state here is computed purely from this validation run's own 20-session "
                "sequence, per packet scope (no production database/table introduced yet).",
            ],
        },
        "session_summaries": session_summaries,
        "aggregate_novelty_counts": {"counts": overall_type_counts, "percentages": overall_type_pct,
                                     "total": total},
        "daily_novelty_distribution": {
            "min": min(novel_counts_per_session) if n else None,
            "max": max(novel_counts_per_session) if n else None,
            "median": statistics.median(novel_counts_per_session) if n else None,
            "mean": (sum(novel_counts_per_session) / n) if n else None,
            "buckets": distribution_buckets,
        },
        "recurrence_analysis": recurrence,
        "three_family_analysis": {
            "three_family_candidate_total": len(three_family),
            "novelty_breakdown": three_family_breakdown,
        },
        "quiet_discovery_analysis": {
            "raw_quiet_count": len(quiet), "novel_quiet_count": len(quiet_novel),
            "quiet_continuation_count": len(quiet_continuation),
            "examples": quiet_novel_examples,
        },
        "representative_transitions": representative,
        "candidates": all_records,
        "warnings": [],
    }


def _recurrence_buckets(counter: Counter) -> dict:
    buckets = {"1_time": 0, "2_times": 0, "3_plus_times": 0}
    for _sym, c in counter.items():
        if c == 1:
            buckets["1_time"] += 1
        elif c == 2:
            buckets["2_times"] += 1
        else:
            buckets["3_plus_times"] += 1
    return buckets


def _representative_transitions(records: list) -> dict:
    """One deterministic example per novelty type actually found - first by date ascending,
    then symbol - never chosen by any score or "best" heuristic."""
    ordered = sorted(records, key=lambda r: (r["date"], r["symbol"]))
    out: dict = {}
    for r in ordered:
        t = r["novelty_type"]
        if t not in out:
            out[t] = {
                "date": r["date"], "symbol": r["symbol"],
                "price_change_pct": r["price_change_pct"],
                "active_families": r["active_families"],
                "previous_persistence": r["previous_persistence"], "current_persistence": r["current_persistence"],
                "previous_direction": r["previous_direction"], "current_direction": r["current_direction"],
                "previous_attention": r["previous_attention"], "current_attention": r["current_attention"],
                "new_families": r["new_families"], "new_technical_events": r["new_technical_events"],
                "novelty_change_reason_codes": r["novelty_change_reason_codes"],
                "novelty_reason": r["novelty_reason"],
            }
    return out


# ------------------------------------------------------------------ IO
def save_artifact(artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"novelty_validation_{end_date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
    return path


def render_markdown(artifact: dict) -> str:
    m = artifact["metadata"]
    agg = artifact["aggregate_novelty_counts"]
    dist = artifact["daily_novelty_distribution"]
    lines = [
        "# Market Intelligence Radar - Event Novelty Validation",
        "", f"Generated: {artifact['generated_at']}",
        f"Sessions analyzed: {m['sessions_analyzed']}, raw candidates: {m['raw_candidate_total']}",
        f"Lookback: {m['novelty_configuration']['lookback_sessions']} valid trading sessions", "",
        "## Sessions", "", "| Date | Raw | Novel | Continuation |", "| --- | --- | --- | --- |",
    ]
    for s in artifact["session_summaries"]:
        lines.append(f"| {s['session_date']} | {s['raw_candidate_count']} | "
                    f"{s['novel_candidate_count']} | {s['continuation_count']} |")
    lines += ["", "## Novelty type counts", "", str(agg["counts"]), str(agg["percentages"]), "",
             "## Daily novel-developments distribution", "",
             f"min {dist['min']}, max {dist['max']}, median {dist['median']}, "
             f"mean {dist['mean']:.2f}" if dist["mean"] is not None else "no sessions",
             "", "Buckets: " + str(dist["buckets"]), "",
             "## Recurrence: raw vs novel", "",
             "Raw: " + str(artifact["recurrence_analysis"]["raw_candidate_recurrence"]),
             "Novel: " + str(artifact["recurrence_analysis"]["novel_event_recurrence"]), "",
             "## Three-family novelty breakdown", "",
             str(artifact["three_family_analysis"]["novelty_breakdown"]), "",
             "## Quiet discovery", "",
             f"raw {artifact['quiet_discovery_analysis']['raw_quiet_count']}, "
             f"novel {artifact['quiet_discovery_analysis']['novel_quiet_count']}, "
             f"continuation {artifact['quiet_discovery_analysis']['quiet_continuation_count']}", ""]
    for q in artifact["quiet_discovery_analysis"]["examples"]:
        lines.append(f"- {q['date']} {q['symbol']} ({q['novelty_type']}): "
                    f"{q['price_change_pct']:.2f}% - {q['novelty_reason']}")
    lines += ["", "## Representative transitions", ""]
    for t, ex in artifact["representative_transitions"].items():
        lines.append(f"- **{t}** - {ex['date']} {ex['symbol']}: {ex['novelty_reason']}")
    return "\n".join(lines)


def save_markdown(text: str, artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"novelty_validation_{end_date}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ------------------------------------------------------------------ real-IO main
def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4.2 Packet 5.4A historical novelty "
                                                 "validation - analysis only.")
    parser.add_argument("--universe", default="NIFTY200")
    parser.add_argument("--sessions", type=int, default=hv.TARGET_SESSIONS)
    parser.add_argument("--end-date", default=None,
                        help="ISO date to pin the 20-session window's last session to, for "
                             "exact reproducibility against a prior run (default: latest "
                             "available).")
    args = parser.parse_args(argv)

    t_start = time.time()
    print("[1/3] Universe + benchmark (identical acquisition to radar.historical_validation)...")
    universe_full = market.get_universe(args.universe)
    store = OHLCVStore(default_db_path(OUT_DIR))
    store_symbols = {row.symbol for row in store.get_range(
        list(universe_full), dt.date(2000, 1, 1), dt.date(2100, 1, 1))}
    universe = {s: universe_full[s] for s in universe_full if s in store_symbols}

    benchmark_series_full = relative_acquisition.build_market_benchmark_series(period="1y")
    if args.end_date:
        end = dt.date.fromisoformat(args.end_date)
        spine = session_alignment.canonical_session_list(benchmark_series_full, end=end)
        selected = spine[-args.sessions:]
        prev_dates = [spine[spine.index(d) - 1] for d in selected]
    else:
        selected, prev_dates, warnings = hv.select_session_dates(benchmark_series_full, args.sessions)
        for w in warnings:
            print(f"      WARNING: {w}")
    print(f"      {len(selected)} sessions: {selected[0]} .. {selected[-1]}" if selected else "      none")

    print("[2/3] Running Radar detectors for each historical session (store-only, 0 Yahoo calls)...")
    results = []
    for session_date, prev_date in zip(selected, prev_dates):
        r = hv.run_one_session(universe, session_date, prev_date, benchmark_series_full, store)
        results.append(r)
        print(f"      {session_date}: candidates {r['composite_snapshot'].candidate_count}")
    store.close()

    print("[3/3] Classifying novelty and assembling artifact...")
    sessions_for_novelty = [(r["session_date"], r["composite_snapshot"].candidates) for r in results]
    novelty_by_date = classify_history(sessions_for_novelty)
    all_records = combined_records(results, novelty_by_date)

    elapsed = time.time() - t_start
    artifact = build_novelty_artifact(results, all_records, elapsed_s=round(elapsed, 2))
    json_path = save_artifact(artifact)
    md_path = save_markdown(render_markdown(artifact), artifact)

    print(f"\n[radar.novelty_validation] artifacts written:\n  {json_path}\n  {md_path}")
    print(f"[radar.novelty_validation] total runtime: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["combined_records", "session_novelty_summary", "build_novelty_artifact",
          "save_artifact", "render_markdown", "save_markdown", "main"]

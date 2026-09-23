"""Phase 4.2 Packet 5.4B.2: 60-session (~3 month) editorial policy stability validation.

Composes the full deterministic Radar pipeline - OHLCV -> session alignment -> Volume/
Technical/Relative detectors -> Composite -> Novelty -> editorial policy simulation - entirely
from modules Packets 5.3D/5.3E/5.4A/5.4B/5.4B.1 already built and tested. This module adds NO
new detector, novelty, or policy logic: `radar.historical_validation.run_one_session` (store-
only, zero Yahoo universe calls), `radar.novelty.classify_history`,
`radar.editorial_policy.simulate_history` (Policies C/D, untouched) and
`radar.editorial_policy_hybrid.simulate_hybrid_history` (Policies E/F, untouched) are called
exactly as their own packets defined them - this module is composition and measurement only,
over a longer (60- rather than 20-session) historical window, plus a chronological 20/20/20
block breakdown to check whether the 20-session Packet 5.4B/5.4B.1 findings were representative
or a short-window artifact.
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

from . import editorial_policy as ep
from . import editorial_policy_hybrid as eph
from . import editorial_policy_validation as epv
from . import hybrid_editorial_policy_validation as hepv
from . import historical_validation as hv
from . import relative_acquisition
from .novelty import classify_history
from .novelty_validation import combined_records

SCHEMA_VERSION = "1.0"
ARTIFACT_DIR = os.path.join(OUT_DIR, "radar_validation")
SESSION_COUNT = 60
BLOCK_SIZE = 20
END_SESSION = dt.date(2026, 9, 21)


# ------------------------------------------------------------------ session selection (pinned end date)
def select_60_sessions(benchmark_series_full: list, *, end_session: dt.date = END_SESSION,
                       count: int = SESSION_COUNT) -> tuple[list, list, list]:
    """Same canonical-spine selection `radar.historical_validation.select_session_dates` already
    uses, pinned to `end_session` for exact reproducibility against the prior 20-session
    artifacts (packet spec section 1: "use the latest completed Radar session already used by
    validation"). Never calendar-day subtraction, never a hard-coded holiday list - purely the
    `^NSEI` benchmark's own trading-session spine, exactly like every other validation packet.
    """
    spine = sorted({row["date"] for row in benchmark_series_full if row["date"] <= end_session})
    warnings = []
    selectable = spine[1:]
    if len(selectable) < count:
        warnings.append(f"canonical spine (<= {end_session}) only supports {len(selectable)} "
                        f"sessions with a known prior session; requested {count} - NOT silently "
                        "shortened, this is the maximum available.")
        chosen = selectable
    else:
        chosen = selectable[-count:]
    prev_dates = [spine[spine.index(d) - 1] for d in chosen]
    return chosen, prev_dates, warnings


# ------------------------------------------------------------------ coverage
def coverage_report(results: list) -> dict:
    per_session = []
    for r in results:
        dataset = r["dataset"]
        requested = len(dataset.requested_symbols)
        usable = len(dataset.series_by_symbol)
        per_session.append({
            "session_date": r["session_date"].isoformat(), "requested_symbols": requested,
            "usable_symbols": usable, "skipped_symbols": len(dataset.skipped_symbols),
            "skip_reasons": _tally(dataset.skipped_symbols),
            "full_coverage": usable == requested,
        })
    usable_counts = [s["usable_symbols"] for s in per_session]
    full = sum(1 for s in per_session if s["full_coverage"])
    below = [s for s in per_session if not s["full_coverage"]]
    return {
        "per_session": per_session,
        "sessions_with_full_coverage": full,
        "sessions_below_full_coverage": len(below),
        "incomplete_sessions": [{"session_date": s["session_date"], "usable_symbols": s["usable_symbols"],
                                "skip_reasons": s["skip_reasons"]} for s in below],
        "minimum_usable_symbols": min(usable_counts) if usable_counts else None,
        "mean_usable_symbols": (sum(usable_counts) / len(usable_counts)) if usable_counts else None,
    }


def _tally(skipped: dict) -> dict:
    out: dict = {}
    for reason in skipped.values():
        out[reason] = out.get(reason, 0) + 1
    return out


# ------------------------------------------------------------------ candidate funnel
def candidate_funnel(results: list, all_records: list) -> dict:
    by_session_raw = {r["session_date"]: r["composite_snapshot"].candidate_count for r in results}
    by_session_novel: Counter = Counter()
    by_session_continuation: Counter = Counter()
    for rec in all_records:
        d = dt.date.fromisoformat(rec["date"])
        if rec["novelty_type"] == "CONTINUATION":
            by_session_continuation[d] += 1
        else:
            by_session_novel[d] += 1

    session_dates = [r["session_date"] for r in results]
    raw_counts = [by_session_raw[d] for d in session_dates]
    novel_counts = [by_session_novel.get(d, 0) for d in session_dates]
    cont_counts = [by_session_continuation.get(d, 0) for d in session_dates]

    def _stats(counts):
        return {"min": min(counts), "mean": sum(counts) / len(counts), "median": statistics.median(counts),
               "max": max(counts)} if counts else {"min": None, "mean": None, "median": None, "max": None}

    return {
        "raw_candidate_total": sum(raw_counts), "novel_total": sum(novel_counts),
        "continuation_total": sum(cont_counts),
        "raw_per_session": _stats(raw_counts), "novel_per_session": _stats(novel_counts),
        "continuation_per_session": _stats(cont_counts),
    }


# ------------------------------------------------------------------ direction availability context
def direction_availability(sessions: list, stories_by_date: dict) -> dict:
    """For every session where a policy's OWN selected set is single-direction (all-positive or
    all-negative), whether the OPPOSITE direction existed anywhere in that session's full novel
    pool - distinguishing "no diversity possible" from "diversity possible but not achieved"
    (packet spec section 13)."""
    pool_directions = {}
    for d, records in sessions:
        pool = ep.eligible_pool(records)
        pool_directions[d] = {r["direction_compatibility"] for r in pool}

    because_unavailable = despite_available = 0
    for d, _records in sessions:
        selected_dirs = {s["direction_compatibility"] for s in stories_by_date.get(d, [])}
        if selected_dirs == {"ALIGNED_POSITIVE"}:
            missing = "ALIGNED_NEGATIVE"
        elif selected_dirs == {"ALIGNED_NEGATIVE"}:
            missing = "ALIGNED_POSITIVE"
        else:
            continue
        if missing in pool_directions[d]:
            despite_available += 1
        else:
            because_unavailable += 1
    return {"single_direction_because_unavailable": because_unavailable,
           "single_direction_despite_both_available": despite_available}


# ------------------------------------------------------------------ recurrence (top 15)
def recurrence_report(all_stories: list) -> dict:
    counts = Counter(s["symbol"] for s in all_stories)
    buckets = {"1_time": 0, "2_times": 0, "3_to_5_times": 0, "gt_5_times": 0}
    for _sym, c in counts.items():
        if c == 1:
            buckets["1_time"] += 1
        elif c == 2:
            buckets["2_times"] += 1
        elif c <= 5:
            buckets["3_to_5_times"] += 1
        else:
            buckets["gt_5_times"] += 1
    top15 = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    total_selections = sum(counts.values())
    return {"buckets": buckets, "max_appearances": max(counts.values()) if counts else 0,
           "unique_symbols": len(counts), "total_selections": total_selections,
           "unique_to_total_ratio": round(len(counts) / total_selections, 3) if total_selections else None,
           "top_15_symbols": [{"symbol": s, "count": c} for s, c in top15]}


# ------------------------------------------------------------------ block metrics
def block_report(policy_label: str, sessions: list, stories_by_date: dict, suppressed_by_date: dict | None,
                 block_bounds: tuple) -> dict:
    start, end = block_bounds
    block_sessions = sessions[start:end]
    return hepv.policy_report(policy_label, block_sessions, stories_by_date, suppressed_by_date)


def block_breakdown(sessions: list, sim_cd: dict, sim_hybrid: dict) -> dict:
    n = len(sessions)
    bounds = [(0, min(BLOCK_SIZE, n)), (BLOCK_SIZE, min(2 * BLOCK_SIZE, n)), (2 * BLOCK_SIZE, n)]
    labels = ["block_1_sessions_1_20", "block_2_sessions_21_40", "block_3_sessions_41_60"]

    policies = {
        ep.POLICY_STATE_CHANGE_WITH_COOLDOWN: (sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
                                              sim_cd["suppressed"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN]),
        ep.POLICY_DIVERSITY_AWARE: (sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
                                   sim_cd["suppressed"][ep.POLICY_DIVERSITY_AWARE]),
        eph.POLICY_HYBRID_RESERVED: (sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
                                    sim_hybrid["suppressed"][eph.POLICY_HYBRID_RESERVED]),
        eph.POLICY_HYBRID_RESERVED_DIVERSITY: (sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
                                              sim_hybrid["suppressed"][eph.POLICY_HYBRID_RESERVED_DIVERSITY]),
    }

    out = {}
    for label, (b_start, b_end) in zip(labels, bounds):
        block_sessions = sessions[b_start:b_end]
        block_dates = {d for d, _ in block_sessions}
        block_out = {"session_dates": [d.isoformat() for d in sorted(block_dates)]}
        for name, (stories_by_date, suppressed_by_date) in policies.items():
            report = hepv.policy_report(name, block_sessions, stories_by_date, suppressed_by_date)
            block_out[name] = {
                "three_family_capture_pct": report["three_family_capture"]["capture_pct"],
                "multiple_changes_capture_pct": _capture_pct(report["state_change_capture"],
                                                              "MULTIPLE_CHANGES"),
                "quiet_lt_2pct": report["quiet_discovery_preservation"]["pct_lt_2pct"],
                "outside_top5_pct": report["top_mover_differentiation"]["pct_outside_top5"],
                "direction_counts": report["direction_distribution"]["counts"],
            }
            all_stories = [s for stories in stories_by_date.values() for s in stories
                          if s["session_date"] in {d.isoformat() for d in block_dates}]
            rec = recurrence_report(all_stories)
            block_out[name]["unique_to_total_ratio"] = rec["unique_to_total_ratio"]
        # reservation-change % for E/F within this block
        for name, disp_fn in ((eph.POLICY_HYBRID_RESERVED, ep.POLICY_STATE_CHANGE_WITH_COOLDOWN),
                              (eph.POLICY_HYBRID_RESERVED_DIVERSITY, ep.POLICY_DIVERSITY_AWARE)):
            disp = hepv.displacement_analysis(block_sessions, sim_hybrid["stories"][name],
                                              sim_cd["stories"][disp_fn])
            n_block = len(block_sessions)
            block_out[name]["reservation_change_pct"] = epv._pct(disp["sessions_with_any_swap"], n_block)
        out[label] = block_out
    return out


def _capture_pct(state_change_capture: dict, novelty_type: str) -> float | None:
    eligible = state_change_capture.get(f"eligible_{novelty_type}")
    selected = state_change_capture.get(f"selected_{novelty_type}")
    return epv._pct(selected, eligible)


# ------------------------------------------------------------------ full artifact
def build_artifact(results: list, all_records: list, sessions: list, *,
                   coverage: dict, network_calls: dict, elapsed_s: float,
                   session_selection_warnings: list,
                   generated_at: dt.datetime | None = None) -> dict:
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)
    session_dates = [d for d, _ in sessions]

    sim_cd = ep.simulate_history(sessions)
    sim_hybrid = eph.simulate_hybrid_history(sessions)

    funnel = candidate_funnel(results, all_records)

    reports = {
        ep.POLICY_STATE_CHANGE_WITH_COOLDOWN: hepv.policy_report(
            ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, sessions,
            sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
            sim_cd["suppressed"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN]),
        ep.POLICY_DIVERSITY_AWARE: hepv.policy_report(
            ep.POLICY_DIVERSITY_AWARE, sessions, sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
            sim_cd["suppressed"][ep.POLICY_DIVERSITY_AWARE]),
        eph.POLICY_HYBRID_RESERVED: hepv.policy_report(
            eph.POLICY_HYBRID_RESERVED, sessions, sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
            sim_hybrid["suppressed"][eph.POLICY_HYBRID_RESERVED]),
        eph.POLICY_HYBRID_RESERVED_DIVERSITY: hepv.policy_report(
            eph.POLICY_HYBRID_RESERVED_DIVERSITY, sessions,
            sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
            sim_hybrid["suppressed"][eph.POLICY_HYBRID_RESERVED_DIVERSITY]),
    }
    # richer recurrence (top 15, unique ratio) replacing epv's top-10 default
    for name, rep in reports.items():
        stories_by_date = {
            ep.POLICY_STATE_CHANGE_WITH_COOLDOWN: sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
            ep.POLICY_DIVERSITY_AWARE: sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
            eph.POLICY_HYBRID_RESERVED: sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
            eph.POLICY_HYBRID_RESERVED_DIVERSITY: sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
        }[name]
        all_stories = [s for stories in stories_by_date.values() for s in stories]
        rep["publication_recurrence"] = recurrence_report(all_stories)
        rep["direction_availability_context"] = direction_availability(sessions, stories_by_date)

    reservation = {p: hepv.reservation_activation(sim_hybrid, p) for p in eph.ALL_HYBRID_POLICIES}
    disp_e_vs_c = hepv.displacement_analysis(sessions, sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
                                             sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN])
    disp_f_vs_d = hepv.displacement_analysis(sessions, sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
                                             sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE])

    overlaps_overall = [
        hepv.overlap(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
                    sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
                    ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, ep.POLICY_DIVERSITY_AWARE),
        hepv.overlap(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
                    sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
                    ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, eph.POLICY_HYBRID_RESERVED),
        hepv.overlap(sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
                    sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
                    ep.POLICY_DIVERSITY_AWARE, eph.POLICY_HYBRID_RESERVED_DIVERSITY),
        hepv.overlap(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
                    sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
                    eph.POLICY_HYBRID_RESERVED, eph.POLICY_HYBRID_RESERVED_DIVERSITY),
    ]

    overlaps_by_block = {}
    bounds = [(0, min(BLOCK_SIZE, len(sessions))), (BLOCK_SIZE, min(2 * BLOCK_SIZE, len(sessions))),
             (2 * BLOCK_SIZE, len(sessions))]
    for label, (b_start, b_end) in zip(["block_1", "block_2", "block_3"], bounds):
        block_dates = {d.isoformat() for d, _ in sessions[b_start:b_end]}

        def _restrict(stories_by_date):
            return {d: v for d, v in stories_by_date.items() if d.isoformat() in block_dates}

        overlaps_by_block[label] = [
            hepv.overlap(_restrict(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN]),
                        _restrict(sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE]), "C", "D"),
            hepv.overlap(_restrict(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN]),
                        _restrict(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED]), "C", "E"),
            hepv.overlap(_restrict(sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE]),
                        _restrict(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY]), "D", "F"),
            hepv.overlap(_restrict(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED]),
                        _restrict(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY]), "E", "F"),
        ]

    blocks = block_breakdown(sessions, sim_cd, sim_hybrid)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "configuration": {
            "sessions_requested": SESSION_COUNT, "end_session": END_SESSION.isoformat(),
            "max_stories_per_session": ep.MAX_STORIES_PER_SESSION,
            "publication_cooldown_sessions": ep.PUBLICATION_COOLDOWN_SESSIONS,
            "max_reserved_3family_slots": eph.MAX_RESERVED_3FAMILY_SLOTS,
        },
        "metadata": {
            "sessions_analyzed": len(sessions), "start_session": session_dates[0].isoformat(),
            "end_session": session_dates[-1].isoformat(),
            "session_dates": [d.isoformat() for d in session_dates],
            "session_selection_warnings": session_selection_warnings,
            "network_calls": network_calls, "runtime_s": elapsed_s,
        },
        "coverage": coverage,
        "candidate_funnel": funnel,
        "policies": reports,
        "reservation_activation": reservation,
        "displacement_e_vs_c": disp_e_vs_c,
        "displacement_f_vs_d": disp_f_vs_d,
        "policy_overlap_overall": overlaps_overall,
        "policy_overlap_by_block": overlaps_by_block,
        "block_breakdown": blocks,
        "warnings": [],
    }


def save_artifact(artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "editorial_policy_validation_60_sessions_2026-09-21.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
    return path


def render_markdown(artifact: dict) -> str:
    m, cov, funnel = artifact["metadata"], artifact["coverage"], artifact["candidate_funnel"]
    lines = ["# 60-Session Editorial Policy Validation", "", f"Generated: {artifact['generated_at']}",
            f"Sessions: {m['sessions_analyzed']} ({m['start_session']} .. {m['end_session']})", "",
            "## Coverage", "",
            f"Full coverage sessions: {cov['sessions_with_full_coverage']}/{m['sessions_analyzed']}, "
            f"min usable {cov['minimum_usable_symbols']}, mean usable "
            f"{cov['mean_usable_symbols']:.1f}" if cov["mean_usable_symbols"] is not None else "no data", "",
            "## Candidate funnel", "",
            f"Raw: {funnel['raw_candidate_total']}, novel: {funnel['novel_total']}, "
            f"continuation: {funnel['continuation_total']}", ""]
    for name, r in artifact["policies"].items():
        sps = r["stories_per_session"]
        lines += [f"## {name}", "",
                 f"Total: {r['total_stories']}; min/mean/median/max: {sps['min']}/"
                 f"{sps['mean']:.2f}/{sps['median']}/{sps['max']}" if sps["mean"] is not None else "none",
                 f"3-family capture: {r['three_family_capture']['capture_pct']}%",
                 f"Quiet <2%: {r['quiet_discovery_preservation']['pct_lt_2pct']}%",
                 f"Outside-top-5: {r['top_mover_differentiation']['pct_outside_top5']}%",
                 f"Unique/total: {r['publication_recurrence']['unique_to_total_ratio']}", ""]
    lines += ["## Block breakdown", "", str(artifact["block_breakdown"]), "",
             "## Policy overlap (overall)", ""]
    for o in artifact["policy_overlap_overall"]:
        lines.append(f"- {o['a']} vs {o['b']}: {o['jaccard_overlap_pct']}%")
    return "\n".join(lines)


def save_markdown(text: str, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "editorial_policy_validation_60_sessions_2026-09-21.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ------------------------------------------------------------------ real-IO main
def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4.2 Packet 5.4B.2: 60-session "
                                                 "editorial policy stability validation.")
    parser.add_argument("--universe", default="NIFTY200")
    args = parser.parse_args(argv)

    t_start = time.time()
    network_calls = {"universe_constituents_nse": 0, "benchmark_yahoo": 0, "universe_yahoo": 0,
                     "ohlcv_store_reads": 0}

    print("[1/4] Universe + benchmark...")
    universe_full = market.get_universe(args.universe)
    network_calls["universe_constituents_nse"] = 1
    store = OHLCVStore(default_db_path(OUT_DIR))
    store_symbols = {row.symbol for row in store.get_range(
        list(universe_full), dt.date(2000, 1, 1), dt.date(2100, 1, 1))}
    network_calls["ohlcv_store_reads"] += 1
    universe = {s: universe_full[s] for s in universe_full if s in store_symbols}

    benchmark_series_full = relative_acquisition.build_market_benchmark_series(period="1y")
    network_calls["benchmark_yahoo"] = 1
    selected, prev_dates, warnings = select_60_sessions(benchmark_series_full)
    for w in warnings:
        print(f"      WARNING: {w}")
    print(f"      {len(selected)} sessions: {selected[0]} .. {selected[-1]}")

    print(f"[2/4] Running Radar pipeline for {len(selected)} historical sessions "
         "(store-only, 0 Yahoo universe calls)...")
    results = []
    for session_date, prev_date in zip(selected, prev_dates):
        r = hv.run_one_session(universe, session_date, prev_date, benchmark_series_full, store)
        results.append(r)
        network_calls["ohlcv_store_reads"] += 1
    store.close()
    print(f"      done: {sum(r['composite_snapshot'].candidate_count for r in results)} raw candidates")

    print("[3/4] Novelty classification...")
    sessions_for_novelty = [(r["session_date"], r["composite_snapshot"].candidates) for r in results]
    novelty_by_date = classify_history(sessions_for_novelty)
    all_records = combined_records(results, novelty_by_date)

    print("[4/4] Editorial policy simulation (C/D/E/F, unmodified) + block analysis...")
    by_date: dict = {}
    for rec in all_records:
        by_date.setdefault(rec["date"], []).append(rec)
    sessions = [(r["session_date"], by_date.get(r["session_date"].isoformat(), [])) for r in results]

    coverage = coverage_report(results)
    elapsed = time.time() - t_start
    artifact = build_artifact(results, all_records, sessions, coverage=coverage,
                              network_calls=network_calls, elapsed_s=round(elapsed, 2),
                              session_selection_warnings=warnings)
    json_path = save_artifact(artifact)
    md_path = save_markdown(render_markdown(artifact))
    print(f"\n[radar.editorial_policy_validation_60] artifacts written:\n  {json_path}\n  {md_path}")
    print(f"[radar.editorial_policy_validation_60] total runtime: {elapsed:.1f}s, "
         f"network calls: {network_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["select_60_sessions", "coverage_report", "candidate_funnel", "direction_availability",
          "recurrence_report", "block_breakdown", "build_artifact", "save_artifact",
          "render_markdown", "save_markdown", "main"]

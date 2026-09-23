"""Phase 4.2 Packet 5.3D: 20-session historical Market Intelligence Radar validation.

HISTORICAL PRODUCT VALIDATION ONLY. This module changes nothing about the intelligence engine:
no detector formula, threshold, or composite rule is touched. It answers one question, run
retrospectively across the most recent 20 completed NSE trading sessions the local OHLCV store
covers: does the Radar consistently surface a small, useful set of multi-family developments -
especially ones a top-movers screen would not show?

Reused verbatim from production, never duplicated:
  * `radar.ohlcv_service` (the shared dataset shape, its per-symbol quality/alignment checks)
  * `radar.session_alignment` (canonical NSE trading-session spine, holiday-placeholder filter)
  * `radar.volume.scan_universe` / `radar.technical.scan_technical_universe` /
    `radar.relative.scan_relative_performance_universe` / `radar.composite.build_candidates`
  * `radar.acquisition.build_universe_relative_volume_facts` (RVOL fact construction, unsaved)
  * `radar.run.price_changes_from_series` (the same day-over-day % used for the top-movers diagnostic)

One new piece this module adds: `load_universe_offline`, a store-only dataset loader that NEVER
calls Yahoo. Production's `radar.ohlcv_service.load_universe` falls back to a live fetch on a
cache miss/tail gap (correct for a daily run); a historical backtest over 20 already-local
sessions must not silently phone out to Yahoo for a symbol whose local cache happens to be
mid-backfill on some day in the sample - that would make network reachability part of whether
this validation run even completes, and would violate "ideally 0 Yahoo universe calls" (packet
spec section 18). A cache gap here is recorded as a normal, expected data-quality skip instead
(`radar.ohlcv_service._finalize_symbol` - the exact SAME per-symbol quality checks production
uses - decides whether a symbol's local series is usable; this module only removes the
fetch-on-miss branch).

Strict point-in-time rule (packet spec section 4): `storage.ohlcv_repository.OHLCVStore.get_range`
is called with `end_date=session_date` for every session under test, so no row dated after that
session is ever read from the store. This module additionally truncates the market benchmark
series to `<= session_date` before it reaches session alignment or the relative-performance
detector - not because the existing detectors would misuse a later row (they look up returns by
exact date pairs, never by position past the requested session), but so that no later-dated row
is even PRESENT in memory during a historical session's computation - defense in depth, and what
`tests/test_radar_historical_validation.py::test_future_session_never_read` asserts directly.

Nothing here calls Gemini or any LLM, persists to `market_history.db`, or mutates a `MarketReport`
- see module-level tests for direct assertions of both.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import time
from collections import Counter

import market
from adapters.market_adapter import MarketAdapter  # noqa: F401  (documents the reused adapter path)
from config import OUT_DIR
from core import MarketReport, ReportType
from core.sources import registry_snapshot
from storage.ohlcv_models import QualityStatus
from storage.ohlcv_repository import OHLCVStore, default_db_path

from . import acquisition, composite, ohlcv_service, relative, relative_acquisition, session_alignment, technical
from .models import AttentionLevel, DirectionCompatibility
from .run import price_changes_from_series

SCHEMA_VERSION = "1.0"
TARGET_SESSIONS = 20
ARTIFACT_DIR = os.path.join(OUT_DIR, "radar_validation")
TOP_MOVERS_N = 5
QUIET_MOVE_PCT = 2.0
QUIET_MIN_FAMILIES = 2
QUIET_MAX_EXAMPLES = 15


# ------------------------------------------------------------------ offline (network-free) load
def load_universe_offline(universe: dict, session_date: dt.date, prev_date: dt.date,
                          store: OHLCVStore, *, spine: frozenset | None = None
                          ) -> "ohlcv_service.UniverseOHLCVDataset":
    """Store-only `UniverseOHLCVDataset` for one historical session - NEVER calls Yahoo.

    Reuses `radar.ohlcv_service._finalize_symbol` (the exact per-symbol "does this series end at
    `session_date`, is the immediately-prior row `prev_date`, is there enough history" check
    production's own `load_universe` applies) so a warm-cache symbol here is exactly as
    trustworthy as one served by the daily production path - only the fetch-on-gap branch is
    removed. `store.get_range(..., end_date=session_date)` is what makes this call structurally
    incapable of reading a row dated after `session_date`.

    `spine` (Phase 4.2 Packet 5.3E): the canonical NSE trading-session spine for this session
    (typically `radar.session_alignment.canonical_session_spine(benchmark_trunc)`, computed by
    the caller from the already-truncated benchmark series). Applied via the SAME
    `radar.ohlcv_service.apply_session_spine` production's own `load_universe` now uses, BEFORE
    the per-symbol finalize/date-gap check below - not a separate historical-only
    implementation of the rule.
    """
    symbols = list(universe)
    dataset = ohlcv_service.UniverseOHLCVDataset(session_date=session_date, requested_symbols=symbols)
    dataset.coverage.requested_symbols = len(symbols)

    start = session_date - dt.timedelta(days=ohlcv_service.STORE_QUERY_WINDOW_DAYS)
    rows = store.get_range(symbols, start, session_date, source="yahoo")
    dataset.coverage.bars_loaded_from_store = len(rows)

    by_symbol: dict = {}
    for r in rows:
        if r.quality_status != QualityStatus.OK:
            continue
        by_symbol.setdefault(r.symbol, []).append(r)

    dropped = ohlcv_service.apply_session_spine(by_symbol, spine)
    if dropped:
        dataset.warnings.append(f"session spine filter: {dropped} provider row(s) for confirmed "
                                f"non-trading dates excluded before completeness validation")

    for s in symbols:
        ok_rows = sorted(by_symbol.get(s, []), key=lambda r: r.session_date)
        if not ok_rows:
            dataset.skipped_symbols[s] = "no_local_cache"
            continue
        ohlcv_service._finalize_symbol(dataset, s, ok_rows, session_date, prev_date)

    dataset.available_symbols = sorted(dataset.series_by_symbol)
    dataset.coverage.cache_complete = len(dataset.series_by_symbol)
    dataset.coverage.usable_symbols = len(dataset.series_by_symbol)
    dataset.coverage.skipped_symbols = len(dataset.skipped_symbols)
    dataset.coverage.network_call_count = 0
    return dataset


# ------------------------------------------------------------------ session selection
def select_session_dates(benchmark_series: list, count: int = TARGET_SESSIONS) -> tuple[list, list, list]:
    """The latest `count` sessions the canonical NIFTY 50 spine itself confirms traded, each
    paired with its own spine-derived `prev_date` (never calendar-day subtraction - packet spec
    section 3). Returns `(selected_dates, prev_dates, warnings)`, both lists oldest-first.

    A session needs at least one spine entry before it to have a `prev_date` at all; the very
    first spine date is therefore never selectable, exactly like `market.get_market()`'s own
    `recap_date`/`prev_date` pairing requires a session before the one being described.
    """
    spine = sorted({row["date"] for row in benchmark_series if row.get("date") is not None})
    warnings = []
    selectable = spine[1:]  # every date except the very first has a spine prev_date
    if len(selectable) < count:
        warnings.append(f"canonical spine only supports {len(selectable)} sessions with a "
                        f"known prior session; requested {count}")
    chosen = selectable[-count:]
    prev_dates = [spine[spine.index(d) - 1] for d in chosen]
    return chosen, prev_dates, warnings


# ------------------------------------------------------------------ one session
def run_one_session(universe: dict, session_date: dt.date, prev_date: dt.date,
                    benchmark_series_full: list, store: OHLCVStore, *,
                    as_of: dt.datetime | None = None) -> dict:
    """Run all three detectors + composition for one historical session, entirely from local
    data. Returns a dict bundling the four snapshots plus coverage/alignment diagnostics -
    nothing here is persisted anywhere: no canonical-history write of any kind."""
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    benchmark_trunc = [row for row in benchmark_series_full if row["date"] <= session_date]
    spine = session_alignment.canonical_session_spine(benchmark_trunc)

    dataset = load_universe_offline(universe, session_date, prev_date, store, spine=spine)
    # Defense-in-depth safety net only, mirroring radar.run.py - the store-only load above is
    # already spine-filtered before its own completeness check via `spine=` above.
    alignment_report = session_alignment.align_dataset_to_spine(dataset, benchmark_trunc)

    facts, vol_skip = acquisition.build_universe_relative_volume_facts(
        universe, session_date, prev_date, retrieved_at=as_of, dataset=dataset, now=as_of)
    ephemeral_report = MarketReport(
        report_date=session_date, report_type=ReportType.RADAR_SCAN, session_date=session_date,
        generated_at=as_of, facts=facts,
        sources=registry_snapshot(f.metadata.get("published_value_source", "") for f in facts),
        metadata={"radar_scan": True, "historical_validation": True,
                 "notes": "Phase 4.2 Packet 5.3D historical validation artifact - never "
                          "persisted, never read by video.py/editorial/, not canonical history."})
    # NOTE: `ephemeral_report` is deliberately never persisted to canonical MarketHistory
    # anywhere in this module - see
    # tests/test_radar_historical_validation.py::test_no_canonical_persistence_call_in_source.

    volume_snapshot = volume_scan(ephemeral_report, vol_skip, universe)
    technical_snapshot = technical.scan_technical_universe(
        list(universe), dataset.series_by_symbol, session_date, skip_reasons=dataset.skipped_symbols)
    relative_snapshot = relative.scan_relative_performance_universe(
        list(universe), dataset.series_by_symbol, benchmark_trunc, session_date,
        skip_reasons=dataset.skipped_symbols)
    composite_snapshot = composite.build_candidates(
        volume_snapshot, technical_snapshot, relative_snapshot, session_date, as_of=as_of)

    price_changes = price_changes_from_series(dataset.series_by_symbol)

    return {
        "session_date": session_date, "prev_date": prev_date, "dataset": dataset,
        "alignment_report": alignment_report, "volume_snapshot": volume_snapshot,
        "technical_snapshot": technical_snapshot, "relative_snapshot": relative_snapshot,
        "composite_snapshot": composite_snapshot, "price_changes": price_changes,
    }


def volume_scan(ephemeral_report, vol_skip, universe):
    """Thin wrapper so the import stays local to one place - `radar.volume.scan_universe` with
    `history=None`: percentile classification is explicitly omitted (RVOL-alone fallback, an
    already-documented degrade path - see docs/MARKET_INTELLIGENCE_RADAR.md's "Cold start and
    failure" table) rather than accumulating a throwaway canonical-history database for this
    validation run. This keeps the run's `market_history.db` footprint at exactly zero, at the
    cost of never upgrading a volume reading from RVOL-alone to percentile-upgraded - noted as a
    limitation in the returned artifact, never silently assumed away."""
    from . import volume
    return volume.scan_universe(ephemeral_report, None, list(universe),
                                acquisition_skip_reasons=vol_skip)


# ------------------------------------------------------------------ per-session metrics (section 6)
def session_metrics(result: dict) -> dict:
    vsnap, tsnap, rsnap, csnap = (result["volume_snapshot"], result["technical_snapshot"],
                                  result["relative_snapshot"], result["composite_snapshot"])
    price_changes = result["price_changes"]
    candidates = csnap.candidates

    rel_counts = Counter()
    for r in rsnap.results:
        if r.persistence_state is None:
            rel_counts["neutral_or_unavailable"] += 1
        elif r.persistence_state.value == "PERSISTENT_POSITIVE":
            rel_counts["positive"] += 1
        elif r.persistence_state.value == "PERSISTENT_NEGATIVE":
            rel_counts["negative"] += 1
        elif r.persistence_state.value == "MIXED":
            rel_counts["mixed"] += 1
        else:
            rel_counts["neutral_or_unavailable"] += 1

    top_movers = sorted(price_changes.items(), key=lambda kv: -abs(kv[1]))[:TOP_MOVERS_N]
    top_mover_symbols = {s for s, _ in top_movers}
    outside_top5 = sum(1 for c in candidates if c.instrument not in top_mover_symbols)
    lt1 = sum(1 for c in candidates if c.price_change_pct is not None and abs(c.price_change_pct) < 1.0)
    lt2 = sum(1 for c in candidates if c.price_change_pct is not None and abs(c.price_change_pct) < 2.0)
    lt3 = sum(1 for c in candidates if c.price_change_pct is not None and abs(c.price_change_pct) < 3.0)

    aligned_pos = sum(1 for c in candidates if c.direction_compatibility == DirectionCompatibility.ALIGNED_POSITIVE)
    aligned_neg = sum(1 for c in candidates if c.direction_compatibility == DirectionCompatibility.ALIGNED_NEGATIVE)
    mixed_dir = sum(1 for c in candidates if c.direction_compatibility == DirectionCompatibility.MIXED)

    return {
        "session_date": result["session_date"].isoformat(),
        "prev_date": result["prev_date"].isoformat(),
        "universe_size": len(result["dataset"].requested_symbols),
        "usable_symbols": len(result["dataset"].series_by_symbol),
        "volume_anomalies": len(vsnap.anomalies),
        "technical_flagged": len(tsnap.flagged),
        "relative_positive": rel_counts["positive"], "relative_negative": rel_counts["negative"],
        "relative_neutral": rel_counts["neutral_or_unavailable"], "relative_mixed": rel_counts["mixed"],
        "candidate_count": csnap.candidate_count,
        "two_family_count": sum(1 for c in candidates if c.independent_signal_count == 2),
        "three_family_count": sum(1 for c in candidates if c.independent_signal_count == 3),
        "high_interest_count": sum(1 for c in candidates if c.attention_level == AttentionLevel.HIGH_INTEREST),
        "notable_count": sum(1 for c in candidates if c.attention_level == AttentionLevel.NOTABLE),
        "aligned_positive": aligned_pos, "aligned_negative": aligned_neg, "mixed_direction": mixed_dir,
        "candidate_outside_top5_movers_count": outside_top5,
        "candidate_abs_move_lt_1pct": lt1, "candidate_abs_move_lt_2pct": lt2, "candidate_abs_move_lt_3pct": lt3,
        "top5_movers": [{"symbol": s, "price_change_pct": p} for s, p in top_movers],
        "data_quality": {
            "volume_skip_reasons": _tally(vsnap.skipped), "technical_skip_reasons": _tally(tsnap.skipped),
            "relative_skip_reasons": _tally(rsnap.skipped),
            "alignment_symbols_filtered": result["alignment_report"].symbols_filtered,
            "alignment_rows_dropped": result["alignment_report"].rows_dropped,
        },
    }


def _tally(skipped: dict) -> dict:
    out: dict = {}
    for reason in skipped.values():
        out[reason] = out.get(reason, 0) + 1
    return out


# ------------------------------------------------------------------ candidate-level records
def candidate_record(c, session_date: dt.date, top_mover_symbols: set) -> dict:
    return {
        "date": session_date.isoformat(), "symbol": c.instrument,
        "price_change_pct": c.price_change_pct,
        "independent_signal_count": c.independent_signal_count,
        "active_families": list(c.active_families),
        "rvol": c.volume_anomaly.relative_volume if c.volume_anomaly else None,
        "structural_events": ([e.event_type.value for e in c.technical_anomaly.events]
                              if c.technical_anomaly else []),
        "relative_1d_pp": c.relative_strength.market_relative_1d_pp if c.relative_strength else None,
        "relative_5d_pp": c.relative_strength.market_relative_5d_pp if c.relative_strength else None,
        "relative_20d_pp": c.relative_strength.market_relative_20d_pp if c.relative_strength else None,
        "persistence": (c.relative_strength.persistence_state.value
                        if c.relative_strength and c.relative_strength.persistence_state else None),
        "direction_compatibility": c.direction_compatibility.value if c.direction_compatibility else None,
        "attention_level": c.attention_level.value if c.attention_level else None,
        "outside_top5_movers": c.instrument not in top_mover_symbols,
        "evidence": list(c.evidence),
        "why_radar_noticed_it": " ".join(c.evidence) if c.evidence else "",
    }


# ------------------------------------------------------------------ full artifact assembly
def build_validation_artifact(universe_name: str, universe: dict, results: list,
                              *, generated_at: dt.datetime | None = None,
                              network_calls: dict | None = None,
                              elapsed_s: float | None = None) -> dict:
    """Pure assembly (packet spec sections 6-20) from already-run `results` (one dict per
    session, as returned by `run_one_session`). No IO, no network, no mutation."""
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)

    per_session = [session_metrics(r) for r in results]

    all_candidate_records = []
    three_family_details = []
    symbol_occurrences: Counter = Counter()
    combo_counts: Counter = Counter()
    total_candidates = 0
    outside_top5_total = 0
    lt1_total = lt2_total = lt3_total = 0
    three_family_total = 0
    top5_overlap_total = 0

    for r in results:
        session_date = r["session_date"]
        price_changes = r["price_changes"]
        top_movers = sorted(price_changes.items(), key=lambda kv: -abs(kv[1]))[:TOP_MOVERS_N]
        top_mover_symbols = {s for s, _ in top_movers}

        for c in r["composite_snapshot"].candidates:
            rec = candidate_record(c, session_date, top_mover_symbols)
            all_candidate_records.append(rec)
            symbol_occurrences[c.instrument] += 1
            combo_counts[" + ".join(sorted(c.active_families))] += 1
            total_candidates += 1
            if rec["outside_top5_movers"]:
                outside_top5_total += 1
            else:
                top5_overlap_total += 1
            if c.price_change_pct is not None:
                if abs(c.price_change_pct) < 1.0:
                    lt1_total += 1
                if abs(c.price_change_pct) < 2.0:
                    lt2_total += 1
                if abs(c.price_change_pct) < 3.0:
                    lt3_total += 1
            if c.independent_signal_count == 3:
                three_family_total += 1
                three_family_details.append({
                    **rec,
                    "why_radar_noticed_it": rec["evidence"],
                })

    candidate_counts_per_session = [m["candidate_count"] for m in per_session]
    distribution_buckets = {"0": 0, "1-5": 0, "6-10": 0, "11-20": 0, ">20": 0}
    for n in candidate_counts_per_session:
        if n == 0:
            distribution_buckets["0"] += 1
        elif n <= 5:
            distribution_buckets["1-5"] += 1
        elif n <= 10:
            distribution_buckets["6-10"] += 1
        elif n <= 20:
            distribution_buckets["11-20"] += 1
        else:
            distribution_buckets[">20"] += 1

    direction_totals = {
        "aligned_positive": sum(m["aligned_positive"] for m in per_session),
        "aligned_negative": sum(m["aligned_negative"] for m in per_session),
        "mixed": sum(m["mixed_direction"] for m in per_session),
    }

    recurrent = sorted(symbol_occurrences.items(), key=lambda kv: (-kv[1], kv[0]))
    recurrence_buckets = {"1_time": 0, "2_times": 0, "3_plus_times": 0}
    for _sym, n in recurrent:
        if n == 1:
            recurrence_buckets["1_time"] += 1
        elif n == 2:
            recurrence_buckets["2_times"] += 1
        else:
            recurrence_buckets["3_plus_times"] += 1

    quiet_pool = [rec for rec in all_candidate_records
                 if rec["price_change_pct"] is not None and abs(rec["price_change_pct"]) < QUIET_MOVE_PCT
                 and rec["independent_signal_count"] >= QUIET_MIN_FAMILIES]
    quiet_discoveries = sorted(quiet_pool, key=lambda rec: (rec["date"], rec["symbol"]), reverse=True)[:QUIET_MAX_EXAMPLES]

    warnings = []
    for r in results:
        warnings.extend(w for w in r["dataset"].warnings if w not in warnings)
        warnings.extend(w for w in r["volume_snapshot"].warnings if w not in warnings)
        warnings.extend(w for w in r["technical_snapshot"].warnings if w not in warnings)
        warnings.extend(w for w in r["relative_snapshot"].warnings if w not in warnings)
        warnings.extend(w for w in r["composite_snapshot"].warnings if w not in warnings)

    n = len(candidate_counts_per_session)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "metadata": {
            "universe_name": universe_name, "universe_requested": len(universe),
            "sessions_requested": TARGET_SESSIONS, "sessions_analyzed": n,
            "session_dates": [m["session_date"] for m in per_session],
            "volume_percentile": "omitted for every session (history=None; RVOL-alone "
                                 "classification only) - see 'limitations' below",
            "network_calls": network_calls or {},
            "runtime_s": elapsed_s,
            "limitations": [
                "Universe constituents use today's NIFTY200 list for every historical session "
                "(no point-in-time constituent history exists in this codebase); OHLCV bars "
                "themselves are point-in-time correct per symbol/session.",
                "Volume-anomaly classification uses the RVOL-alone fallback for all 20 "
                "sessions (no percentile upgrade) - this run intentionally builds no throwaway "
                "canonical-history database, keeping market_history.db writes at exactly zero.",
                "Sector-relative performance is not scanned (no reliable stock->sector mapping "
                "exists yet, same limitation as radar.run); only market-relative evidence feeds "
                "the RELATIVE_PERFORMANCE family here, matching production's radar/run.py.",
            ],
        },
        "session_summaries": per_session,
        "aggregate_metrics": {
            "candidates_per_session": {
                "min": min(candidate_counts_per_session) if n else None,
                "max": max(candidate_counts_per_session) if n else None,
                "median": statistics.median(candidate_counts_per_session) if n else None,
                "mean": (sum(candidate_counts_per_session) / n) if n else None,
            },
            "session_distribution": distribution_buckets,
            "evidence_family_combinations": dict(combo_counts),
            "direction_totals": direction_totals,
            "total_candidates": total_candidates,
            "pct_outside_top5_movers": _pct(outside_top5_total, total_candidates),
            "pct_move_lt_1pct": _pct(lt1_total, total_candidates),
            "pct_move_lt_2pct": _pct(lt2_total, total_candidates),
            "pct_move_lt_3pct": _pct(lt3_total, total_candidates),
            "pct_three_family_high_interest": _pct(three_family_total, total_candidates),
            "top5_overlap": {"total_candidates": total_candidates, "top5_overlaps": top5_overlap_total,
                             "non_overlaps": outside_top5_total},
        },
        "candidates": all_candidate_records,
        "three_family_candidates": three_family_details,
        "quiet_discoveries": quiet_discoveries,
        "recurrent_symbols": {
            "buckets": recurrence_buckets,
            "most_recurrent": [{"symbol": s, "count": c} for s, c in recurrent[:20]],
        },
        "data_quality_warnings": warnings,
    }
    return artifact


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


# ------------------------------------------------------------------ IO
def save_artifact(artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"historical_validation_{end_date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
    return path


def render_markdown(artifact: dict) -> str:
    m, agg = artifact["metadata"], artifact["aggregate_metrics"]
    lines = [
        "# Market Intelligence Radar - 20-Session Historical Validation",
        "", f"Generated: {artifact['generated_at']}", f"Universe: {m['universe_name']} "
       f"({m['universe_requested']} requested)", f"Sessions analyzed: {m['sessions_analyzed']} "
       f"of {m['sessions_requested']} requested", "",
        "## Sessions", "", "| Date | Usable | Candidates | 2-fam | 3-fam | HIGH_INTEREST |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for s in artifact["session_summaries"]:
        lines.append(f"| {s['session_date']} | {s['usable_symbols']} | {s['candidate_count']} | "
                    f"{s['two_family_count']} | {s['three_family_count']} | {s['high_interest_count']} |")
    lines += ["", "## Candidate frequency",
             f"min {agg['candidates_per_session']['min']}, max {agg['candidates_per_session']['max']}, "
             f"median {agg['candidates_per_session']['median']}, mean "
             f"{agg['candidates_per_session']['mean']:.2f}" if agg['candidates_per_session']['mean'] is not None
             else "no sessions analyzed", "", "Distribution: " + str(agg["session_distribution"]), "",
             "## Evidence-family combinations", "", str(agg["evidence_family_combinations"]), "",
             "## Direction totals", "", str(agg["direction_totals"]), "",
             "## Non-obvious discovery", "",
             f"{agg['pct_outside_top5_movers']}% of candidates outside the top-5 absolute movers; "
             f"{agg['pct_move_lt_2pct']}% moved <2%; {agg['pct_three_family_high_interest']}% were "
             "three-family HIGH_INTEREST.", "",
             "## Recurrent symbols", "", str(artifact["recurrent_symbols"]["buckets"]),
             "Most recurrent: " + ", ".join(f"{d['symbol']}x{d['count']}"
                                            for d in artifact["recurrent_symbols"]["most_recurrent"][:10]),
             "", "## Three-family candidates", ""]
    for c in artifact["three_family_candidates"]:
        lines.append(f"- {c['date']} {c['symbol']}: {c['price_change_pct']:.2f}% "
                    f"({'; '.join(c['why_radar_noticed_it'])})" if c["price_change_pct"] is not None
                    else f"- {c['date']} {c['symbol']}: ({'; '.join(c['why_radar_noticed_it'])})")
    lines += ["", "## Quiet discoveries (|move| < 2%, >=2 families)", ""]
    for q in artifact["quiet_discoveries"]:
        lines.append(f"- {q['date']} {q['symbol']}: {q['price_change_pct']:.2f}% - "
                    f"{'; '.join(q['evidence'])}")
    lines += ["", "## Data-quality warnings", ""]
    lines += [f"- {w}" for w in artifact["data_quality_warnings"]] or ["- none"]
    return "\n".join(lines)


def save_markdown(text: str, artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"historical_validation_{end_date}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ------------------------------------------------------------------ real-IO main
def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4.2 Packet 5.3D historical Radar "
                                                 "validation - analysis only, no video/editorial.")
    parser.add_argument("--universe", default="NIFTY200")
    parser.add_argument("--sessions", type=int, default=TARGET_SESSIONS)
    args = parser.parse_args(argv)

    t_start = time.time()
    network_calls = {"universe_constituents_nse": 0, "benchmark_yahoo": 0, "universe_yahoo": 0}

    print("[1/4] Universe constituents (NSE CSV, not Yahoo)...")
    try:
        universe_full = market.get_universe(args.universe)
        network_calls["universe_constituents_nse"] = 1
    except Exception as exc:
        print(f"      constituent list unavailable ({exc}); falling back to store's own symbols")
        universe_full = {}

    store = OHLCVStore(default_db_path(OUT_DIR))
    store_symbols = {row.symbol for row in store.get_range(
        list(universe_full) if universe_full else _all_store_symbols(store),
        dt.date(2000, 1, 1), dt.date(2100, 1, 1))}
    universe = ({s: universe_full[s] for s in universe_full if s in store_symbols}
               if universe_full else {s: s for s in store_symbols})
    print(f"      universe: {len(universe)} symbols usable from local store")

    print("[2/4] Market benchmark (^NSEI, ONE Yahoo call - the canonical trading-session spine)...")
    benchmark_series_full = relative_acquisition.build_market_benchmark_series(period="1y")
    network_calls["benchmark_yahoo"] = 1
    selected, prev_dates, spine_warnings = select_session_dates(benchmark_series_full, args.sessions)
    for w in spine_warnings:
        print(f"      WARNING: {w}")
    print(f"      {len(selected)} sessions selected: {selected[0]} .. {selected[-1]}" if selected
         else "      no sessions available")

    print(f"[3/4] Running Radar detectors for {len(selected)} historical sessions "
         "(store-only, 0 Yahoo universe calls)...")
    results = []
    for session_date, prev_date in zip(selected, prev_dates):
        r = run_one_session(universe, session_date, prev_date, benchmark_series_full, store)
        results.append(r)
        print(f"      {session_date}: usable {len(r['dataset'].series_by_symbol)}, "
             f"candidates {r['composite_snapshot'].candidate_count}")

    store.close()

    elapsed = time.time() - t_start
    print("[4/4] Assembling validation artifact...")
    artifact = build_validation_artifact(args.universe, universe, results,
                                         network_calls=network_calls, elapsed_s=round(elapsed, 2))
    json_path = save_artifact(artifact)
    md_text = render_markdown(artifact)
    md_path = save_markdown(md_text, artifact)

    print(f"\n[radar.historical_validation] artifacts written:\n  {json_path}\n  {md_path}")
    print(f"[radar.historical_validation] total runtime: {elapsed:.1f}s, "
         f"network calls: {network_calls}")
    return 0


def _all_store_symbols(store: OHLCVStore) -> list:
    cur = store.conn.execute("SELECT DISTINCT symbol FROM daily_ohlcv")
    return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_universe_offline", "select_session_dates", "run_one_session",
          "session_metrics", "candidate_record", "build_validation_artifact",
          "save_artifact", "render_markdown", "save_markdown", "main"]

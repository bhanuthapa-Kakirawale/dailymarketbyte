"""Daily production Market Intelligence Radar pipeline (Phase 4.2 Packet 5.4D).

The first genuine daily entry point - everything before this packet was detection
infrastructure (Packets 1-4), historical validation (5.3D-5.3E), or policy simulation
(5.4A-5.4C) that a human or a test ran manually. `run_daily_radar` is what a real scheduled run
would call:

    Market data -> canonical trading session -> shared OHLCV dataset -> Volume/Technical/
    Relative detectors -> Composite candidates -> Novelty (vs persisted candidate history) ->
    Production editorial selector (vs persisted publication history) -> DailyRadarResult

Still explicitly NOT connected to video, upload, or `main.py`'s publication gates (see
docs/MARKET_INTELLIGENCE_RADAR.md's "detection infrastructure, not a publication gate"
boundary, unchanged by this packet) - this module's own output artifact
(`output/radar/daily_radar_<session_date>.json`) is the new canonical downstream input for
whatever presentation layer a later packet builds, not a trigger for one.

## Two separate kinds of "history", kept explicitly apart (packet spec section 9)

* **Candidate history** (`storage.candidate_history_repository.CandidateHistoryStore`) answers
  "was this symbol Radar-interesting recently" - the input `radar.novelty.classify_history`
  needs to classify TODAY's candidates. Nothing here ever substitutes editorial/publication
  history for this - they are different questions about different things.
* **Editorial (publication) history** (`storage.editorial_repository.EditorialStore`) answers
  "did we PUBLISH this symbol recently" - the input `radar.editorial_selector`'s cooldown needs.

## Canonical spine ownership (packet spec section 4)

This module fetches the market benchmark and builds the canonical trading-session spine
EXACTLY ONCE, then threads that same `spine` list into every downstream call that needs
trading-session semantics (OHLCV load, session alignment, candidate-history lookback, editorial
cooldown lookback) - no layer below this one is ever allowed to independently reconstruct its
own session list.

## No new detector/composite/novelty/selector logic

Every calculation in this module is a call into an already-validated `radar.*` component
(`radar.ohlcv_service`, `radar.session_alignment`, `radar.volume`, `radar.technical`,
`radar.relative`, `radar.composite`, `radar.novelty`, `radar.editorial_selector`). This module
is orchestration, reconstruction of persisted candidate state into the shape those components
expect, and story assembly only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field

import config
import market
from core import MarketReport, ReportType
from core.content_safety import sanitize_field
from core.sources import registry_snapshot
from storage.candidate_history_repository import CandidateHistoryStore
from storage.candidate_history_repository import default_db_path as candidate_history_db_path
from storage.candidate_history_models import StoredCandidateState
from storage.editorial_repository import EditorialStore
from storage.editorial_repository import default_db_path as editorial_db_path

from . import acquisition, composite, editorial_selector as es, ohlcv_service, relative
from . import relative_acquisition, session_alignment, technical, volume
from .models import (AttentionLevel, DirectionCompatibility, RelativePerformance,
                     RelativePersistenceState, StockRadarCandidate)
from .novelty import classify_history
from .thresholds import DEFAULT_NOVELTY_THRESHOLDS

ARTIFACT_DIR = os.path.join(config.OUT_DIR, "radar")
DAILY_PIPELINE_VERSION = "1.0"


# ------------------------------------------------------------------ result models
@dataclass
class RadarStory:
    """One final, presentation-ready intelligence story - structured data, never narration
    (packet spec section 16). `concise_reason`/`novelty_reason`/`editorial_selection_reason`
    have already passed `core.content_safety.sanitize_field` (section 18): no recommendation
    language can reach this object."""
    instrument: str
    company_name: str | None
    price_change_pct: float | None
    active_families: list
    independent_signal_count: int
    volume_context: dict | None
    technical_context: dict | None
    relative_context: dict | None
    novelty_type: str
    novelty_reason: str
    direction: str | None
    attention_level: str | None
    editorial_selection_reason: str
    reserved_3family: bool
    diversity_role: str
    supporting_session_dates: list
    concise_reason: str
    provenance: dict
    selection_id: str

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "company_name": self.company_name,
            "price_change_pct": self.price_change_pct, "active_families": list(self.active_families),
            "independent_signal_count": self.independent_signal_count,
            "volume_context": self.volume_context, "technical_context": self.technical_context,
            "relative_context": self.relative_context, "novelty_type": self.novelty_type,
            "novelty_reason": self.novelty_reason, "direction": self.direction,
            "attention_level": self.attention_level,
            "editorial_selection_reason": self.editorial_selection_reason,
            "reserved_3family": self.reserved_3family, "diversity_role": self.diversity_role,
            "supporting_session_dates": list(self.supporting_session_dates),
            "concise_reason": self.concise_reason, "provenance": dict(self.provenance),
            "selection_id": self.selection_id,
        }


@dataclass
class DailyRadarResult:
    """Packet spec section 15. `pipeline_status`: `OK` (ran cleanly), `DEGRADED` (ran, but a
    non-critical stage fell back or partially failed - see `warnings`), or `FAILED` (a critical
    layer - canonical spine, candidate history, or editorial history - could not be established;
    `stories` is always `[]` in this case, never a guess)."""
    session_date: str
    generated_at: str
    pipeline_status: str
    dry_run: bool
    universe_requested: int
    universe_usable: int
    volume_event_count: int
    technical_event_count: int
    relative_count: int
    composite_candidate_count: int
    novel_candidate_count: int
    continuation_count: int
    editorial_selection_count: int
    stories: list = field(default_factory=list)
    coverage_diagnostics: dict = field(default_factory=dict)
    pipeline_diagnostics: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    calculation_versions: dict = field(default_factory=dict)
    selector_version: str = es.EDITORIAL_SELECTOR_VERSION
    daily_pipeline_version: str = DAILY_PIPELINE_VERSION

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date, "generated_at": self.generated_at,
            "pipeline_status": self.pipeline_status, "dry_run": self.dry_run,
            "universe_requested": self.universe_requested, "universe_usable": self.universe_usable,
            "volume_event_count": self.volume_event_count,
            "technical_event_count": self.technical_event_count,
            "relative_count": self.relative_count,
            "composite_candidate_count": self.composite_candidate_count,
            "novel_candidate_count": self.novel_candidate_count,
            "continuation_count": self.continuation_count,
            "editorial_selection_count": self.editorial_selection_count,
            "stories": [s.to_dict() for s in self.stories],
            "coverage_diagnostics": dict(self.coverage_diagnostics),
            "pipeline_diagnostics": dict(self.pipeline_diagnostics),
            "warnings": list(self.warnings), "calculation_versions": dict(self.calculation_versions),
            "selector_version": self.selector_version,
            "daily_pipeline_version": self.daily_pipeline_version,
        }


def _failed_result(session_date, as_of: dt.datetime, reason: str, *, dry_run: bool,
                   timings: dict | None = None) -> DailyRadarResult:
    return DailyRadarResult(
        session_date=session_date.isoformat() if session_date else "unknown",
        generated_at=as_of.isoformat(), pipeline_status="FAILED", dry_run=dry_run,
        universe_requested=0, universe_usable=0, volume_event_count=0, technical_event_count=0,
        relative_count=0, composite_candidate_count=0, novel_candidate_count=0,
        continuation_count=0, editorial_selection_count=0, warnings=[reason],
        pipeline_diagnostics=timings or {})


# ------------------------------------------------------------------ candidate-history reconstruction
def _to_stored_state(candidate: StockRadarCandidate) -> StoredCandidateState:
    persistence = (candidate.relative_strength.persistence_state.value
                   if candidate.relative_strength and candidate.relative_strength.persistence_state
                   else None)
    return StoredCandidateState(
        session_date=candidate.market_date, instrument=candidate.instrument,
        active_families=tuple(candidate.active_families), reason_codes=tuple(candidate.reason_codes),
        independent_signal_count=candidate.independent_signal_count,
        direction_compatibility=(candidate.direction_compatibility.value
                                 if candidate.direction_compatibility else None),
        attention_level=candidate.attention_level.value if candidate.attention_level else None,
        persistence_state=persistence, price_change_pct=candidate.price_change_pct,
        calculation_version=candidate.calculation_version)


def _reconstruct_candidate(stored: StoredCandidateState) -> StockRadarCandidate:
    """A `StockRadarCandidate`-shaped object carrying ONLY the fields `radar.novelty` reads
    (`active_families`, `reason_codes`, `independent_signal_count`, `direction_compatibility`,
    `attention_level`, `relative_strength.persistence_state`) - never a claim that this is a
    complete/re-fetched candidate. `volume_anomaly`/`technical_anomaly` stay `None`, which is
    honest: this packet never persisted them and never re-derives them from anything."""
    direction = DirectionCompatibility(stored.direction_compatibility) if stored.direction_compatibility else None
    attention = AttentionLevel(stored.attention_level) if stored.attention_level else None
    relative_strength = None
    if stored.persistence_state:
        relative_strength = RelativePerformance(
            instrument=stored.instrument, session_date=stored.session_date,
            stock_return_1d=None, stock_return_5d=None, stock_return_20d=None,
            market_return_1d=None, market_return_5d=None, market_return_20d=None,
            market_relative_1d_pp=None, market_relative_5d_pp=None, market_relative_20d_pp=None,
            sector=None, sector_return_1d=None, sector_return_5d=None, sector_return_20d=None,
            sector_relative_1d_pp=None, sector_relative_5d_pp=None, sector_relative_20d_pp=None,
            persistence_state=RelativePersistenceState(stored.persistence_state),
            relative_shift_pp=None)
    return StockRadarCandidate(
        instrument=stored.instrument, market_date=stored.session_date,
        active_families=list(stored.active_families), reason_codes=list(stored.reason_codes),
        independent_signal_count=stored.independent_signal_count,
        direction_compatibility=direction, attention_level=attention,
        relative_strength=relative_strength, price_change_pct=stored.price_change_pct)


# ------------------------------------------------------------------ story assembly
def _build_story(candidate: StockRadarCandidate, novelty, selection, universe: dict) -> RadarStory:
    raw_concise = " ".join(candidate.evidence) if candidate.evidence else novelty.reason
    concise_reason, _ = sanitize_field(raw_concise, fallback="Notable Radar development.")
    novelty_reason, _ = sanitize_field(novelty.reason, fallback="")
    selection_reason, _ = sanitize_field(selection.selection_reason, fallback="")

    volume_context = None
    if candidate.volume_anomaly is not None:
        va = candidate.volume_anomaly
        volume_context = {"level": va.level.value if va.level else None,
                          "relative_volume": va.relative_volume, "rvol_percentile": va.rvol_percentile}
    technical_context = None
    if candidate.technical_anomaly is not None:
        technical_context = {"events": [e.event_type.value for e in candidate.technical_anomaly.events]}
    relative_context = None
    if candidate.relative_strength is not None:
        rs = candidate.relative_strength
        relative_context = {
            "persistence_state": rs.persistence_state.value if rs.persistence_state else None,
            "market_relative_5d_pp": rs.market_relative_5d_pp,
            "market_relative_20d_pp": rs.market_relative_20d_pp,
        }

    provenance = {
        "candidate_calculation_version": candidate.calculation_version,
        "volume_calculation_version": candidate.volume_anomaly.calculation_version if candidate.volume_anomaly else None,
        "technical_calculation_version": candidate.technical_anomaly.calculation_version if candidate.technical_anomaly else None,
        "relative_calculation_version": candidate.relative_strength.calculation_version if candidate.relative_strength else None,
        "novelty_calculation_version": novelty.calculation_version,
        "selector_version": selection.selector_version,
        "source": "yahoo",
        "supporting_fact_ids": list(candidate.volume_anomaly.supporting_fact_ids) if candidate.volume_anomaly else [],
    }

    return RadarStory(
        instrument=candidate.instrument, company_name=universe.get(candidate.instrument),
        price_change_pct=candidate.price_change_pct, active_families=list(candidate.active_families),
        independent_signal_count=candidate.independent_signal_count,
        volume_context=volume_context, technical_context=technical_context,
        relative_context=relative_context, novelty_type=novelty.novelty_type.value,
        novelty_reason=novelty_reason, direction=selection.direction_compatibility,
        attention_level=selection.attention_level, editorial_selection_reason=selection_reason,
        reserved_3family=selection.reserved_3family, diversity_role=selection.diversity_role,
        supporting_session_dates=[d.isoformat() for d in candidate.supporting_session_dates],
        concise_reason=concise_reason, provenance=provenance, selection_id=selection.selection_id)


# ------------------------------------------------------------------ artifact IO
def save_artifact(result: DailyRadarResult, out_dir: str = ARTIFACT_DIR) -> str:
    """One deterministic path per session (packet spec section 20/21) - a rerun overwrites the
    SAME file with logically identical content rather than versioning a new one."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"daily_radar_{result.session_date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    return path


# ------------------------------------------------------------------ production entry point
def run_daily_radar(session_date: dt.date | None = None, *, universe_name: str = "NIFTY200",
                    out_dir: str | None = None, dry_run: bool = False,
                    as_of: dt.datetime | None = None) -> DailyRadarResult:
    """The daily production entry point (packet spec section 2). `session_date=None` resolves
    to the latest session the canonical spine itself confirms traded; an explicit date (e.g. for
    reproducing a known historical session) is validated against that same spine rather than
    trusted blindly.

    `dry_run=True` runs every stage (data through editorial selection) but persists NEITHER
    today's candidate-history row NOR today's editorial selections, and does not write the
    session artifact file - prior history is still READ normally (packet spec section 26).
    """
    out_dir = out_dir or config.OUT_DIR
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    t_start = time.time()
    timings: dict = {}
    warnings: list = []
    network_calls = {"universe_constituents_nse": 0, "benchmark_yahoo": 0, "universe_yahoo": 0}

    # ---- 1+2: benchmark + canonical spine (authoritative owner - built exactly once) ----
    t0 = time.time()
    try:
        benchmark_series_full = relative_acquisition.build_market_benchmark_series(period="1y")
        network_calls["benchmark_yahoo"] = 1
    except Exception as exc:
        return _failed_result(session_date, as_of, f"benchmark/spine acquisition failed: {exc}",
                              dry_run=dry_run)
    spine = sorted({r["date"] for r in benchmark_series_full if r.get("date") is not None})
    timings["benchmark_ms"] = round((time.time() - t0) * 1000, 1)

    t0 = time.time()
    if not spine:
        return _failed_result(session_date, as_of, "canonical trading-session spine is empty "
                              "(benchmark unavailable)", dry_run=dry_run, timings=timings)
    if session_date is None:
        session_date = spine[-1]
    elif session_date not in spine:
        return _failed_result(session_date, as_of,
                              f"{session_date} is not a session on the canonical spine",
                              dry_run=dry_run, timings=timings)
    idx = spine.index(session_date)
    if idx == 0:
        return _failed_result(session_date, as_of,
                              "no prior spine session available to determine prev_date",
                              dry_run=dry_run, timings=timings)
    prev_date = spine[idx - 1]
    timings["session_resolution_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- universe ----
    try:
        universe = market.get_universe(universe_name)
        network_calls["universe_constituents_nse"] = 1
    except Exception as exc:
        warnings.append(f"universe constituent list unavailable ({exc}); proceeding with an "
                        "empty universe rather than a guessed one")
        universe = {}

    # ---- 3: shared OHLCV dataset (production loader - Yahoo fallback allowed, spine-aware) ----
    t0 = time.time()
    dataset = ohlcv_service.load_universe(universe, session_date, prev_date, spine=spine,
                                          out_dir=out_dir)
    network_calls["universe_yahoo"] = dataset.coverage.network_call_count
    timings["ohlcv_load_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- 4: canonical session alignment (defense-in-depth safety net for the Yahoo-fallback path) ----
    t0 = time.time()
    alignment_report = session_alignment.align_dataset_to_spine(dataset, benchmark_series_full)
    timings["alignment_ms"] = round((time.time() - t0) * 1000, 1)
    series_by_symbol = dataset.series_by_symbol

    # ---- 5: volume detector (ephemeral RVOL report - never persisted to market_history.db;
    # RADAR_SCAN accumulation, if wanted, remains radar.run.py's separate concern) ----
    t0 = time.time()
    facts, vol_skip = acquisition.build_universe_relative_volume_facts(
        universe, session_date, prev_date, retrieved_at=as_of, dataset=dataset, now=as_of)
    ephemeral_report = MarketReport(
        report_date=session_date, report_type=ReportType.RADAR_SCAN, session_date=session_date,
        generated_at=as_of, facts=facts,
        sources=registry_snapshot(f.metadata.get("published_value_source", "") for f in facts),
        metadata={"radar_scan": True, "daily_pipeline": True,
                 "notes": "Phase 4.2 Packet 5.4D daily production scan - never persisted, "
                          "never read by video.py/editorial/, not canonical history."})
    volume_snapshot = volume.scan_universe(ephemeral_report, None, list(universe),
                                           acquisition_skip_reasons=vol_skip)
    timings["volume_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- 6: technical detector ----
    t0 = time.time()
    technical_snapshot = technical.scan_technical_universe(
        list(universe), series_by_symbol, session_date, skip_reasons=dataset.skipped_symbols)
    timings["technical_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- 7: relative-performance detector ----
    t0 = time.time()
    relative_snapshot = relative.scan_relative_performance_universe(
        list(universe), series_by_symbol, benchmark_series_full, session_date,
        skip_reasons=dataset.skipped_symbols)
    timings["relative_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- 8: composite candidates ----
    t0 = time.time()
    composite_snapshot = composite.build_candidates(volume_snapshot, technical_snapshot,
                                                     relative_snapshot, session_date, as_of=as_of)
    timings["composite_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- 9-11: novelty, from PERSISTED CANDIDATE HISTORY (never editorial history) ----
    t0 = time.time()
    try:
        candidate_store = CandidateHistoryStore(candidate_history_db_path(out_dir))
        prior_rows = candidate_store.get_prior_candidates(
            session_date, spine, DEFAULT_NOVELTY_THRESHOLDS.lookback_sessions)
    except Exception as exc:
        return _failed_result(session_date, as_of,
                              f"candidate-history unavailable ({exc}); refusing to classify "
                              "novelty against an unknown/assumed-empty history",
                              dry_run=dry_run, timings=timings)

    prior_sessions_for_novelty = [(d, [_reconstruct_candidate(r) for r in rows]) for d, rows in prior_rows]
    # Ordering (packet spec section 12): today's own candidates are appended LAST, so
    # `classify_history` can only ever compare session_date against sessions strictly before it -
    # persistence of today's own state happens further below, AFTER this call.
    sessions_for_novelty = prior_sessions_for_novelty + [(session_date, composite_snapshot.candidates)]
    novelty_by_date = classify_history(sessions_for_novelty)
    today_novelty = novelty_by_date[session_date]
    timings["novelty_ms"] = round((time.time() - t0) * 1000, 1)

    if not dry_run:
        try:
            candidate_store.save_candidates(
                session_date, [_to_stored_state(c) for c in composite_snapshot.candidates])
        except Exception as exc:
            warnings.append(f"candidate-history persistence failed ({exc}); today's Radar "
                            "state will not be visible to a future session's novelty calculation")
    candidate_store.close()

    # ---- 12-15: production editorial selector, from PERSISTED EDITORIAL HISTORY ----
    t0 = time.time()
    try:
        editorial_store = EditorialStore(editorial_db_path(out_dir))
        recently_selected = editorial_store.get_prior_selections(
            session_date, spine, es.PUBLICATION_COOLDOWN_SESSIONS)
    except Exception as exc:
        return _failed_result(session_date, as_of,
                              f"editorial publication history unavailable ({exc}); refusing to "
                              "select to avoid risking repeat publication",
                              dry_run=dry_run, timings=timings)

    pairs = list(zip(composite_snapshot.candidates, today_novelty))
    selection_result = es.select_session(session_date, pairs, recently_selected, as_of=as_of)

    if not dry_run:
        try:
            editorial_store.save_selections(selection_result.selected)
        except Exception as exc:
            warnings.append(f"editorial selection persistence failed ({exc}); cooldown state "
                            "for this session will not be visible to future runs")
    editorial_store.close()
    timings["editorial_ms"] = round((time.time() - t0) * 1000, 1)

    # ---- 16: assemble stories + final artifact ----
    candidates_by_symbol = {c.instrument: c for c in composite_snapshot.candidates}
    novelty_by_symbol = {n.instrument: n for n in today_novelty}
    stories = [
        _build_story(candidates_by_symbol[sel.instrument], novelty_by_symbol[sel.instrument],
                    sel, universe)
        for sel in selection_result.selected]

    novel_count = sum(1 for n in today_novelty if n.novelty_type.value != "CONTINUATION")
    continuation_count = len(today_novelty) - novel_count

    coverage = {
        "requested_symbols": len(dataset.requested_symbols), "usable_symbols": len(dataset.series_by_symbol),
        "skipped_symbols": len(dataset.skipped_symbols),
        "cache_complete": dataset.coverage.cache_complete, "cache_partial": dataset.coverage.cache_partial,
        "cache_miss": dataset.coverage.cache_miss,
        "alignment_symbols_filtered": alignment_report.symbols_filtered,
        "alignment_rows_dropped": alignment_report.rows_dropped,
    }
    timings["total_ms"] = round((time.time() - t_start) * 1000, 1)

    warnings = warnings + list(dataset.warnings) + list(volume_snapshot.warnings) + \
        list(technical_snapshot.warnings) + list(relative_snapshot.warnings) + \
        list(composite_snapshot.warnings) + list(selection_result.warnings)

    status = "DEGRADED" if (selection_result.degraded or not universe or warnings) else "OK"

    result = DailyRadarResult(
        session_date=session_date.isoformat(), generated_at=as_of.isoformat(),
        pipeline_status=status, dry_run=dry_run,
        universe_requested=len(dataset.requested_symbols), universe_usable=len(dataset.series_by_symbol),
        volume_event_count=len(volume_snapshot.anomalies), technical_event_count=len(technical_snapshot.flagged),
        relative_count=len(relative_snapshot.scanned), composite_candidate_count=composite_snapshot.candidate_count,
        novel_candidate_count=novel_count, continuation_count=continuation_count,
        editorial_selection_count=len(selection_result.selected), stories=stories,
        coverage_diagnostics=coverage,
        pipeline_diagnostics={"stage_timings_ms": timings, "network_calls": network_calls,
                              "editorial_diagnostics": selection_result.diagnostics},
        warnings=warnings,
        calculation_versions={"composite": composite_snapshot.calculation_version,
                              "volume": "1.0", "technical": "1.0", "relative": "1.0"},
        selector_version=selection_result.selector_version)

    if not dry_run:
        save_artifact(result, os.path.join(out_dir, "radar"))

    return result


__all__ = ["RadarStory", "DailyRadarResult", "run_daily_radar", "save_artifact",
          "DAILY_PIPELINE_VERSION"]

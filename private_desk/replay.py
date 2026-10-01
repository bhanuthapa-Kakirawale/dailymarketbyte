"""Detector replay: the Radar's OWN detector values for one session, recomputed read-only from
the stored OHLCV - so the desk can show the actual numbers behind every candidate.

Why this exists: `radar_candidate_history.db` keeps each candidate's families and reason codes,
but not the values behind them (RVOL, the prior-20 high it broke, the 5/20-session relative pp),
and the daily JSON keeps values only for the <= 5 editorially selected stories.

How it stays honest:

* Same detectors, same orchestration. The steps mirror `radar.daily_pipeline._run_detectors`
  call for call (`session_alignment.align_dataset_to_spine`, `acquisition.
  build_universe_relative_volume_facts(dataset=...)`, `volume.scan_universe`, `technical.
  scan_technical_universe`, `relative.scan_relative_performance_universe`, `composite.
  build_candidates`). No threshold, formula or rule is re-implemented here.
* Store only, never a fetch. The one difference from the pipeline: the dataset is built from
  the read-only store (`DeskRepository.ohlcv_rows`) instead of `ohlcv_service.load_universe`,
  which tops up missing symbols from Yahoo. A symbol the store cannot cover is skipped with the
  pipeline's own reason (`ohlcv_service._finalize_symbol`), never fetched.
* Reconciled, never trusted blindly. `reconcile()` compares the replayed candidate set and
  reason codes with what the evening run actually recorded. Only a MATCHED candidate's values
  are shown as its detector evidence; a mismatch is shown as a mismatch (the stored OHLCV may
  have been repaired since, or the universe may differ), never papered over.

The result is a plain JSON-able dict, cached under `private_desk/cache/` keyed by a fingerprint
of the inputs (the OHLCV db + the session's artifacts), so it is recomputed only when they change.
"""
from __future__ import annotations

import datetime as dt

from config import IST

from .cache import DeskCache, fingerprint
from .repository import DeskRepository

REPLAY_VERSION = "1.0"

MATCHED = "MATCHED"
REASON_CODES_DIFFER = "REASON_CODES_DIFFER"
NOT_REPLAYED = "NOT_REPLAYED"          # recorded by the evening run, absent from the replay
REPLAY_ONLY = "REPLAY_ONLY"            # replay produced it, the evening run did not record it


class ReplayUnavailable(Exception):
    pass


def _as_of(artifact: dict | None, session: dt.date) -> dt.datetime:
    """The replay's evaluation instant: the original run's own `generated_at` when known, else
    the session's evening (22:00 IST) - never "now", which would age a past session's facts."""
    if artifact and artifact.get("generated_at"):
        try:
            return dt.datetime.fromisoformat(artifact["generated_at"])
        except ValueError:
            pass
    return dt.datetime.combine(session, dt.time(22, 0), tzinfo=IST)


def _build_dataset(repo: DeskRepository, universe: dict, session: dt.date, prev_date: dt.date,
                   spine: list):
    from radar import ohlcv_service
    from storage.ohlcv_models import QualityStatus

    dataset = ohlcv_service.UniverseOHLCVDataset(session_date=session,
                                                 requested_symbols=list(universe), source="yahoo")
    dataset.coverage.requested_symbols = len(universe)
    start = session - dt.timedelta(days=ohlcv_service.STORE_QUERY_WINDOW_DAYS)
    by_symbol: dict = {}
    for r in repo.ohlcv_rows(universe, start, session):
        if r.quality_status == QualityStatus.OK:
            by_symbol.setdefault(r.symbol, []).append(r)
    dropped = ohlcv_service.apply_session_spine(by_symbol, frozenset(spine))
    if dropped:
        dataset.warnings.append(f"session spine filter: {dropped} stored row(s) for confirmed "
                                "non-trading dates excluded")
    for symbol in universe:
        ok_rows = sorted(by_symbol.get(symbol, []), key=lambda r: r.session_date)
        ohlcv_service._finalize_symbol(dataset, symbol, ok_rows, session, prev_date)
    dataset.available_symbols = sorted(dataset.series_by_symbol)
    dataset.coverage.usable_symbols = len(dataset.series_by_symbol)
    dataset.coverage.skipped_symbols = len(dataset.skipped_symbols)
    return dataset


def _spine(benchmark: list, session: dt.date) -> list:
    from radar import session_alignment
    return session_alignment.canonical_session_list(benchmark, end=session)


def run_detectors(repo: DeskRepository, session: dt.date, constituents: dict | None = None) -> dict:
    """The detector chain for `session` from stored data (see module docstring). Returns the
    live objects; `compute_replay` turns them into the cached JSON. `constituents` overrides
    the universe lookup (used by tests that build the Market Structure artifact from it).
    Raises `ReplayUnavailable` when benchmark, universe or session are not there."""
    from core import MarketReport, ReportType
    from core.sources import registry_snapshot
    from radar import acquisition, composite, relative, session_alignment, technical, volume

    benchmark = repo.benchmark_series(session)
    if not benchmark:
        raise ReplayUnavailable("benchmark (^NSEI) not in the OHLCV store")
    spine = _spine(benchmark, session)
    if session not in spine:
        raise ReplayUnavailable(f"{session} is not a session on the canonical spine")
    idx = spine.index(session)
    if idx == 0:
        raise ReplayUnavailable("no prior session on the spine")
    prev_date = spine[idx - 1]

    universe_session = None
    if constituents is None:
        constituents, universe_session = repo.universe(session)
    if not constituents:
        raise ReplayUnavailable("no Market Structure artifact holds the universe constituents")
    universe = {s: (c or {}).get("company") or s for s, c in constituents.items()}

    artifact, _ = repo.radar_artifact(session)
    as_of = _as_of(artifact, session)

    dataset = _build_dataset(repo, universe, session, prev_date, spine)
    session_alignment.align_dataset_to_spine(dataset, benchmark)
    series_by_symbol = dataset.series_by_symbol

    facts, vol_skip = acquisition.build_universe_relative_volume_facts(
        universe, session, prev_date, retrieved_at=as_of, dataset=dataset, now=as_of)
    report = MarketReport(
        report_date=session, report_type=ReportType.RADAR_SCAN, session_date=session,
        generated_at=as_of, facts=facts,
        sources=registry_snapshot(f.metadata.get("published_value_source", "") for f in facts),
        metadata={"radar_scan": True, "private_desk_replay": True})
    volume_snapshot = volume.scan_universe(report, None, list(universe),
                                           acquisition_skip_reasons=vol_skip)
    technical_snapshot = technical.scan_technical_universe(
        list(universe), series_by_symbol, session, skip_reasons=dataset.skipped_symbols)
    relative_snapshot = relative.scan_relative_performance_universe(
        list(universe), series_by_symbol, benchmark, session,
        skip_reasons=dataset.skipped_symbols)
    composite_snapshot = composite.build_candidates(volume_snapshot, technical_snapshot,
                                                     relative_snapshot, session, as_of=as_of)
    return {"session": session, "prev_date": prev_date, "spine": spine, "as_of": as_of,
            "universe": universe, "universe_session": universe_session, "dataset": dataset,
            "volume": volume_snapshot, "technical": technical_snapshot,
            "relative": relative_snapshot, "composite": composite_snapshot}


def compute_replay(repo: DeskRepository, session: dt.date) -> dict:
    """`run_detectors` flattened into the JSON the desk caches and displays."""
    from radar import ohlcv_service, technical
    from radar.thresholds import DEFAULT_TECHNICAL_THRESHOLDS

    run = run_detectors(repo, session)
    prev_date, as_of, universe = run["prev_date"], run["as_of"], run["universe"]
    universe_session, dataset = run["universe_session"], run["dataset"]
    volume_snapshot, relative_snapshot = run["volume"], run["relative"]
    composite_snapshot = run["composite"]
    series_by_symbol = dataset.series_by_symbol

    rvol_rows, _ = ohlcv_service.relative_volume_rows_from_dataset(dataset, universe)
    rvol = {r["symbol"]: r["volx"] for r in rvol_rows}
    anomalies = {a.instrument: a for a in volume_snapshot.anomalies}
    rel = {r.instrument: r for r in relative_snapshot.results}

    symbols = {}
    for symbol, series in series_by_symbol.items():
        cur = series[-1]
        prev = series[-2] if len(series) >= 2 else None
        r = rvol.get(symbol)
        cur_vol = cur.get("volume")
        structure = technical._build_structure(symbol, series, DEFAULT_TECHNICAL_THRESHOLDS)
        symbols[symbol] = {
            "close": cur.get("close"),
            "prev_close": prev.get("close") if prev else None,
            "prev_date": prev["date"].isoformat() if prev else None,
            "current_volume": cur_vol,
            "relative_volume": r,
            # algebraically the prior-20 mean `market.relative_volume` divided by
            "prior20_avg_volume": (cur_vol / r) if (r and cur_vol) else None,
            "volume_anomaly": anomalies[symbol].to_dict() if symbol in anomalies else None,
            "technical": structure.to_dict(),
            "relative": rel[symbol].to_dict() if symbol in rel else None,
        }

    candidates = {}
    for c in composite_snapshot.candidates:
        candidates[c.instrument] = {
            "reason_codes": list(c.reason_codes), "active_families": list(c.active_families),
            "evidence": list(c.evidence), "independent_signal_count": c.independent_signal_count,
            "direction_compatibility": c.direction_compatibility.value if c.direction_compatibility else None,
            "attention_level": c.attention_level.value if c.attention_level else None,
            "price_change_pct": c.price_change_pct,
        }

    return {
        "replay_version": REPLAY_VERSION, "session": session.isoformat(),
        "prev_date": prev_date.isoformat(), "as_of": as_of.isoformat(),
        "universe_source_session": universe_session.isoformat() if universe_session else None,
        "universe_size": len(universe),
        "usable_symbols": len(series_by_symbol),
        "skipped_symbols": dict(sorted(dataset.skipped_symbols.items())),
        "volume_skipped": len(volume_snapshot.skipped),
        "warnings": list(dataset.warnings) + list(composite_snapshot.warnings),
        "calculation_version": composite_snapshot.calculation_version,
        "symbols": symbols, "candidates": candidates,
    }


def reconcile(replay: dict | None, stored: list) -> dict:
    """`{symbol: status}` for the union of recorded and replayed candidates."""
    out = {}
    replayed = (replay or {}).get("candidates") or {}
    for c in stored:
        r = replayed.get(c.instrument)
        if r is None:
            out[c.instrument] = NOT_REPLAYED
        elif sorted(r["reason_codes"]) == sorted(c.reason_codes):
            out[c.instrument] = MATCHED
        else:
            out[c.instrument] = REASON_CODES_DIFFER
    stored_syms = {c.instrument for c in stored}
    for s in replayed:
        if s not in stored_syms:
            out[s] = REPLAY_ONLY
    return out


def replay_inputs(repo: DeskRepository, session: dt.date) -> list:
    paths = [repo.db_file("ohlcv")]
    _, a = repo.radar_artifact(session)
    ms = repo.market_structure_path(session)
    for p in (a, ms):
        if p:
            paths.append(p)
    return paths


def get_replay(repo: DeskRepository, cache: DeskCache | None, session: dt.date) -> dict:
    """Cached replay. `{"status": "OK", ...}` or `{"status": "UNAVAILABLE", "reason": ...}`."""
    key = f"{REPLAY_VERSION}:{fingerprint(replay_inputs(repo, session))}"
    name = f"replay_{session.isoformat()}.json"
    if cache is not None:
        hit = cache.get(name, key)
        if hit is not None:
            return hit
    try:
        value = {"status": "OK", **compute_replay(repo, session)}
    except ReplayUnavailable as exc:
        value = {"status": "UNAVAILABLE", "reason": str(exc), "session": session.isoformat()}
    except Exception as exc:          # the desk must fail safe, never crash a page
        value = {"status": "UNAVAILABLE", "session": session.isoformat(),
                 "reason": f"replay failed: {type(exc).__name__}: {exc}"}
    if cache is not None:
        cache.put(name, key, value)
    return value


__all__ = ["get_replay", "compute_replay", "run_detectors", "reconcile", "MATCHED", "REASON_CODES_DIFFER",
          "NOT_REPLAYED", "REPLAY_ONLY", "ReplayUnavailable", "REPLAY_VERSION"]

"""Unusual-volume detection: is today's relative volume unusual for THIS stock, relative to
its own recent history - never "top volume" and never gated by price direction.

Two hard boundaries, mirrored from `intelligence/engine.py`:

* **No acquisition.** The only inputs are today's MarketReport and what canonical
  `MarketHistory` already holds. Nothing here calls a provider, a website or a model.
* **No mutation.** `report` and `history` are read and never written.

Relative volume itself is never recomputed here - every reading is the `STOCK_RELATIVE_VOLUME`
Fact value the pipeline already computed (`market.relative_volume`), read as-is. Only readings
carrying `definition_version` 2.0 (Phase 3's 20-prior-session definition) enter a comparison,
today's or historical - the same compatibility rule already proven in
`intelligence/movers.py::analyse_relative_volume`.
"""
from __future__ import annotations

import datetime as dt

from core import Metric

from intelligence.history import (DEFAULT_WINDOW_SESSIONS, HistoricalWindow, HistoryUnavailable,
                                  count_below)
from intelligence.models import is_eligible

from .models import VolumeAnomaly, VolumeRadarSnapshot
from .thresholds import DEFAULT_VOLUME_THRESHOLDS, VolumeThresholds, classify_anomaly

# Must match market.RELATIVE_VOLUME_DEFINITION["definition_version"] - kept as a separate
# constant (not imported) because this module never touches market.py's calculation path,
# only the Fact value the adapter already stamped with it. A cross-check test asserts equality.
RELATIVE_VOLUME_DEFINITION_VERSION = "2.0"


def scan_universe(report, history, universe: list, *,
                  scan_report=None,
                  acquisition_skip_reasons: dict | None = None,
                  window_sessions: int = DEFAULT_WINDOW_SESSIONS,
                  as_of: dt.datetime | None = None,
                  thresholds: VolumeThresholds = DEFAULT_VOLUME_THRESHOLDS,
                  highest_rvol_window: int = 20,
                  include_demo: bool = False) -> VolumeRadarSnapshot:
    """Detect unusual-volume candidates within `universe` for `report`'s session.

    `universe` is entirely caller-supplied - never defaulted to or hardcoded as a particular
    index's constituents. Read-only: never mutates `report`, `scan_report` or `history`, never
    calls `history.save_report`.

    `scan_report` is an optional second, `ReportType.RADAR_SCAN` `MarketReport` (see
    `radar/acquisition.py`) carrying universe-wide `STOCK_RELATIVE_VOLUME` facts for symbols
    that are not today's video movers. When given, its facts are unioned with `report`'s for
    "today's data" - `report`'s own facts win on any collision (defensive; acquisition-time
    `already_covered` should already keep the two disjoint). Omitting it reproduces Packet 1's
    original behaviour exactly (movers-only, from `report` alone).

    `acquisition_skip_reasons` (typically `radar.acquisition`'s bulk-acquisition skip reasons)
    lets specific reasons - "no_recap_row", "backfill_pending", "date_gap",
    "insufficient_relative_volume_history" - surface in `snapshot.skipped` instead of the
    generic message.
    """
    session_date = report.session_date or report.report_date
    universe_list = list(dict.fromkeys(universe))          # dedupe, preserve caller's order

    snapshot = VolumeRadarSnapshot(
        session_date=session_date, generated_at=as_of or dt.datetime.now(dt.timezone.utc),
        universe_size=len(universe_list), universe_source="caller-supplied universe list")

    current = _current_stock_data(report, set(universe_list), scan_report=scan_report)
    for symbol in universe_list:
        if symbol in current:
            snapshot.scanned.append(symbol)
        else:
            snapshot.skipped[symbol] = (acquisition_skip_reasons or {}).get(
                symbol, "no STOCK_RELATIVE_VOLUME fact in today's report")

    if not snapshot.scanned:
        return snapshot

    comparable_by_symbol: dict = {}
    if history is None:
        snapshot.warnings.append("no historical database available; "
                                 "classification limited to today's reading, percentile omitted")
    else:
        window = HistoricalWindow(history, session_date, report_id=report.report_id,
                                  window_sessions=window_sessions, include_demo=include_demo)
        try:
            points = window.points(Metric.STOCK_RELATIVE_VOLUME)
            comparable_by_symbol = _comparable_points_by_instrument(
                points, RELATIVE_VOLUME_DEFINITION_VERSION)
        except HistoryUnavailable as exc:
            snapshot.warnings.append(f"historical database unreadable: {exc}")
        snapshot.warnings.extend(w for w in window.warnings if w not in snapshot.warnings)

    anomalies = []
    for symbol in snapshot.scanned:
        anomaly = _build_anomaly(symbol, current[symbol], comparable_by_symbol.get(symbol, []),
                                 session_date, thresholds, highest_rvol_window, snapshot)
        if anomaly is not None:
            anomalies.append(anomaly)

    anomalies.sort(key=lambda a: (-(a.relative_volume or 0.0), a.instrument))
    snapshot.anomalies = anomalies
    return snapshot


def _build_anomaly(symbol, data, comparable, session_date, thresholds, highest_rvol_window,
                   snapshot) -> VolumeAnomaly | None:
    """One instrument's classified reading, or `None` if it is not (or cannot be) flagged.

    Returning `None` leaves the symbol recorded in `snapshot.scanned` but out of
    `snapshot.anomalies` - it was considered and found unremarkable, not skipped.
    """
    relative_volume = data["relative_volume"]
    if data["definition_version"] != RELATIVE_VOLUME_DEFINITION_VERSION:
        _warn(snapshot, f"{symbol}: today's relative-volume reading uses an older definition "
                        f"than {RELATIVE_VOLUME_DEFINITION_VERSION} and cannot be classified")
        return None

    values = [p.value for p in comparable]
    prior_sessions_available = len(values)
    rvol_historical_rank = count_below(values, relative_volume) if values else None

    rvol_percentile = None
    warnings = []
    if prior_sessions_available >= thresholds.min_percentile_sample:
        rvol_percentile = (rvol_historical_rank / prior_sessions_available) * 100
    elif prior_sessions_available:
        warnings.append(f"percentile omitted: only {prior_sessions_available} comparable "
                        f"sessions, need {thresholds.min_percentile_sample}")
    else:
        warnings.append("percentile omitted: no comparable relative-volume history yet")

    level = classify_anomaly(relative_volume, rvol_percentile, prior_sessions_available, thresholds)
    if level is None:
        return None                     # not anomalous - stays scanned, never enters anomalies

    highest = _highest_in_n_sessions(comparable, highest_rvol_window)
    price_change_pct = data.get("price_change_pct")

    data_quality = ["definition_version_2.0_confirmed",
                    "raw_volume_unavailable: only the relative-volume ratio is persisted "
                    "by the current pipeline"]
    if prior_sessions_available >= thresholds.min_percentile_sample:
        data_quality.append("full_percentile_window")

    return VolumeAnomaly(
        instrument=symbol, market_date=session_date, current_volume=None, average_volume_20=None,
        relative_volume=relative_volume, prior_sessions_available=prior_sessions_available,
        rvol_percentile=rvol_percentile, rvol_historical_rank=rvol_historical_rank,
        highest_rvol_in_n_sessions=highest, price_change_pct=price_change_pct, level=level,
        why_flagged=_why_flagged(symbol, relative_volume, level, rvol_historical_rank,
                                 prior_sessions_available, rvol_percentile, price_change_pct,
                                 thresholds),
        supporting_fact_ids=[data["fact_id"]] + [p.fact_id for p in comparable],
        supporting_report_ids=[p.report_id for p in comparable],
        definition_version=RELATIVE_VOLUME_DEFINITION_VERSION,
        data_quality=data_quality, warnings=warnings)


def _why_flagged(symbol, relative_volume, level, rank, sample, percentile, price_change_pct,
                 thresholds) -> str:
    text = (f"{symbol}'s relative volume of {relative_volume:.2f}x is {level.value.lower()}: "
           f"at least {thresholds.elevated_rvol:g}x its 20-session average volume")
    if percentile is not None:
        text += (f", higher than {rank} of its previous {sample} comparable readings "
                f"({percentile:.0f}th percentile)")
    text += "."
    if price_change_pct is not None:
        text += (f" Price moved {price_change_pct:+.2f}% today (context only, not part of "
                f"this classification).")
    return text


def _highest_in_n_sessions(comparable: list, n_requested: int) -> dict | None:
    """Highest comparable RVOL reading in the most recent `n_requested` sessions available.

    Wording rule enforced by the caller of this data, never by this function: when
    `is_complete_window` is False, prose must say "in its N recorded sessions", never "N
    trading days" - the window is not what was requested, only what is actually known.
    """
    if not comparable:
        return None
    window_slice = comparable[:n_requested]
    best = max(window_slice, key=lambda p: p.value)
    return {"value": best.value, "session_date": best.market_date, "fact_id": best.fact_id,
           "n_requested": n_requested, "n_recorded": len(window_slice),
           "is_complete_window": len(window_slice) >= n_requested}


def _comparable_points_by_instrument(points: list, definition_version: str) -> dict:
    """Collapse to canonical (report_id, fact_id) identity, keep only facts where ANY
    observation carries `definition_version`, group by instrument, newest session first.

    Mirrors `intelligence/movers.py::analyse_relative_volume`'s dedup-then-compatibility-gate
    logic (kept as a separate, radar-owned copy since that step isn't exported as a public
    helper from `intelligence.movers`) - counting observations instead of canonical facts
    would inflate the sample and the rank, making a reading look better corroborated than it
    is.
    """
    by_fact: dict = {}
    for point in points:
        key = (point.report_id, point.fact_id)
        compatible = (point.observation_metadata or {}).get("definition_version") == definition_version
        entry = by_fact.get(key)
        if entry is None:
            by_fact[key] = {"point": point, "compatible": compatible}
        elif compatible:
            entry["compatible"] = True

    grouped: dict = {}
    for entry in by_fact.values():
        if not entry["compatible"]:
            continue
        point = entry["point"]
        grouped.setdefault(point.instrument, []).append(point)
    return grouped


def _current_stock_data(report, universe: set, scan_report=None) -> dict:
    """Today's relative volume (+ price, for context) per universe instrument.

    Sourced from `report`'s own STOCK_RELATIVE_VOLUME facts (today's movers) and, when
    `scan_report` is given, unioned with its universe-wide facts (radar/acquisition.py) -
    `report`'s facts win on any collision. Price context (STOCK_CHANGE_PCT) always comes from
    `report` alone: only movers carry it, and `scan_report` never emits it by design (see
    `adapters/market_adapter.py::observe_universe_relative_volume`)."""
    out = _relative_volume_facts(report, universe)
    if scan_report is not None:
        for symbol, data in _relative_volume_facts(scan_report, universe).items():
            out.setdefault(symbol, data)

    for fact in report.facts_for(Metric.STOCK_CHANGE_PCT):
        if fact.instrument in out and fact.value is not None and is_eligible(fact.validation_status):
            out[fact.instrument]["price_change_pct"] = float(fact.value)

    return out


def _relative_volume_facts(source_report, universe: set) -> dict:
    out = {}
    for fact in source_report.facts_for(Metric.STOCK_RELATIVE_VOLUME):
        if fact.instrument not in universe or fact.value is None or not is_eligible(fact.validation_status):
            continue
        versions = {(o.metadata or {}).get("definition_version") for o in fact.observations}
        out[fact.instrument] = {
            "relative_volume": float(fact.value), "fact_id": fact.fact_id,
            "definition_version": (RELATIVE_VOLUME_DEFINITION_VERSION
                                   if RELATIVE_VOLUME_DEFINITION_VERSION in versions else None),
            "price_change_pct": None,
        }
    return out


def _warn(snapshot, message: str) -> None:
    if message not in snapshot.warnings:
        snapshot.warnings.append(message)


__all__ = ["scan_universe", "RELATIVE_VOLUME_DEFINITION_VERSION"]

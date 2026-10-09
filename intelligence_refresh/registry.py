"""The capability registry: for each piece of Private Desk intelligence, whether a missed
historical session can honestly be reconstructed, and if so with which existing, unmodified
engine. Pure data - no I/O, no imports of the engines it names.

Classifications (see docs/INTELLIGENCE_REFRESH.md for the reasoning behind each one):

RECONSTRUCTABLE
    Price-derived; a genuine historical-reconstruction route exists via an existing,
    unmodified tool.
LATEST_SESSION_ONLY
    The underlying source only ever exposes "fetch latest/current" - no historical-date
    parameter exists anywhere in its acquisition layer. The only lever available is catching
    up the CURRENT latest session if it hasn't been captured yet; a missed session is
    `HISTORICAL_UNAVAILABLE` forever after its capture window closes.
UNSUPPORTED_HISTORICALLY
    No existing tool reconstructs this for a past session, by design (the tool that reuses
    the same calculation path deliberately never writes it for a non-live session). This
    project does not invent one.
NOT_APPLICABLE
    Nothing to refresh - already derived lazily, fresh, on every read.
"""
from __future__ import annotations

from dataclasses import dataclass

RECONSTRUCTABLE = "RECONSTRUCTABLE"
LATEST_SESSION_ONLY = "LATEST_SESSION_ONLY"
UNSUPPORTED_HISTORICALLY = "UNSUPPORTED_HISTORICALLY"
NOT_APPLICABLE = "NOT_APPLICABLE"

# Per-session component statuses (coverage.py / manifest session_results use these).
PRESENT = "ALREADY_PRESENT"
MISSING = "MISSING"
BACKFILLED = "BACKFILLED"
FAILED = "FAILED"
WOULD_BACKFILL = "WOULD_BACKFILL"
HISTORICAL_UNAVAILABLE = "HISTORICAL_UNAVAILABLE"


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    backfill_mode: str
    requires_network: bool
    engine_ref: str
    repair_fn: str | None   # dotted "module:function", or None if no repair path exists


COMPONENTS: dict[str, ComponentSpec] = {
    "ohlcv_benchmark": ComponentSpec(
        key="ohlcv_benchmark", backfill_mode=RECONSTRUCTABLE, requires_network=True,
        engine_ref="radar.ohlcv_service.load_universe, radar.relative_acquisition."
                   "build_market_benchmark_series",
        repair_fn="intelligence_refresh.price_chain_backfill:fetch_benchmark_context"),
    "market_structure": ComponentSpec(
        key="market_structure", backfill_mode=RECONSTRUCTABLE, requires_network=True,
        engine_ref="market_structure.build.build_for_session",
        repair_fn="intelligence_refresh.price_chain_backfill:backfill_market_structure"),
    "radar_candidate_history": ComponentSpec(
        key="radar_candidate_history", backfill_mode=RECONSTRUCTABLE, requires_network=True,
        engine_ref="radar.candidate_history_backfill.backfill_candidate_history",
        repair_fn="intelligence_refresh.price_chain_backfill:backfill_radar_candidates"),
    "radar_editorial_selections": ComponentSpec(
        key="radar_editorial_selections", backfill_mode=UNSUPPORTED_HISTORICALLY,
        requires_network=False,
        engine_ref="radar.daily_pipeline.run_daily_radar (live-only; radar.candidate_history_"
                   "backfill deliberately never writes editorial selections for a backfilled "
                   "session)",
        repair_fn=None),
    "institutional_flow": ComponentSpec(
        key="institutional_flow", backfill_mode=LATEST_SESSION_ONLY, requires_network=False,
        engine_ref="products.report_job.capture_institutional", repair_fn=None),
    "market_events": ComponentSpec(
        key="market_events", backfill_mode=LATEST_SESSION_ONLY, requires_network=False,
        engine_ref="products.report_job.capture_market_events", repair_fn=None),
    "official_snapshots": ComponentSpec(
        key="official_snapshots", backfill_mode=LATEST_SESSION_ONLY, requires_network=False,
        engine_ref="products.report_job.capture_official", repair_fn=None),
    "canonical_report": ComponentSpec(
        key="canonical_report", backfill_mode=LATEST_SESSION_ONLY, requires_network=False,
        engine_ref="main.produce_report via products.report_job.run_report_job",
        repair_fn=None),
    "intelligence_snapshot": ComponentSpec(
        key="intelligence_snapshot", backfill_mode=LATEST_SESSION_ONLY, requires_network=False,
        engine_ref="main.build_intelligence", repair_fn=None),
    "market_regime": ComponentSpec(
        key="market_regime", backfill_mode=NOT_APPLICABLE, requires_network=False,
        engine_ref="private_desk.regime.RegimeStore (lazy, derived on every page view)",
        repair_fn=None),
}

# The only components a historical (non-latest) session can ever genuinely repair.
PRICE_CHAIN_KEYS = ("ohlcv_benchmark", "market_structure", "radar_candidate_history")

# Reached only via the latest-session catch-up step; never attempted for a historical session.
LATEST_ONLY_KEYS = tuple(k for k, c in COMPONENTS.items() if c.backfill_mode == LATEST_SESSION_ONLY)

# No repair path exists at all; always reported as-is.
STATIC_KEYS = tuple(k for k, c in COMPONENTS.items()
                    if c.backfill_mode in (UNSUPPORTED_HISTORICALLY, NOT_APPLICABLE))

ALL_KEYS = tuple(COMPONENTS.keys())

__all__ = ["ComponentSpec", "COMPONENTS", "PRICE_CHAIN_KEYS", "LATEST_ONLY_KEYS", "STATIC_KEYS",
          "ALL_KEYS", "RECONSTRUCTABLE", "LATEST_SESSION_ONLY", "UNSUPPORTED_HISTORICALLY",
          "NOT_APPLICABLE", "PRESENT", "MISSING", "BACKFILLED", "FAILED", "WOULD_BACKFILL",
          "HISTORICAL_UNAVAILABLE"]

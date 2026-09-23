"""Universe-wide RVOL acquisition (Phase 4.2 Packet 1.1).

Packet 1's `radar/volume.py::scan_universe` can only classify a stock that already has a
`STOCK_RELATIVE_VOLUME` canonical Fact, and until this module existed the only place that Fact
was ever created was `adapters/market_adapter.py::observe_movers` - scoped to the day's ~10
video movers. This module gives the Radar a wider acquisition path, independent of editorial
mover selection, WITHOUT touching the publication-facing `MarketReport` the video reads.

Two hard boundaries, matching the rest of this pipeline's canonical-history discipline:

* **RVOL only.** `observe_universe_relative_volume` never emits STOCK_CLOSE/STOCK_CHANGE_PCT -
  see its docstring for why (`intelligence/movers.py::analyse_recurrence` would silently
  misclassify hundreds of ordinary stocks as frequent movers otherwise).
* **A distinct report, not the day's publication report.** The scan is persisted through the
  SAME `MarketHistory.save_report` every other canonical report uses, under
  `ReportType.RADAR_SCAN`, which yields a `report_id` distinct from that day's `PRE_MARKET`
  report. It carries no JSON artifact (`artifact_path=None`) - this is DB-only canonical
  history the video never reads.

Not called from `main.py`. The caller (a test, a manual script, or a future scheduled task) is
responsible for invoking this and for supplying `already_covered` - the symbols already
represented by today's real mover facts, so a symbol never gets two independent
STOCK_RELATIVE_VOLUME facts for the same (instrument, market_date) under two different
report_ids, which would double-count that session in every later percentile window.
"""
from __future__ import annotations

import datetime as dt

import market
from adapters.market_adapter import MarketAdapter
from adapters.report_builder import facts_from
from core import MarketReport, Metric, ReportType
from core.sources import registry_snapshot

from . import ohlcv_service


def build_universe_relative_volume_facts(universe: dict, recap_date: dt.date, prev_date: dt.date,
                                         retrieved_at: dt.datetime,
                                         already_covered=frozenset(),
                                         demo: bool = False,
                                         now: dt.datetime | None = None,
                                         dataset: "ohlcv_service.UniverseOHLCVDataset | None" = None):
    """Bulk-fetch + validate RVOL Facts for `universe` minus `already_covered`.

    `dataset` (Phase 4.2 Packet 5.3): when supplied, RVOL rows are computed from the shared
    `UniverseOHLCVDataset` (`ohlcv_service.relative_volume_rows_from_dataset` - the same
    `market.relative_volume()` formula, just fed from data the shared acquisition boundary
    already loaded) instead of issuing a second, independent Yahoo bulk fetch. Omitting it
    keeps the pre-5.3 behaviour (its own direct `market.get_universe_relative_volume` call) as
    a compatibility fallback for any caller not yet passing a shared dataset.

    Returns `(facts, skip_reasons)`. `skip_reasons` maps a symbol to why it has no usable
    reading (`"no_recap_row"`, `"backfill_pending"`, `"date_gap"`,
    `"insufficient_relative_volume_history"`) - deterministic, never guessed.
    """
    scan_universe = {s: name for s, name in universe.items() if s not in already_covered}
    if dataset is not None:
        rows, skip_reasons = ohlcv_service.relative_volume_rows_from_dataset(dataset, scan_universe)
    else:
        rows, skip_reasons = market.get_universe_relative_volume(scan_universe, recap_date, prev_date)

    adapter = MarketAdapter(recap_date, retrieved_at, demo=demo)
    observations = adapter.observe_universe_relative_volume(rows)
    facts = facts_from(observations, now=now)
    return facts, skip_reasons


def acquire_universe_scan_report(history, universe: dict, report_date: dt.date,
                                 recap_date: dt.date, prev_date: dt.date,
                                 already_covered=frozenset(),
                                 demo: bool = False, now: dt.datetime | None = None,
                                 dataset: "ohlcv_service.UniverseOHLCVDataset | None" = None):
    """Build and persist one RADAR_SCAN report: universe-wide STOCK_RELATIVE_VOLUME facts,
    nothing else, never the day's real PRE_MARKET report.

    Returns `(scan_report, skip_reasons, persisted)`. `persisted` is `False` when a report
    with this id was already stored (the same idempotent semantics `save_report` gives every
    other canonical report - a rerun does not duplicate history).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    facts, skip_reasons = build_universe_relative_volume_facts(
        universe, recap_date, prev_date, retrieved_at=now,
        already_covered=already_covered, demo=demo, now=now, dataset=dataset)

    scan_report = MarketReport(
        report_date=report_date, report_type=ReportType.RADAR_SCAN, session_date=recap_date,
        generated_at=now, facts=facts,
        sources=registry_snapshot(f.metadata.get("published_value_source", "") for f in facts),
        metadata={
            "radar_scan": True,
            "universe_size": len(universe),
            "already_covered": sorted(already_covered),
            "skip_reasons": skip_reasons,
            "demo": bool(demo),
            "notes": ("Derived Market Intelligence Radar acquisition, not a publication "
                     "report - never rendered, never read by video.py/editorial/."),
        })
    if demo:
        scan_report.report_id = f"{scan_report.report_id}_DEMO"

    persisted = history.save_report(scan_report, artifact_path=None, is_demo=demo)
    return scan_report, skip_reasons, persisted


__all__ = ["build_universe_relative_volume_facts", "acquire_universe_scan_report"]

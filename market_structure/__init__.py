"""Market Structure: public-safe aggregates over the Radar's per-stock observations.

    Radar detectors (volume / technical / session-aligned OHLC)       PRIVATE, named stocks
            │
            ▼
    build_observations(universe_def, ...)   one observation per constituent, per-metric coverage
            ▼
    aggregate(...) -> MarketStructureSnapshot   counts over ONE named universe, reconciled
            ▼
    select_insights(snapshot)  -> 0-2 UNDER THE SURFACE scenes    PUBLIC, never a name

See docs/MARKET_STRUCTURE.md.
"""
from .aggregator import (MIN_COVERAGE_PCT, PARTIAL, PUBLISHABLE, SUPPRESSED,
                         MarketStructureSnapshot, Metric, ReconciliationError, aggregate,
                         exact_share, subset_contrast)
from .editorial import StructureInsight, select_insights
from .observations import StructureObservation, build_observations
from .sectors import UNCLASSIFIED, sector_for
from .store import load_snapshot, save_snapshot, structure_facts
from .universe import (Constituent, UniverseDefinition, UniverseError, from_constituent_csv,
                       from_market_meta)

__all__ = ["MarketStructureSnapshot", "Metric", "aggregate", "subset_contrast", "exact_share",
           "StructureInsight", "select_insights", "StructureObservation", "build_observations",
           "UniverseDefinition", "Constituent", "UniverseError", "from_constituent_csv",
           "from_market_meta", "sector_for", "UNCLASSIFIED", "MIN_COVERAGE_PCT", "PUBLISHABLE",
           "PARTIAL", "SUPPRESSED", "ReconciliationError", "save_snapshot", "load_snapshot",
           "structure_facts"]

"""Market Intelligence Radar: detects developments hard for an individual to find by hand
across many securities, starting with unusual trading volume (Phase 4.2 / Packet 1).

Distinct from `intelligence/`: that package curates at most three statements for one Short's
video, scoped to today's report's own gainers/losers. The Radar scans a caller-supplied
universe and returns every candidate that clears a threshold - there is no video and no
selection cap. Both packages share the same canonical-history rules (current session
excluded, trading sessions not calendar days, only eligible facts count, versions never
silently mixed) via `intelligence.history.HistoricalWindow`.

No LLM anywhere in this package. Nothing here recommends, predicts or ranks - see
`docs/MARKET_INTELLIGENCE_RADAR.md`.
"""
from .composite import build_candidates
from .models import (AnomalyLevel, AttentionLevel, DirectionCompatibility, EvidenceFamily,
                     RADAR_SCHEMA_VERSION, CALCULATION_VERSION, RadarCompositeSnapshot,
                     RelativePerformance, RelativePerformanceSnapshot, RelativePersistenceState,
                     StockRadarCandidate, VolumeAnomaly, VolumeRadarSnapshot)
from .relative import scan_relative_performance_universe
from .thresholds import (CompositeThresholds, DEFAULT_COMPOSITE_THRESHOLDS,
                         DEFAULT_RELATIVE_THRESHOLDS, DEFAULT_VOLUME_THRESHOLDS,
                         RelativePerformanceThresholds, VolumeThresholds, classify_anomaly)
from .volume import scan_universe

__all__ = ["AnomalyLevel", "VolumeAnomaly", "RelativePersistenceState", "RelativePerformance",
           "RelativePerformanceSnapshot", "EvidenceFamily", "DirectionCompatibility",
           "AttentionLevel", "StockRadarCandidate", "RadarCompositeSnapshot", "VolumeRadarSnapshot",
           "RADAR_SCHEMA_VERSION", "CALCULATION_VERSION", "VolumeThresholds",
           "DEFAULT_VOLUME_THRESHOLDS", "RelativePerformanceThresholds",
           "DEFAULT_RELATIVE_THRESHOLDS", "CompositeThresholds", "DEFAULT_COMPOSITE_THRESHOLDS",
           "classify_anomaly", "scan_universe", "scan_relative_performance_universe",
           "build_candidates"]

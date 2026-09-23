"""Deterministic, configurable thresholds for classifying unusual volume.

Cutoffs live on one frozen dataclass so a caller can tune them without touching
`radar/volume.py`. Defaults are conservative desk convention (2x/3x/5x average volume;
90th/97th percentile) rather than a single arbitrary "RVOL > 2" rule, and percentile can
only ever REFINE the RVOL-only base level upward, never invent a level RVOL alone did not
already earn and never demote one it did.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import AnomalyLevel


@dataclass(frozen=True)
class VolumeThresholds:
    elevated_rvol: float = 2.0
    unusual_rvol: float = 3.0
    extreme_rvol: float = 5.0
    unusual_percentile: float = 90.0
    extreme_percentile: float = 97.0
    # Sessions of comparable history required before a percentile is trusted at all - matches
    # the RVOL v2.0 lookback itself, so percentile context needs one full window.
    min_percentile_sample: int = 20


DEFAULT_VOLUME_THRESHOLDS = VolumeThresholds()


@dataclass(frozen=True)
class TechnicalThresholds:
    """Deterministic, configurable knobs for `radar.technical`. A frozen dataclass so a caller
    can tune sensitivity without touching detection logic, mirroring `VolumeThresholds`.
    """
    range_window_short: int = 20
    range_window_long: int = 50
    sma_short: int = 20
    sma_long: int = 50
    # Range-compression: compare the most recent N-session high/low range against the median
    # of that many PRIOR non-overlapping N-session windows. Needs at least
    # `compression_lookback_windows` full prior windows before a comparison is trusted at all.
    compression_window: int = 5
    compression_lookback_windows: int = 10
    # Flag compression when the recent window's range is at or below this fraction of the
    # historical median range - i.e. genuinely tight, not merely "a bit below average".
    compression_ratio: float = 0.6


DEFAULT_TECHNICAL_THRESHOLDS = TechnicalThresholds()


@dataclass(frozen=True)
class RelativePerformanceThresholds:
    """Deterministic, configurable knobs for `radar.relative`. A frozen dataclass so a caller
    can tune sensitivity without touching detection logic, mirroring `VolumeThresholds`/
    `TechnicalThresholds`.
    """
    # A window's market-relative pp reading must exceed this magnitude to count toward
    # PERSISTENT_POSITIVE/PERSISTENT_NEGATIVE; both must clear it and agree in sign. Below it
    # in both windows reads as NEUTRAL rather than "barely persistent".
    persistence_threshold_pp: float = 1.5


DEFAULT_RELATIVE_THRESHOLDS = RelativePerformanceThresholds()


@dataclass(frozen=True)
class CompositeThresholds:
    """Deterministic, configurable knobs for `radar.composite` (Phase 4.2 Packet 4). A frozen
    dataclass so a caller can tune eligibility without touching composition logic, mirroring
    `VolumeThresholds`/`TechnicalThresholds`/`RelativePerformanceThresholds`.
    """
    # A candidate must have at least this many INDEPENDENT evidence families (VOLUME, STRUCTURE,
    # RELATIVE_PERFORMANCE) active before it is surfaced at all - see `radar.models
    # .EvidenceFamily`. One family's evidence still exists in the underlying detector snapshot;
    # it simply does not enter the composite candidate set.
    min_independent_families: int = 2
    # Attention-level cutoffs, both read against `independent_signal_count` (capped at 3).
    notable_min_families: int = 2
    high_interest_min_families: int = 3


DEFAULT_COMPOSITE_THRESHOLDS = CompositeThresholds()


@dataclass(frozen=True)
class NoveltyThresholds:
    """Deterministic, configurable knobs for `radar.novelty` (Phase 4.2 Packet 5.4A). A frozen
    dataclass so a caller can tune editorial-state memory without touching classification logic,
    mirroring `VolumeThresholds`/`TechnicalThresholds`/`RelativePerformanceThresholds`/
    `CompositeThresholds`.
    """
    # How many PRIOR valid trading sessions (canonical spine positions, never calendar days) a
    # symbol's previous candidate appearance may lie within before it still counts as the
    # comparison baseline. This is editorial-state memory, not a trading signal - a candidate
    # that reappears unchanged after a gap within this window is a CONTINUATION; one that last
    # appeared further back is a fresh NEW_CANDIDATE, exactly as if it had never appeared before.
    lookback_sessions: int = 5


DEFAULT_NOVELTY_THRESHOLDS = NoveltyThresholds()


def classify_anomaly(rvol: float | None, percentile: float | None, sample: int,
                     thresholds: VolumeThresholds = DEFAULT_VOLUME_THRESHOLDS) -> AnomalyLevel | None:
    """Classify one relative-volume reading. `None` means "not anomalous" (or unusable).

    Price direction is never an input here - only the relative-volume ratio and, when there
    is enough comparable history, its historical percentile.
    """
    if rvol is None or rvol < thresholds.elevated_rvol:
        return None

    if rvol >= thresholds.extreme_rvol:
        level = AnomalyLevel.EXTREME
    elif rvol >= thresholds.unusual_rvol:
        level = AnomalyLevel.UNUSUAL
    else:
        level = AnomalyLevel.ELEVATED

    if percentile is not None and sample >= thresholds.min_percentile_sample:
        if level is AnomalyLevel.ELEVATED and percentile >= thresholds.unusual_percentile:
            level = AnomalyLevel.UNUSUAL
        if level is AnomalyLevel.UNUSUAL and percentile >= thresholds.extreme_percentile:
            level = AnomalyLevel.EXTREME

    return level


__all__ = ["VolumeThresholds", "DEFAULT_VOLUME_THRESHOLDS", "classify_anomaly",
           "TechnicalThresholds", "DEFAULT_TECHNICAL_THRESHOLDS",
           "RelativePerformanceThresholds", "DEFAULT_RELATIVE_THRESHOLDS",
           "CompositeThresholds", "DEFAULT_COMPOSITE_THRESHOLDS",
           "NoveltyThresholds", "DEFAULT_NOVELTY_THRESHOLDS"]

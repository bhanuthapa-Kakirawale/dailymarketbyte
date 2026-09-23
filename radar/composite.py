"""Evidence composition: does a symbol have MULTIPLE INDEPENDENT pieces of unusual evidence,
not "which stock moved the most" (Phase 4.2 Packet 4).

Two hard boundaries, mirrored from `radar/volume.py` / `radar/technical.py` / `radar/relative.py`:

* **No acquisition.** The only inputs are the three detectors' own typed snapshots
  (`VolumeRadarSnapshot`, `TechnicalRadarSnapshot`, `RelativePerformanceSnapshot`), already
  produced by `radar.volume.scan_universe` / `radar.technical.scan_technical_universe` /
  `radar.relative.scan_relative_performance_universe`. Nothing here calls a provider, a website
  or a model, and nothing here recomputes RVOL, a structural event or a return.
* **No mutation.** Every snapshot is read and never written; each `StockRadarCandidate` reuses
  the detectors' own dataclass instances by reference, never a copy.

This module asks a narrower question than any single detector: not "which stock gained the
most" but "which stocks have independent evidence, across separate families, that a
top-gainers page would not surface on its own". A stock with three independent, mutually
reinforcing pieces of evidence is worth a trader's attention even at a modest price change; a
large price move backed by nothing but the move itself is not a candidate here at all - see
`docs/MARKET_INTELLIGENCE_RADAR.md`'s Packet 4 section for the product reasoning and the
regression fixtures that pin both directions of that invariant.
"""
from __future__ import annotations

import datetime as dt

from .models import (AnomalyLevel, AttentionLevel, DirectionCompatibility, EvidenceFamily,
                     RadarCompositeSnapshot, RelativePersistenceState, StockRadarCandidate,
                     TechnicalEventType)
from .thresholds import CompositeThresholds, DEFAULT_COMPOSITE_THRESHOLDS

# Meaningful volume evidence (section 6 of the packet spec): ELEVATED alone stays contextual and
# never activates the VOLUME family - only UNUSUAL/EXTREME do.
_MEANINGFUL_VOLUME_LEVELS = (AnomalyLevel.UNUSUAL, AnomalyLevel.EXTREME)

# Meaningful structure evidence (section 7): discrete transition events only. RANGE_COMPRESSION
# describes a STATE, not a directional structural change, and stays contextual - it never
# activates the STRUCTURE family by itself.
_MEANINGFUL_STRUCTURE_EVENTS = frozenset({
    TechnicalEventType.BREAK_ABOVE_20D_RANGE, TechnicalEventType.BREAK_BELOW_20D_RANGE,
    TechnicalEventType.BREAK_ABOVE_50D_RANGE, TechnicalEventType.BREAK_BELOW_50D_RANGE,
    TechnicalEventType.CROSS_ABOVE_SMA20, TechnicalEventType.CROSS_BELOW_SMA20,
    TechnicalEventType.CROSS_ABOVE_SMA50, TechnicalEventType.CROSS_BELOW_SMA50,
})
_POSITIVE_STRUCTURE_EVENTS = frozenset({
    TechnicalEventType.BREAK_ABOVE_20D_RANGE, TechnicalEventType.BREAK_ABOVE_50D_RANGE,
    TechnicalEventType.CROSS_ABOVE_SMA20, TechnicalEventType.CROSS_ABOVE_SMA50,
})
_NEGATIVE_STRUCTURE_EVENTS = frozenset({
    TechnicalEventType.BREAK_BELOW_20D_RANGE, TechnicalEventType.BREAK_BELOW_50D_RANGE,
    TechnicalEventType.CROSS_BELOW_SMA20, TechnicalEventType.CROSS_BELOW_SMA50,
})

# Meaningful relative evidence (section 8): only a persistence VERDICT counts. MIXED/NEUTRAL/None
# stay non-active - the raw pp figures are still preserved on `RelativePerformance` itself.
_MEANINGFUL_RELATIVE_STATES = (RelativePersistenceState.PERSISTENT_POSITIVE,
                               RelativePersistenceState.PERSISTENT_NEGATIVE)


def build_candidates(volume_snapshot, technical_snapshot, relative_snapshot,
                     session_date: dt.date, *,
                     thresholds: CompositeThresholds = DEFAULT_COMPOSITE_THRESHOLDS,
                     as_of: dt.datetime | None = None) -> RadarCompositeSnapshot:
    """Join the three detectors' snapshots for `session_date` and compose evidence per symbol.

    Any of `volume_snapshot`/`technical_snapshot`/`relative_snapshot` may be `None` - a missing
    detector's evidence simply stays missing, never fabricated as neutral (section 16). A
    snapshot whose OWN `session_date` does not match the requested `session_date` is ignored
    entirely (with a warning) rather than silently joined against the wrong session - mismatched
    sessions must never be combined (section 6 / section 16 of the packet spec).

    The candidate universe is the union of every symbol any supplied detector actually
    `scanned` for this session - a symbol seen by only one detector can still qualify if that
    detector's own evidence, combined with nothing else, somehow cleared the family bar (it
    cannot on its own, since one detector is at most one family, but it stays visible in
    `universe_size`/`non_candidates_count` rather than disappearing).
    """
    warnings: list = []
    volume_by_symbol = _index_matching(volume_snapshot, "anomalies", "market_date", session_date,
                                       warnings, "volume")
    technical_by_symbol = _index_matching(technical_snapshot, "flagged", "market_date",
                                          session_date, warnings, "technical")
    relative_by_symbol = _index_matching(relative_snapshot, "results", "session_date",
                                         session_date, warnings, "relative")

    universe: set = set()
    for snapshot in (volume_snapshot, technical_snapshot, relative_snapshot):
        if snapshot is not None and snapshot.session_date == session_date:
            universe.update(snapshot.scanned)

    candidates = []
    for symbol in sorted(universe):
        candidate = _build_candidate(
            symbol, session_date, volume_by_symbol.get(symbol),
            technical_by_symbol.get(symbol), relative_by_symbol.get(symbol), thresholds)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda c: c.instrument)

    return RadarCompositeSnapshot(
        session_date=session_date, generated_at=as_of or dt.datetime.now(dt.timezone.utc),
        universe_size=len(universe), symbols_with_volume=len(volume_by_symbol),
        symbols_with_technical=len(technical_by_symbol), symbols_with_relative=len(relative_by_symbol),
        candidates=candidates, candidate_count=len(candidates),
        non_candidates_count=len(universe) - len(candidates), warnings=warnings)


def _index_matching(snapshot, list_attr: str, date_attr: str, session_date: dt.date,
                    warnings: list, label: str) -> dict:
    """`{instrument: item}` for `snapshot`'s `list_attr`, restricted to entries whose own
    `date_attr` equals `session_date`. A whole snapshot with a mismatched `session_date` is
    dropped entirely (with a warning) rather than partially trusted."""
    if snapshot is None:
        return {}
    if snapshot.session_date != session_date:
        warnings.append(f"{label} snapshot session_date {snapshot.session_date} does not match "
                        f"requested {session_date}; ignored entirely")
        return {}
    out = {}
    for item in getattr(snapshot, list_attr):
        if getattr(item, date_attr) == session_date:
            out[item.instrument] = item
    return out


def _build_candidate(symbol: str, session_date: dt.date, volume, technical, relative,
                     thresholds: CompositeThresholds) -> StockRadarCandidate | None:
    """One symbol's composed evidence, or `None` if it does not clear
    `thresholds.min_independent_families`. Eligibility is judged BEFORE construction - a
    sub-threshold candidate is never built and then discarded (section 5)."""
    volume_active, volume_lines, volume_codes = _volume_evidence(volume)
    structure_active, structure_lines, structure_codes, structure_direction = _structure_evidence(technical)
    relative_active, relative_lines, relative_codes, relative_direction = _relative_evidence(relative)

    active_families = []
    if volume_active:
        active_families.append(EvidenceFamily.VOLUME)
    if structure_active:
        active_families.append(EvidenceFamily.STRUCTURE)
    if relative_active:
        active_families.append(EvidenceFamily.RELATIVE_PERFORMANCE)

    # Capped at 3 (section 4): stacking evidence within one family can never inflate this past
    # the number of INDEPENDENT families actually active.
    independent_signal_count = min(len(active_families), 3)
    if independent_signal_count < thresholds.min_independent_families:
        return None

    warnings = []
    if volume is None:
        warnings.append(f"{symbol}: no volume-anomaly evidence available for this session")
    if technical is None:
        warnings.append(f"{symbol}: no technical-structure evidence available for this session")
    if relative is None:
        warnings.append(f"{symbol}: no relative-performance evidence available for this session")

    return StockRadarCandidate(
        instrument=symbol, market_date=session_date,
        volume_anomaly=volume, technical_anomaly=technical, relative_strength=relative,
        price_change_pct=_price_change_pct(volume, relative),
        evidence=volume_lines + structure_lines + relative_lines,
        reason_codes=volume_codes + structure_codes + relative_codes,
        independent_signal_count=independent_signal_count,
        active_families=[f.value for f in active_families],
        direction_compatibility=_compatibility(structure_direction, relative_direction),
        attention_level=_attention_level(independent_signal_count, thresholds),
        supporting_session_dates=_supporting_dates(volume, technical, relative),
        warnings=warnings)


def _volume_evidence(volume) -> tuple[bool, list, list]:
    """VOLUME is non-directional by construction (section 9) - it never returns a direction."""
    if volume is None or volume.level not in _MEANINGFUL_VOLUME_LEVELS:
        return False, [], []
    rvol = volume.relative_volume
    line = (f"Volume is {rvol:.1f}x its prior-20-session average."
           if rvol is not None else "Volume is unusual relative to its prior-20-session average.")
    return True, [line], [f"VOLUME_{volume.level.value}"]


def _structure_evidence(technical) -> tuple[bool, list, list, int | None]:
    meaningful = [e for e in (technical.events if technical is not None else [])
                 if e.event_type in _MEANINGFUL_STRUCTURE_EVENTS]
    if not meaningful:
        return False, [], [], None

    lines, codes, signs = [], [], []
    for event in meaningful:
        lines.append(_structure_line(event))
        codes.append(f"STRUCTURE_{event.event_type.value}")
        if event.event_type in _POSITIVE_STRUCTURE_EVENTS:
            signs.append(1)
        elif event.event_type in _NEGATIVE_STRUCTURE_EVENTS:
            signs.append(-1)
    return True, lines, codes, _combine_signs(signs)


def _structure_line(event) -> str:
    """Deterministic factual sentence from the event's own `evidence` dict - never the model
    filling in a template, and never BUY/SELL/bullish/bearish language."""
    ev = event.evidence
    event_type = event.event_type
    positive = event_type in _POSITIVE_STRUCTURE_EVENTS
    direction_word = "above" if positive else "below"

    if event_type in (TechnicalEventType.BREAK_ABOVE_20D_RANGE, TechnicalEventType.BREAK_BELOW_20D_RANGE,
                      TechnicalEventType.BREAK_ABOVE_50D_RANGE, TechnicalEventType.BREAK_BELOW_50D_RANGE):
        window = ev.get("window_sessions")
        bound = "high" if positive else "low"
        distance = ev.get("break_distance_pct")
        if distance is None:
            return f"Price closed {direction_word} its prior {window}-session {bound}."
        return f"Price closed {abs(distance):.1f}% {direction_word} its prior {window}-session {bound}."

    # CROSS_ABOVE/BELOW_SMA20/50
    window = "20" if "SMA20" in event_type.value else "50"
    return f"Price crossed {direction_word} its {window}-session average."


def _relative_evidence(relative) -> tuple[bool, list, list, int | None]:
    if relative is None or relative.persistence_state not in _MEANINGFUL_RELATIVE_STATES:
        return False, [], [], None

    positive = relative.persistence_state is RelativePersistenceState.PERSISTENT_POSITIVE
    verb = "outperformance" if positive else "underperformance"
    five, twenty = relative.market_relative_5d_pp, relative.market_relative_20d_pp
    line = (f"Persistent {verb} versus Nifty: {five:+.1f} pp over 5 sessions, "
           f"{twenty:+.1f} pp over 20 sessions.")
    code = f"RELATIVE_{relative.persistence_state.value}"
    return True, [line], [code], (1 if positive else -1)


def _combine_signs(signs: list) -> int | None:
    """`None` (no directional signal), `1`/`-1` (unanimous), or `0` (internally conflicting -
    e.g. a break above one range alongside a cross below an SMA in the same session)."""
    if not signs:
        return None
    unique = set(signs)
    if unique == {1}:
        return 1
    if unique == {-1}:
        return -1
    return 0


def _compatibility(structure_direction: int | None,
                   relative_direction: int | None) -> DirectionCompatibility:
    """Only STRUCTURE and RELATIVE_PERFORMANCE ever contribute a direction - VOLUME is excluded
    by construction (section 9). `NON_DIRECTIONAL` covers a candidate whose only active
    family is VOLUME-adjacent evidence with no directional family active at all (not reachable
    at the current `min_independent_families >= 2` default, since VOLUME alone can never clear
    eligibility, but kept correct for a caller who lowers the threshold)."""
    directions = [d for d in (structure_direction, relative_direction) if d is not None]
    if not directions:
        return DirectionCompatibility.NON_DIRECTIONAL
    unique = set(directions)
    if unique == {1}:
        return DirectionCompatibility.ALIGNED_POSITIVE
    if unique == {-1}:
        return DirectionCompatibility.ALIGNED_NEGATIVE
    return DirectionCompatibility.MIXED


def _attention_level(independent_signal_count: int,
                     thresholds: CompositeThresholds) -> AttentionLevel | None:
    if independent_signal_count >= thresholds.high_interest_min_families:
        return AttentionLevel.HIGH_INTEREST
    if independent_signal_count >= thresholds.notable_min_families:
        return AttentionLevel.NOTABLE
    return None


def _price_change_pct(volume, relative) -> float | None:
    """Context only, never a classification input (section 1's own worked example: ABC's +0.9%
    price move is deliberately unremarkable). Prefers the volume detector's own price context;
    falls back to the relative detector's 1D stock return when volume evidence is absent."""
    if volume is not None and volume.price_change_pct is not None:
        return volume.price_change_pct
    if relative is not None and relative.stock_return_1d is not None:
        return relative.stock_return_1d
    return None


def _supporting_dates(volume, technical, relative) -> list:
    dates: set = set()
    if volume is not None:
        dates.add(volume.market_date)
    if technical is not None:
        dates.update(technical.supporting_session_dates)
    if relative is not None:
        dates.update(relative.supporting_session_dates)
    return sorted(dates)


__all__ = ["build_candidates"]

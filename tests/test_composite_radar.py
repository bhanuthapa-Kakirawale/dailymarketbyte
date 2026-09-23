"""Evidence composition (Phase 4.2 Packet 4): independent-family eligibility, per-family
"meaningful vs contextual" activation rules, direction compatibility, attention level, session
joining, missing-detector tolerance, determinism and immutability. Offline only - every input is
a hand-built detector snapshot, never a real fetch.
"""
import copy
import datetime as dt

from radar import (AnomalyLevel, AttentionLevel, DirectionCompatibility, RelativePersistenceState,
                   build_candidates)
from radar.models import (RelativePerformance, RelativePerformanceSnapshot, TechnicalEvent,
                          TechnicalEventType, TechnicalRadarSnapshot, TechnicalStructure,
                          VolumeAnomaly, VolumeRadarSnapshot)

SESSION = dt.date(2026, 9, 22)
OTHER_SESSION = dt.date(2026, 9, 21)
AS_OF = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)


# =============================================================== builders
def make_volume(symbol, level, rvol=4.0, price_change_pct=0.8, session_date=SESSION):
    return VolumeAnomaly(
        instrument=symbol, market_date=session_date, current_volume=None, average_volume_20=None,
        relative_volume=rvol, prior_sessions_available=25, rvol_percentile=None,
        rvol_historical_rank=None, highest_rvol_in_n_sessions=None,
        price_change_pct=price_change_pct, level=level, why_flagged="test")


def volume_snapshot(anomalies, scanned=None, session_date=SESSION):
    scanned = scanned if scanned is not None else [a.instrument for a in anomalies]
    return VolumeRadarSnapshot(session_date=session_date, generated_at=AS_OF,
                               universe_size=len(scanned), universe_source="test",
                               anomalies=list(anomalies), scanned=list(scanned))


def make_event(event_type, **evidence):
    return TechnicalEvent(event_type=event_type, evidence=evidence, why="test event")


def make_technical(symbol, events=None, session_date=SESSION):
    return TechnicalStructure(
        instrument=symbol, market_date=session_date, close=100.0, prior_20_high=None,
        prior_20_low=None, prior_50_high=None, prior_50_low=None, sma20=None, sma50=None,
        distance_from_sma20_pct=None, distance_from_sma50_pct=None, recent_5d_range_pct=None,
        median_5d_range_pct=None, sessions_available=25, events=list(events or []),
        supporting_session_dates=[session_date])


def technical_snapshot(flagged, scanned=None, session_date=SESSION):
    scanned = scanned if scanned is not None else [s.instrument for s in flagged]
    return TechnicalRadarSnapshot(session_date=session_date, generated_at=AS_OF,
                                  universe_size=len(scanned), universe_source="test",
                                  flagged=list(flagged), scanned=list(scanned))


def make_relative(symbol, persistence_state=None, five=None, twenty=None, one=None,
                  session_date=SESSION):
    return RelativePerformance(
        instrument=symbol, session_date=session_date, stock_return_1d=one, stock_return_5d=five,
        stock_return_20d=twenty, market_return_1d=None, market_return_5d=None,
        market_return_20d=None, market_relative_1d_pp=None, market_relative_5d_pp=five,
        market_relative_20d_pp=twenty, sector=None, sector_return_1d=None, sector_return_5d=None,
        sector_return_20d=None, sector_relative_1d_pp=None, sector_relative_5d_pp=None,
        sector_relative_20d_pp=None, persistence_state=persistence_state, relative_shift_pp=None,
        supporting_session_dates=[session_date])


def relative_snapshot(results, scanned=None, session_date=SESSION):
    scanned = scanned if scanned is not None else [r.instrument for r in results]
    return RelativePerformanceSnapshot(session_date=session_date, generated_at=AS_OF,
                                       universe_size=len(scanned), universe_source="test",
                                       results=list(results), scanned=list(scanned))


# =============================================================== eligibility
def test_one_family_is_not_a_candidate():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    snap = build_candidates(vol, None, None, SESSION)
    assert snap.candidate_count == 0
    assert snap.candidates == []
    assert snap.non_candidates_count == 1


def test_two_families_is_a_candidate():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    snap = build_candidates(vol, tech, None, SESSION)
    assert snap.candidate_count == 1
    assert snap.candidates[0].instrument == "ABC"
    assert snap.candidates[0].independent_signal_count == 2


def test_three_families_is_a_candidate():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidate_count == 1
    candidate = snap.candidates[0]
    assert candidate.independent_signal_count == 3
    assert candidate.attention_level is AttentionLevel.HIGH_INTEREST


# =============================================================== volume family
def test_elevated_volume_alone_does_not_activate_family():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.ELEVATED)])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.0, twenty=3.0)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert snap.candidate_count == 0


def test_unusual_volume_activates_family():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.0, twenty=3.0)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert snap.candidate_count == 1
    assert "VOLUME_UNUSUAL" in snap.candidates[0].reason_codes


def test_extreme_volume_activates_family():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.0, twenty=3.0)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert snap.candidate_count == 1
    assert "VOLUME_EXTREME" in snap.candidates[0].reason_codes


# =============================================================== structure family
def test_range_break_activates_structure():
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    snap = build_candidates(vol, tech, None, SESSION)
    assert any(c.instrument == "ABC" for c in snap.candidates)
    assert "STRUCTURE_BREAK_ABOVE_20D_RANGE" in snap.candidates[0].reason_codes


def test_sma_cross_activates_structure():
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.CROSS_ABOVE_SMA20)])])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    snap = build_candidates(vol, tech, None, SESSION)
    assert "STRUCTURE_CROSS_ABOVE_SMA20" in snap.candidates[0].reason_codes


def test_compression_alone_does_not_activate_structure():
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.RANGE_COMPRESSION, recent_range_pct=1.0,
                           median_range_pct=3.0)])])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    snap = build_candidates(vol, tech, None, SESSION)
    assert snap.candidate_count == 0


# =============================================================== relative family
def test_persistent_positive_activates_relative():
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert "RELATIVE_PERSISTENT_POSITIVE" in snap.candidates[0].reason_codes


def test_persistent_negative_activates_relative():
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_NEGATIVE,
                                           five=-3.7, twenty=-7.9)])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert "RELATIVE_PERSISTENT_NEGATIVE" in snap.candidates[0].reason_codes


def test_mixed_persistence_does_not_activate_relative():
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.MIXED,
                                           five=3.7, twenty=-7.9)])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidates[0].independent_signal_count == 2
    assert not any(code.startswith("RELATIVE_") for code in snap.candidates[0].reason_codes)


def test_neutral_persistence_does_not_activate_relative():
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.NEUTRAL,
                                           five=0.2, twenty=-0.3)])
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert snap.candidate_count == 0


# =============================================================== direction compatibility
def test_aligned_positive():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidates[0].direction_compatibility is DirectionCompatibility.ALIGNED_POSITIVE


def test_aligned_negative():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_BELOW_20D_RANGE, window_sessions=20,
                           break_distance_pct=-2.0)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_NEGATIVE,
                                           five=-3.8, twenty=-6.1)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidates[0].direction_compatibility is DirectionCompatibility.ALIGNED_NEGATIVE


def test_conflicting_evidence_is_mixed():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_NEGATIVE,
                                           five=-3.8, twenty=-6.1)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidates[0].direction_compatibility is DirectionCompatibility.MIXED


def test_volume_never_contributes_a_direction():
    """Volume EXTREME + a single positive structural event, with relative inactive: the only
    directional evidence is STRUCTURE, so compatibility must read from structure alone, never
    be forced to NON_DIRECTIONAL just because relative is absent."""
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_BELOW_20D_RANGE, window_sessions=20,
                           break_distance_pct=-1.5)])])
    snap = build_candidates(vol, tech, None, SESSION)
    assert snap.candidates[0].direction_compatibility is DirectionCompatibility.ALIGNED_NEGATIVE


# =============================================================== product invariants
def test_quiet_stock_with_three_independent_signals_becomes_candidate():
    """Packet spec's headline case: a modest +0.8% move that a top-gainers page would miss."""
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.UNUSUAL, rvol=4.3,
                                       price_change_pct=0.8)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.2)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.4)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidate_count == 1
    candidate = snap.candidates[0]
    assert candidate.instrument == "ABC"
    assert candidate.price_change_pct == 0.8
    assert candidate.independent_signal_count == 3
    assert candidate.attention_level is AttentionLevel.HIGH_INTEREST


def test_large_mover_with_no_meaningful_evidence_is_rejected():
    """+8.5% alone, no meaningful volume/structure/relative evidence, must NOT become a
    candidate just because the price move is large."""
    vol = volume_snapshot([], scanned=["XYZ"])           # RVOL too low to even flag
    tech = technical_snapshot([], scanned=["XYZ"])        # inside its 20D range
    rel = relative_snapshot([make_relative("XYZ", RelativePersistenceState.MIXED,
                                           five=8.5, twenty=1.0, one=8.5)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidate_count == 0
    assert snap.non_candidates_count == 1
    assert "XYZ" not in [c.instrument for c in snap.candidates]


def test_downside_anomaly_is_discoverable():
    vol = volume_snapshot([make_volume("LMN", AnomalyLevel.UNUSUAL, rvol=3.8,
                                       price_change_pct=-2.1)])
    tech = technical_snapshot([make_technical(
        "LMN", [make_event(TechnicalEventType.BREAK_BELOW_20D_RANGE, window_sessions=20,
                           break_distance_pct=-1.9)])])
    rel = relative_snapshot([make_relative("LMN", RelativePersistenceState.PERSISTENT_NEGATIVE,
                                           five=-3.1, twenty=-5.5)])
    snap = build_candidates(vol, tech, rel, SESSION)
    assert snap.candidate_count == 1
    candidate = snap.candidates[0]
    assert candidate.direction_compatibility is DirectionCompatibility.ALIGNED_NEGATIVE
    assert not any(word in " ".join(candidate.evidence).lower()
                   for word in ("buy", "sell", "bullish", "bearish"))


# =============================================================== session integrity
def test_mismatched_session_dates_never_combined():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME, session_date=OTHER_SESSION)],
                          session_date=OTHER_SESSION)
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])], session_date=SESSION)
    snap = build_candidates(vol, tech, None, SESSION)
    assert snap.candidate_count == 0
    assert snap.symbols_with_volume == 0
    assert any("volume" in w for w in snap.warnings)


# =============================================================== missing detector
def test_missing_volume_snapshot():
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    snap = build_candidates(None, tech, rel, SESSION)
    assert snap.candidate_count == 1
    assert snap.candidates[0].independent_signal_count == 2
    assert any("volume" in w for w in snap.candidates[0].warnings)


def test_missing_technical_snapshot():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    snap = build_candidates(vol, None, rel, SESSION)
    assert snap.candidate_count == 1
    assert snap.candidates[0].independent_signal_count == 2


def test_missing_relative_snapshot():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    snap = build_candidates(vol, tech, None, SESSION)
    assert snap.candidate_count == 1
    assert snap.candidates[0].independent_signal_count == 2


# =============================================================== determinism
def test_deterministic_serialization_and_order():
    vol = volume_snapshot([make_volume("ZED", AnomalyLevel.EXTREME),
                           make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([
        make_technical("ZED", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE,
                                          window_sessions=20, break_distance_pct=1.0)]),
        make_technical("ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE,
                                          window_sessions=20, break_distance_pct=1.0)])])
    snap1 = build_candidates(vol, tech, None, SESSION, as_of=AS_OF)
    snap2 = build_candidates(vol, tech, None, SESSION, as_of=AS_OF)
    assert snap1.to_dict() == snap2.to_dict()
    assert [c.instrument for c in snap1.candidates] == ["ABC", "ZED"]


# =============================================================== immutability
def test_inputs_are_not_mutated():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    before = (copy.deepcopy(vol.to_dict()), copy.deepcopy(tech.to_dict()), copy.deepcopy(rel.to_dict()))
    build_candidates(vol, tech, rel, SESSION)
    after = (vol.to_dict(), tech.to_dict(), rel.to_dict())
    assert before == after


# =============================================================== round-trip
def test_snapshot_round_trips_through_json():
    vol = volume_snapshot([make_volume("ABC", AnomalyLevel.EXTREME)])
    tech = technical_snapshot([make_technical(
        "ABC", [make_event(TechnicalEventType.BREAK_ABOVE_20D_RANGE, window_sessions=20,
                           break_distance_pct=1.8)])])
    rel = relative_snapshot([make_relative("ABC", RelativePersistenceState.PERSISTENT_POSITIVE,
                                           five=3.7, twenty=7.9)])
    snap = build_candidates(vol, tech, rel, SESSION, as_of=AS_OF)
    from radar.models import RadarCompositeSnapshot
    restored = RadarCompositeSnapshot.from_json(snap.to_json())
    assert restored.to_dict() == snap.to_dict()

"""Phase 4.2 Packet 5.4A: deterministic event-novelty classification. Fully offline and pure -
no network, no database, no canonical history; input is a hand-built, already-consecutive
sequence of `(session_date, [StockRadarCandidate, ...])` pairs, exactly the shape
`radar.novelty.classify_history` consumes.
"""
import datetime as dt

import pytest

from radar.models import (AttentionLevel, DirectionCompatibility, NoveltyType,
                          RelativePerformance, RelativePersistenceState, StockRadarCandidate)
from radar.novelty import classify_history
from radar.thresholds import NoveltyThresholds

D0 = dt.date(2026, 9, 1)


def _date(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)  # tests only ever use these as opaque, ordered labels


def _relative(persistence=None):
    if persistence is None:
        return None
    return RelativePerformance(
        instrument="X", session_date=D0, stock_return_1d=None, stock_return_5d=None,
        stock_return_20d=None, market_return_1d=None, market_return_5d=None, market_return_20d=None,
        market_relative_1d_pp=None, market_relative_5d_pp=None, market_relative_20d_pp=None,
        sector=None, sector_return_1d=None, sector_return_5d=None, sector_return_20d=None,
        sector_relative_1d_pp=None, sector_relative_5d_pp=None, sector_relative_20d_pp=None,
        persistence_state=persistence, relative_shift_pp=None)


def make_candidate(symbol, session_date, *, families=("STRUCTURE", "RELATIVE_PERFORMANCE"),
                   technical_events=(), persistence=None,
                   direction=DirectionCompatibility.ALIGNED_POSITIVE,
                   attention=AttentionLevel.NOTABLE, price_change_pct=1.0):
    reason_codes = [f"STRUCTURE_{e}" for e in technical_events]
    return StockRadarCandidate(
        instrument=symbol, market_date=session_date, active_families=list(families),
        reason_codes=reason_codes, direction_compatibility=direction, attention_level=attention,
        relative_strength=_relative(persistence), price_change_pct=price_change_pct,
        independent_signal_count=len(families))


def _only(result_by_date, session_date, symbol=None):
    records = result_by_date[session_date]
    if symbol is None:
        assert len(records) == 1
        return records[0]
    return next(r for r in records if r.instrument == symbol)


# --------------------------------------------------------------------------- Test 1
def test_first_appearance_is_new_candidate():
    c = make_candidate("ABC", _date(0))
    result = classify_history([(_date(0), [c])])
    rec = _only(result, _date(0))
    assert rec.novelty_type == NoveltyType.NEW_CANDIDATE
    assert rec.previous_candidate_date is None
    assert rec.sessions_since_previous_candidate is None


# --------------------------------------------------------------------------- Test 2
def test_identical_next_session_is_continuation():
    c1 = make_candidate("ABC", _date(0))
    c2 = make_candidate("ABC", _date(1))
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.CONTINUATION
    assert rec.previous_candidate_date == _date(0)
    assert rec.sessions_since_previous_candidate == 1


# --------------------------------------------------------------------------- Test 3
def test_gap_reappearance_within_lookback_unchanged_is_continuation():
    c_mon = make_candidate("ABC", _date(0))
    c_thu = make_candidate("ABC", _date(3))
    sessions = [(_date(0), [c_mon]), (_date(1), []), (_date(2), []), (_date(3), [c_thu])]
    result = classify_history(sessions)
    rec = _only(result, _date(3))
    assert rec.novelty_type == NoveltyType.CONTINUATION
    assert rec.previous_candidate_date == _date(0)
    assert rec.sessions_since_previous_candidate == 3


# --------------------------------------------------------------------------- Test 4
def test_reappearance_outside_lookback_is_new_candidate():
    c_first = make_candidate("ABC", _date(0))
    c_later = make_candidate("ABC", _date(6))  # 6 sessions later, lookback default = 5
    sessions = [(_date(0), [c_first])] + [(_date(i), []) for i in range(1, 6)] + [(_date(6), [c_later])]
    result = classify_history(sessions)
    rec = _only(result, _date(6))
    assert rec.novelty_type == NoveltyType.NEW_CANDIDATE
    assert rec.previous_candidate_date is None
    assert rec.sessions_since_previous_candidate is None


def test_lookback_boundary_is_inclusive():
    """Exactly `lookback_sessions` positions back still counts as within the window."""
    thresholds = NoveltyThresholds(lookback_sessions=5)
    c_first = make_candidate("ABC", _date(0))
    c_later = make_candidate("ABC", _date(5))  # exactly 5 sessions later
    sessions = [(_date(0), [c_first])] + [(_date(i), []) for i in range(1, 5)] + [(_date(5), [c_later])]
    result = classify_history(sessions, thresholds=thresholds)
    rec = _only(result, _date(5))
    assert rec.novelty_type == NoveltyType.CONTINUATION
    assert rec.sessions_since_previous_candidate == 5


# --------------------------------------------------------------------------- Test 5
def test_new_evidence_family():
    c1 = make_candidate("ABC", _date(0), families=("STRUCTURE", "RELATIVE_PERFORMANCE"))
    c2 = make_candidate("ABC", _date(1), families=("STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"))
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.NEW_EVIDENCE_FAMILY
    assert rec.new_families == ["VOLUME"]
    assert "NEW_FAMILY_VOLUME" in rec.change_reason_codes


def test_lost_family_alone_is_not_novel():
    c1 = make_candidate("ABC", _date(0), families=("STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"))
    c2 = make_candidate("ABC", _date(1), families=("STRUCTURE", "RELATIVE_PERFORMANCE"))
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.CONTINUATION
    assert rec.lost_families == ["VOLUME"]
    assert rec.new_families == []


# --------------------------------------------------------------------------- Test 6
def test_new_technical_event():
    c1 = make_candidate("ABC", _date(0), technical_events=["CROSS_ABOVE_SMA20"])
    c2 = make_candidate("ABC", _date(1),
                        technical_events=["CROSS_ABOVE_SMA20", "BREAK_ABOVE_20D_RANGE"])
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.NEW_TECHNICAL_EVENT
    assert rec.new_technical_events == ["BREAK_ABOVE_20D_RANGE"]


def test_persistent_technical_event_not_repeatedly_new():
    c1 = make_candidate("ABC", _date(0), technical_events=["CROSS_ABOVE_SMA20"])
    c2 = make_candidate("ABC", _date(1), technical_events=["CROSS_ABOVE_SMA20"])
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.CONTINUATION
    assert rec.new_technical_events == []


# --------------------------------------------------------------------------- Test 7
def test_persistence_transition():
    c1 = make_candidate("ABC", _date(0), persistence=RelativePersistenceState.MIXED)
    c2 = make_candidate("ABC", _date(1), persistence=RelativePersistenceState.PERSISTENT_POSITIVE)
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.PERSISTENCE_TRANSITION
    assert rec.previous_persistence == "MIXED"
    assert rec.current_persistence == "PERSISTENT_POSITIVE"


def test_persistence_transition_requires_both_values_available():
    c1 = make_candidate("ABC", _date(0), persistence=None)
    c2 = make_candidate("ABC", _date(1), persistence=RelativePersistenceState.PERSISTENT_POSITIVE)
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type != NoveltyType.PERSISTENCE_TRANSITION


# --------------------------------------------------------------------------- Test 8
def test_direction_transition():
    c1 = make_candidate("ABC", _date(0), direction=DirectionCompatibility.MIXED)
    c2 = make_candidate("ABC", _date(1), direction=DirectionCompatibility.ALIGNED_POSITIVE)
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.DIRECTION_TRANSITION
    assert rec.previous_direction == "MIXED"
    assert rec.current_direction == "ALIGNED_POSITIVE"


# --------------------------------------------------------------------------- Test 9
def test_attention_escalation_notable_to_high_interest():
    c1 = make_candidate("ABC", _date(0), attention=AttentionLevel.NOTABLE)
    c2 = make_candidate("ABC", _date(1), attention=AttentionLevel.HIGH_INTEREST)
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.ATTENTION_ESCALATION


def test_attention_de_escalation_is_not_escalation():
    c1 = make_candidate("ABC", _date(0), attention=AttentionLevel.HIGH_INTEREST)
    c2 = make_candidate("ABC", _date(1), attention=AttentionLevel.NOTABLE)
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type != NoveltyType.ATTENTION_ESCALATION


# --------------------------------------------------------------------------- Test 10
def test_multiple_simultaneous_changes():
    c1 = make_candidate("ABC", _date(0), families=("STRUCTURE", "RELATIVE_PERFORMANCE"),
                        attention=AttentionLevel.NOTABLE)
    c2 = make_candidate("ABC", _date(1), families=("STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"),
                        attention=AttentionLevel.HIGH_INTEREST)
    result = classify_history([(_date(0), [c1]), (_date(1), [c2])])
    rec = _only(result, _date(1))
    assert rec.novelty_type == NoveltyType.MULTIPLE_CHANGES
    assert "NEW_FAMILY_VOLUME" in rec.change_reason_codes
    assert "ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST" in rec.change_reason_codes


# --------------------------------------------------------------------------- Test 11
def test_weekend_holiday_gap_does_not_consume_lookback():
    """A gap in the caller's session LIST (calendar-day skip, no session at all) does not
    behave like a session where the symbol simply wasn't a candidate - list-index adjacency
    IS trading-session adjacency by contract; this test proves consecutive trading sessions
    (irrespective of the calendar gap between their dates) give the same result as any other
    consecutive pair."""
    monday = dt.date(2026, 9, 14)
    friday_before = dt.date(2026, 9, 11)  # 3 calendar days earlier, but the PRECEDING trading session
    c1 = make_candidate("ABC", friday_before)
    c2 = make_candidate("ABC", monday)
    result = classify_history([(friday_before, [c1]), (monday, [c2])])
    rec = _only(result, monday)
    assert rec.novelty_type == NoveltyType.CONTINUATION
    assert rec.sessions_since_previous_candidate == 1


# --------------------------------------------------------------------------- Test 12
def test_future_candidate_cannot_influence_historical_novelty():
    c0 = make_candidate("ABC", _date(0))
    c1 = make_candidate("ABC", _date(1), families=("STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"))
    full = classify_history([(_date(0), [c0]), (_date(1), [c1])])
    prefix_only = classify_history([(_date(0), [c0])])
    assert prefix_only[_date(0)][0].to_dict() == full[_date(0)][0].to_dict()


# --------------------------------------------------------------------------- Test 13
def test_raw_candidate_object_unchanged_after_classification():
    c1 = make_candidate("ABC", _date(0))
    c2 = make_candidate("ABC", _date(1), families=("STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"))
    before = [c1.to_dict(), c2.to_dict()]
    classify_history([(_date(0), [c1]), (_date(1), [c2])])
    after = [c1.to_dict(), c2.to_dict()]
    assert before == after


# --------------------------------------------------------------------------- Test 14
def test_deterministic_repeated_run():
    c1 = make_candidate("ABC", _date(0))
    c2 = make_candidate("ABC", _date(1), families=("STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"),
                        attention=AttentionLevel.HIGH_INTEREST)
    d1 = make_candidate("DEF", _date(0))
    d2 = make_candidate("DEF", _date(1))
    sessions = [(_date(0), [c1, d1]), (_date(1), [c2, d2])]

    r1 = classify_history(sessions)
    r2 = classify_history(sessions)
    assert {d.isoformat(): [rec.to_dict() for rec in recs] for d, recs in r1.items()} == \
        {d.isoformat(): [rec.to_dict() for rec in recs] for d, recs in r2.items()}


# --------------------------------------------------------------------------- misc
def test_multiple_symbols_independent():
    a1 = make_candidate("AAA", _date(0))
    b1 = make_candidate("BBB", _date(0), families=("VOLUME", "STRUCTURE"))
    a2 = make_candidate("AAA", _date(1))
    result = classify_history([(_date(0), [a1, b1]), (_date(1), [a2])])
    assert _only(result, _date(1)).instrument == "AAA"
    assert _only(result, _date(1)).novelty_type == NoveltyType.CONTINUATION
    assert len(result[_date(0)]) == 2

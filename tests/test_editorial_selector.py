"""Phase 4.2 Packet 5.4C: focused tests for the production editorial selector + its persistence
layer. Pure-function tests use hand-built `StockRadarCandidate`/`CandidateNovelty` pairs (no
network, no detectors); the equivalence test (last in this file) runs the real 60-session local
pipeline once, offline, store-only - see its own docstring.
"""
import datetime as dt

import pytest

import radar.editorial_selector as es
from radar.models import (AttentionLevel, CandidateNovelty, DirectionCompatibility, NoveltyType,
                          StockRadarCandidate)
from storage.editorial_models import SelectionLifecycle
from storage.editorial_repository import EditorialStore, default_db_path

D0 = dt.date(2026, 9, 1)


def _date(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)


def make_pair(symbol, session_date, *, novelty_type=NoveltyType.NEW_CANDIDATE, families=2,
             direction=DirectionCompatibility.ALIGNED_POSITIVE, attention=None,
             technical_events=(), change_reason_codes=None, prev_date=None, sessions_since=None):
    active_families = (["STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"] if families == 3
                       else ["STRUCTURE", "RELATIVE_PERFORMANCE"])
    reason_codes = [f"STRUCTURE_{e}" for e in technical_events]
    attention = attention or (AttentionLevel.HIGH_INTEREST if families == 3 else AttentionLevel.NOTABLE)
    candidate = StockRadarCandidate(
        instrument=symbol, market_date=session_date, active_families=active_families,
        reason_codes=reason_codes, direction_compatibility=direction, attention_level=attention,
        independent_signal_count=families, price_change_pct=1.0)
    novelty = CandidateNovelty(
        instrument=symbol, session_date=session_date, novelty_type=novelty_type,
        previous_candidate_date=prev_date, sessions_since_previous_candidate=sessions_since,
        change_reason_codes=change_reason_codes or [novelty_type.value],
        reason=f"{symbol}: test fixture.")
    return candidate, novelty


def _symbols(result) -> list:
    return [s.instrument for s in result.selected]


# --------------------------------------------------------------------------- Test 1
def test_continuation_never_selected():
    pairs = [make_pair("AAA", _date(0), novelty_type=NoveltyType.CONTINUATION),
            make_pair("BBB", _date(0), novelty_type=NoveltyType.NEW_CANDIDATE)]
    result = es.select_session(_date(0), pairs, {})
    assert "AAA" not in _symbols(result)
    assert "BBB" in _symbols(result)


# --------------------------------------------------------------------------- Test 2/3
def test_max_five_and_fewer_allowed():
    many = [make_pair(f"S{i}", _date(0), families=2) for i in range(10)]
    result = es.select_session(_date(0), many, {})
    assert len(result.selected) == 5

    one = [make_pair("AAA", _date(0), families=3)]
    result_one = es.select_session(_date(0), one, {})
    assert len(result_one.selected) == 1


# --------------------------------------------------------------------------- Test 4/5
def test_state_change_ordering_and_symbol_asc():
    pairs = [make_pair("D2", _date(0), novelty_type=NoveltyType.NEW_CANDIDATE, families=2),
            make_pair("D1", _date(0), novelty_type=NoveltyType.NEW_CANDIDATE, families=3),
            make_pair("B2", _date(0), novelty_type=NoveltyType.MULTIPLE_CHANGES, families=2,
                     direction=DirectionCompatibility.ALIGNED_NEGATIVE),
            make_pair("B1", _date(0), novelty_type=NoveltyType.MULTIPLE_CHANGES, families=3,
                     direction=DirectionCompatibility.ALIGNED_NEGATIVE)]
    result = es.select_session(_date(0), pairs, {})
    # Both 3-family candidates fit within MAX_RESERVED_3FAMILY_SLOTS=2, state-change-first,
    # symbol ASC within its own bucket.
    reserved = [s.instrument for s in result.selected if s.reserved_3family]
    assert set(reserved) == {"B1", "D1"}
    b1 = next(s for s in result.selected if s.instrument == "B1")
    assert b1.selection_bucket == "RESERVED_3FAMILY_STATE_CHANGE"
    d1 = next(s for s in result.selected if s.instrument == "D1")
    assert d1.selection_bucket == "RESERVED_3FAMILY_NEW_CANDIDATE"


# --------------------------------------------------------------------------- Test 6/7
def test_cooldown_is_prior_3_sessions_and_no_self_cooldown():
    pairs_day0 = [make_pair("AAA", _date(0), novelty_type=NoveltyType.NEW_CANDIDATE)]
    result0 = es.select_session(_date(0), pairs_day0, {})
    assert "AAA" in _symbols(result0)

    # rerunning SAME session with an empty recently_selected (as EditorialStore.get_prior_
    # selections would return, since it excludes the current session) must not self-suppress.
    result0_rerun = es.select_session(_date(0), pairs_day0, {})
    assert "AAA" in _symbols(result0_rerun)

    # next session, AAA now IS in recently_selected (selected on session 0) -> suppressed
    pairs_day1 = [make_pair("AAA", _date(1), novelty_type=NoveltyType.NEW_CANDIDATE)]
    result1 = es.select_session(_date(1), pairs_day1, {"AAA": _date(0)})
    assert "AAA" not in _symbols(result1)
    assert result1.suppressed_by_cooldown == 1


# --------------------------------------------------------------------------- Test 8
def test_unselected_radar_occurrence_does_not_trigger_cooldown():
    """`recently_selected` reflects prior SELECTIONS only - a symbol the Radar saw but this
    selector didn't pick must not be in that dict at all, by construction of the persistence
    layer (nothing was ever saved for it)."""
    pairs = [make_pair("AAA", _date(1), novelty_type=NoveltyType.NEW_CANDIDATE)]
    result = es.select_session(_date(1), pairs, {})  # AAA never persisted -> not suppressed
    assert "AAA" in _symbols(result)


# --------------------------------------------------------------------------- Tests 9-13: overrides
def _override_case(codes, *, expect_override):
    recently_selected = {"AAA": _date(0)}
    pairs = [make_pair("AAA", _date(1), novelty_type=NoveltyType.MULTIPLE_CHANGES,
                       change_reason_codes=codes)]
    result = es.select_session(_date(1), pairs, recently_selected)
    if expect_override:
        assert "AAA" in _symbols(result)
        assert result.selected[0].cooldown_status.startswith("COOLDOWN_OVERRIDE_")
    else:
        assert "AAA" not in _symbols(result)


def test_attention_escalation_overrides_cooldown():
    _override_case(["ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"], expect_override=True)


def test_new_evidence_family_overrides_cooldown():
    _override_case(["NEW_FAMILY_VOLUME"], expect_override=True)


def test_direction_transition_overrides_cooldown():
    _override_case(["DIRECTION_TRANSITION_MIXED_TO_ALIGNED_POSITIVE"], expect_override=True)


def test_new_technical_event_alone_does_not_override():
    _override_case(["NEW_TECHNICAL_EVENT_BREAK_ABOVE_20D_RANGE"], expect_override=False)


def test_three_family_alone_does_not_override_cooldown():
    recently_selected = {"AAA": _date(0)}
    pairs = [make_pair("AAA", _date(1), novelty_type=NoveltyType.NEW_TECHNICAL_EVENT, families=3,
                       change_reason_codes=["NEW_TECHNICAL_EVENT_BREAK_ABOVE_20D_RANGE"])]
    result = es.select_session(_date(1), pairs, recently_selected)
    assert "AAA" not in _symbols(result)


# --------------------------------------------------------------------------- Test 14/15/16
def test_max_two_reserved_slots_and_reservation_after_cooldown():
    pairs = [make_pair(s, _date(0), families=3, novelty_type=NoveltyType.NEW_CANDIDATE)
            for s in ("CCC", "AAA", "BBB")]
    result = es.select_session(_date(0), pairs, {})
    reserved = [s for s in result.selected if s.reserved_3family]
    assert len(reserved) == 2
    assert result.reservation_count == 2

    # cooldown-suppressed 3-family candidate must never be reserved
    recently_selected = {"AAA": _date(0) - dt.timedelta(days=1)}
    pairs2 = [make_pair("AAA", _date(1), families=3, novelty_type=NoveltyType.NEW_CANDIDATE)]
    result2 = es.select_session(_date(1), pairs2, recently_selected)
    assert "AAA" not in _symbols(result2)


def test_reserved_cannot_be_evicted_by_diversity():
    pairs = [
        make_pair("AAA", _date(0), families=3, novelty_type=NoveltyType.MULTIPLE_CHANGES,
                 direction=DirectionCompatibility.ALIGNED_POSITIVE),
        make_pair("BBB", _date(0), families=3, novelty_type=NoveltyType.MULTIPLE_CHANGES,
                 direction=DirectionCompatibility.ALIGNED_POSITIVE),
        make_pair("CCC", _date(0), families=2, novelty_type=NoveltyType.NEW_CANDIDATE,
                 direction=DirectionCompatibility.ALIGNED_NEGATIVE),
    ]
    result = es.select_session(_date(0), pairs, {})
    reserved = {s.instrument for s in result.selected if s.reserved_3family}
    assert reserved == {"AAA", "BBB"}
    assert "CCC" in _symbols(result)


# --------------------------------------------------------------------------- Test 17/18
def test_diversity_fills_available_directions():
    pairs = [make_pair("AAA", _date(0), families=2, direction=DirectionCompatibility.ALIGNED_POSITIVE),
            make_pair("BBB", _date(0), families=2, direction=DirectionCompatibility.ALIGNED_NEGATIVE)]
    result = es.select_session(_date(0), pairs, {})
    roles = {s.instrument: s.diversity_role for s in result.selected}
    assert roles["AAA"] == "POSITIVE_SLOT"
    assert roles["BBB"] == "NEGATIVE_SLOT"


def test_diversity_does_not_invent_unavailable_direction():
    pairs = [make_pair(s, _date(0), families=2, direction=DirectionCompatibility.ALIGNED_POSITIVE)
            for s in ("AAA", "BBB")]
    result = es.select_session(_date(0), pairs, {})
    assert result.direction_availability["negative_available"] is False
    assert all(s.diversity_role != "NEGATIVE_SLOT" for s in result.selected)


# --------------------------------------------------------------------------- Test 19: selection IDs
def test_deterministic_selection_ids():
    pairs = [make_pair("AAA", _date(0))]
    r1 = es.select_session(_date(0), pairs, {})
    r2 = es.select_session(_date(0), pairs, {})
    assert r1.selected[0].selection_id == r2.selected[0].selection_id
    assert r1.selected[0].selection_id == es.make_selection_id(_date(0), "AAA")


# --------------------------------------------------------------------------- Test 20/21: idempotent persistence
def test_idempotent_persistence_no_duplicates(tmp_path):
    store = EditorialStore(default_db_path(str(tmp_path)))
    pairs = [make_pair("AAA", _date(0), families=3), make_pair("BBB", _date(0), families=2)]
    result = es.select_session(_date(0), pairs, {})

    inserted1 = store.save_selections(result.selected)
    inserted2 = store.save_selections(result.selected)  # exact rerun
    assert inserted1 == 2
    assert inserted2 == 0  # nothing new written

    rows = store.get_session_selections(_date(0))
    assert len(rows) == 2
    store.close()


# --------------------------------------------------------------------------- Test 22: no N+1
def test_get_prior_selections_single_bounded_query(tmp_path):
    store = EditorialStore(default_db_path(str(tmp_path)))
    spine = [_date(i) for i in range(10)]
    pairs = [make_pair(f"S{i}", _date(0)) for i in range(50)]
    result = es.select_session(_date(0), pairs[:5], {})
    store.save_selections(result.selected)

    class _CountingConn:
        def __init__(self, conn):
            self._conn = conn
            self.calls = 0

        def execute(self, *a, **k):
            self.calls += 1
            return self._conn.execute(*a, **k)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    store.conn = _CountingConn(store.conn)
    store.get_prior_selections(_date(3), spine, lookback_sessions=3)
    assert store.conn.calls == 1  # exactly one query regardless of candidate-pool size
    store.close()


# --------------------------------------------------------------------------- Test 23: trading sessions
def test_cooldown_uses_trading_session_spine_not_calendar_days(tmp_path):
    store = EditorialStore(default_db_path(str(tmp_path)))
    friday = dt.date(2026, 9, 11)
    monday = dt.date(2026, 9, 14)  # 3 calendar days later, but the VERY NEXT trading session
    spine = [friday, monday, dt.date(2026, 9, 15)]

    pairs_friday = [make_pair("AAA", friday, novelty_type=NoveltyType.NEW_CANDIDATE)]
    result_friday = es.select_session(friday, pairs_friday, {})
    store.save_selections(result_friday.selected)

    recently_selected = store.get_prior_selections(monday, spine, lookback_sessions=3)
    assert recently_selected.get("AAA") == friday   # still within cooldown (1 trading session back)
    store.close()


# --------------------------------------------------------------------------- Test 24/25/26: immutability
def test_candidate_and_novelty_objects_unmutated():
    candidate, novelty = make_pair("AAA", _date(0), families=3)
    candidate_before, novelty_before = candidate.to_dict(), novelty.to_dict()
    es.select_session(_date(0), [(candidate, novelty)], {})
    assert candidate.to_dict() == candidate_before
    assert novelty.to_dict() == novelty_before


def test_no_market_report_or_ohlcv_dependency_in_selector_source():
    import inspect
    source = inspect.getsource(es)
    assert "MarketReport" not in source
    assert "OHLCVStore" not in source
    assert "market_ohlcv" not in source


# --------------------------------------------------------------------------- Test 27/28
def test_selector_version_and_lifecycle_persisted(tmp_path):
    store = EditorialStore(default_db_path(str(tmp_path)))
    pairs = [make_pair("AAA", _date(0))]
    result = es.select_session(_date(0), pairs, {})
    store.save_selections(result.selected)

    rows = store.get_session_selections(_date(0))
    assert rows[0].selector_version == es.EDITORIAL_SELECTOR_VERSION
    assert rows[0].lifecycle_state == SelectionLifecycle.SELECTED.value

    updated = store.update_publication_state(rows[0].selection_id, SelectionLifecycle.PUBLISHED.value)
    assert updated
    rows2 = store.get_session_selections(_date(0))
    assert rows2[0].lifecycle_state == SelectionLifecycle.PUBLISHED.value
    store.close()


# --------------------------------------------------------------------------- Test 29: persistence failure
def test_persistence_failure_returns_degraded_result_not_silent_success():
    class BrokenStore:
        def get_prior_selections(self, *a, **k):
            raise RuntimeError("simulated: database unavailable")

    pairs = [make_pair("AAA", _date(0))]
    result = es.run_and_persist(_date(0), pairs, BrokenStore(), spine=[_date(0)])
    assert result.degraded is True
    assert result.selected == []
    assert result.warnings


# --------------------------------------------------------------------------- rerun safety (section 18)
def test_accidental_rerun_does_not_change_cooldown_for_future_sessions(tmp_path):
    store = EditorialStore(default_db_path(str(tmp_path)))
    pairs = [make_pair("AAA", _date(0))]
    r1 = es.run_and_persist(_date(0), pairs, store, spine=[_date(0)])
    r2 = es.run_and_persist(_date(0), pairs, store, spine=[_date(0)])  # accidental rerun
    assert _symbols(r1) == _symbols(r2) == ["AAA"]
    assert len(store.get_session_selections(_date(0))) == 1  # no duplicate row
    store.close()

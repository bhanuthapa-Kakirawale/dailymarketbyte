"""Phase 4.2 Packet 5.4B.1: hybrid editorial policy simulation (Policy E/F). Fully offline and
pure - reuses `tests/test_editorial_policy.py::make_record`'s exact record shape.
"""
import copy
import datetime as dt

import radar.editorial_policy as ep
import radar.editorial_policy_hybrid as eph
from test_editorial_policy import make_record

D0 = dt.date(2026, 9, 1)


def _date(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)


# --------------------------------------------------------------------------- Tests 1-3: reservation count
def test_e_reserves_zero_when_no_3family():
    records = [make_record("AAA", _date(0), families=2)]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    assert not any(s["reserved_3family"] for s in stories)


def test_e_reserves_one_when_exactly_one_3family():
    records = [make_record("AAA", _date(0), families=3), make_record("BBB", _date(0), families=2)]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    reserved = [s for s in stories if s["reserved_3family"]]
    assert len(reserved) == 1
    assert reserved[0]["symbol"] == "AAA"


def test_e_reserves_max_two_when_three_or_more_3family():
    records = [make_record(s, _date(0), families=3) for s in ("CCC", "AAA", "BBB")]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    reserved = [s for s in stories if s["reserved_3family"]]
    assert len(reserved) == 2
    # third 3-family candidate still eligible for the normal fill step (packet spec section 4)
    assert {s["symbol"] for s in stories} == {"AAA", "BBB", "CCC"}


# --------------------------------------------------------------------------- Test 4/5: reserved ordering
def test_reserved_state_change_before_new_candidate():
    records = [make_record("ZZZ", _date(0), families=3, novelty_type="NEW_CANDIDATE"),
              make_record("AAA", _date(0), families=3, novelty_type="MULTIPLE_CHANGES")]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    reserved = [s for s in stories if s["reserved_3family"]]
    assert [s["symbol"] for s in reserved] == ["AAA", "ZZZ"]
    assert reserved[0]["selection_bucket"] == "RESERVED_3FAMILY_STATE_CHANGE"
    assert reserved[1]["selection_bucket"] == "RESERVED_3FAMILY_NEW_CANDIDATE"


def test_reserved_symbol_asc_within_bucket():
    records = [make_record("ZZZ", _date(0), families=3, novelty_type="MULTIPLE_CHANGES"),
              make_record("AAA", _date(0), families=3, novelty_type="MULTIPLE_CHANGES")]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    reserved = [s for s in stories if s["reserved_3family"]]
    assert [s["symbol"] for s in reserved] == ["AAA", "ZZZ"]


# --------------------------------------------------------------------------- Test 6/7/8: cooldown before reservation
def test_reservation_happens_after_cooldown():
    """A 3-family candidate suppressed by cooldown (no override) must never be reserved."""
    history = {"AAA": 0}
    day1 = [make_record("AAA", _date(1), families=3, novelty_type="NEW_CANDIDATE"),
           make_record("BBB", _date(1), families=2, novelty_type="NEW_CANDIDATE")]
    stories, suppressed = eph.select_policy_e(ep.eligible_pool(day1), 1, history)
    assert all(s["symbol"] != "AAA" for s in stories)
    assert any(s["symbol"] == "AAA" for s in suppressed)


def test_three_family_status_alone_does_not_override_cooldown():
    history = {"AAA": 0}
    day1 = [make_record("AAA", _date(1), families=3, novelty_type="NEW_TECHNICAL_EVENT",
                        change_reason_codes=["NEW_TECHNICAL_EVENT_BREAK_ABOVE_20D_RANGE"])]
    stories, suppressed = eph.select_policy_e(ep.eligible_pool(day1), 1, history)
    assert all(s["symbol"] != "AAA" for s in stories)
    assert suppressed


def test_existing_cooldown_override_still_works_for_reserved_candidate():
    history = {"AAA": 0}
    day1 = [make_record("AAA", _date(1), families=3, novelty_type="MULTIPLE_CHANGES",
                        change_reason_codes=["ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"])]
    stories, suppressed = eph.select_policy_e(ep.eligible_pool(day1), 1, history)
    assert suppressed == []
    reserved = [s for s in stories if s["reserved_3family"]]
    assert any(s["symbol"] == "AAA" for s in reserved)
    assert reserved[0]["cooldown_status"].startswith("COOLDOWN_OVERRIDE_")


# --------------------------------------------------------------------------- Test 9: no eviction by diversity
def test_reserved_story_cannot_be_evicted_by_diversity():
    # Two reserved 3-family candidates, both ALIGNED_POSITIVE; remaining pool has an
    # ALIGNED_NEGATIVE 2-family candidate. Diversity must fill around, never replace reserved.
    records = [
        make_record("AAA", _date(0), families=3, novelty_type="MULTIPLE_CHANGES", direction="ALIGNED_POSITIVE"),
        make_record("BBB", _date(0), families=3, novelty_type="MULTIPLE_CHANGES", direction="ALIGNED_POSITIVE"),
        make_record("CCC", _date(0), families=2, novelty_type="NEW_CANDIDATE", direction="ALIGNED_NEGATIVE"),
    ]
    stories, _, diag = eph.select_policy_f(ep.eligible_pool(records), 0, {})
    reserved_symbols = {s["symbol"] for s in stories if s["reserved_3family"]}
    assert reserved_symbols == {"AAA", "BBB"}
    assert any(s["symbol"] == "CCC" for s in stories)  # negative still fills a remaining slot


# --------------------------------------------------------------------------- Test 10/11: F diversity fill
def test_f_uses_remaining_slots_for_opposite_direction():
    records = [make_record("AAA", _date(0), families=2, direction="ALIGNED_POSITIVE"),
              make_record("BBB", _date(0), families=2, direction="ALIGNED_NEGATIVE")]
    stories, _, diag = eph.select_policy_f(ep.eligible_pool(records), 0, {})
    roles = {s["symbol"]: s["diversity_role"] for s in stories}
    assert roles["AAA"] == "POSITIVE_SLOT"
    assert roles["BBB"] == "NEGATIVE_SLOT"
    assert diag["positive_selected"] and diag["negative_selected"]


def test_f_does_not_invent_unavailable_direction():
    records = [make_record(s, _date(0), families=2, direction="ALIGNED_POSITIVE") for s in ("AAA", "BBB", "CCC")]
    stories, _, diag = eph.select_policy_f(ep.eligible_pool(records), 0, {})
    assert diag["negative_available"] is False
    assert diag["negative_selected"] is False
    assert all(s["diversity_role"] != "NEGATIVE_SLOT" for s in stories)


# --------------------------------------------------------------------------- Test 12/13: capacity
def test_max_stories_never_exceeds_five():
    records = [make_record(f"S{i}", _date(0), families=(3 if i < 4 else 2)) for i in range(20)]
    pool = ep.eligible_pool(records)
    assert len(eph.select_policy_e(pool, 0, {})[0]) <= ep.MAX_STORIES_PER_SESSION
    assert len(eph.select_policy_f(pool, 0, {})[0]) <= ep.MAX_STORIES_PER_SESSION


def test_fewer_than_five_allowed():
    records = [make_record("AAA", _date(0), families=3)]
    pool = ep.eligible_pool(records)
    assert len(eph.select_policy_e(pool, 0, {})[0]) == 1
    assert len(eph.select_policy_f(pool, 0, {})[0]) == 1


# --------------------------------------------------------------------------- Test 14/15: no price/RVOL influence
def test_price_move_does_not_affect_reserved_ordering():
    records = [make_record("ZZZ", _date(0), families=3, price_change_pct=99.0),
              make_record("AAA", _date(0), families=3, price_change_pct=0.001)]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    reserved = [s for s in stories if s["reserved_3family"]]
    assert [s["symbol"] for s in reserved] == ["AAA", "ZZZ"]


def test_rvol_does_not_affect_reserved_ordering():
    records = [make_record("ZZZ", _date(0), families=3, rvol=99.0),
              make_record("AAA", _date(0), families=3, rvol=0.001)]
    stories, _ = eph.select_policy_e(ep.eligible_pool(records), 0, {})
    reserved = [s for s in stories if s["reserved_3family"]]
    assert [s["symbol"] for s in reserved] == ["AAA", "ZZZ"]


# --------------------------------------------------------------------------- Test 16: no future leakage
def test_future_session_cannot_affect_earlier_selection():
    day0 = [make_record("AAA", _date(0), families=3, novelty_type="NEW_CANDIDATE")]
    day1 = [make_record("AAA", _date(1), families=3, novelty_type="MULTIPLE_CHANGES",
                        change_reason_codes=["ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"])]
    full = eph.simulate_hybrid_history([(_date(0), day0), (_date(1), day1)])
    prefix = eph.simulate_hybrid_history([(_date(0), day0)])
    for policy in eph.ALL_HYBRID_POLICIES:
        assert full["stories"][policy][_date(0)] == prefix["stories"][policy][_date(0)]


# --------------------------------------------------------------------------- Test 17/18: no mutation
def test_raw_and_novelty_records_unchanged_after_simulation():
    records = [make_record("AAA", _date(0), families=3), make_record("BBB", _date(0), families=2)]
    before = copy.deepcopy(records)
    eph.simulate_hybrid_history([(_date(0), records)])
    assert records == before


# --------------------------------------------------------------------------- Test 19: C/D unchanged
def test_policy_c_and_d_unchanged():
    """Re-runs Packet 5.4B's own scenarios directly against `radar.editorial_policy` (never
    touched by this packet) to prove Policies C/D behave identically - this module adds code,
    it never edits `radar/editorial_policy.py`."""
    records = [make_record("AAA", _date(0), families=3, novelty_type="NEW_CANDIDATE"),
              make_record("BBB", _date(0), families=2, novelty_type="MULTIPLE_CHANGES"),
              make_record("CCC", _date(0), families=2, novelty_type="NEW_CANDIDATE")]
    pool = ep.eligible_pool(records)
    stories_c, _ = ep.select_policy_c(pool, 0, {})
    stories_d, _ = ep.select_policy_d(pool, 0, {})
    assert [s["symbol"] for s in stories_c] == ["BBB", "AAA", "CCC"]
    assert {s["symbol"] for s in stories_d} == {"BBB", "AAA", "CCC"}
    assert not any("reserved_3family" in s for s in stories_c)
    assert not any("reserved_3family" in s for s in stories_d)


# --------------------------------------------------------------------------- Test 20: determinism
def test_repeated_simulation_deterministic():
    records0 = [make_record("AAA", _date(0), families=3), make_record("BBB", _date(0), families=2)]
    records1 = [make_record("AAA", _date(1), families=3, novelty_type="MULTIPLE_CHANGES",
                            change_reason_codes=["NEW_FAMILY_VOLUME"])]
    sessions = [(_date(0), records0), (_date(1), records1)]
    r1 = eph.simulate_hybrid_history(sessions)
    r2 = eph.simulate_hybrid_history(sessions)
    assert r1 == r2

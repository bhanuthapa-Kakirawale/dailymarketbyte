"""Phase 4.2 Packet 5.4B: deterministic editorial policy simulation. Fully offline and pure -
input is hand-built novelty-record dicts (the exact shape `radar.novelty_validation.
combined_records` produces), no network, no database.
"""
import copy
import datetime as dt

import radar.editorial_policy as ep

D0 = dt.date(2026, 9, 1)


def _date(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)


def make_record(symbol, session_date, *, novelty_type="NEW_CANDIDATE", families=3,
                price_change_pct=1.0, direction="ALIGNED_POSITIVE", outside_top5=True,
                change_reason_codes=None, rvol=None):
    active_families = (["STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"] if families == 3
                       else ["STRUCTURE", "RELATIVE_PERFORMANCE"])
    return {
        "date": session_date.isoformat(), "symbol": symbol, "price_change_pct": price_change_pct,
        "independent_signal_count": families, "active_families": active_families, "rvol": rvol,
        "structural_events": [], "direction_compatibility": direction,
        "attention_level": "HIGH_INTEREST" if families == 3 else "NOTABLE",
        "outside_top5_movers": outside_top5, "evidence": [], "why_radar_noticed_it": "",
        "novelty_type": novelty_type, "previous_candidate_date": None,
        "sessions_since_previous_candidate": None, "new_families": [], "lost_families": [],
        "new_technical_events": [], "previous_persistence": None, "current_persistence": None,
        "previous_direction": None, "current_direction": direction, "previous_attention": None,
        "current_attention": None,
        "novelty_change_reason_codes": change_reason_codes or [novelty_type],
        "novelty_reason": f"{symbol}: test fixture.",
    }


# --------------------------------------------------------------------------- Test 1
def test_continuation_never_selected():
    pool_records = [make_record("AAA", _date(0), novelty_type="CONTINUATION"),
                    make_record("BBB", _date(0), novelty_type="NEW_CANDIDATE")]
    for policy_fn in (ep.select_policy_a, ep.select_policy_b):
        pool = ep.eligible_pool(pool_records)
        stories = policy_fn(pool)
        assert all(s["symbol"] != "AAA" for s in stories)
        assert any(s["symbol"] == "BBB" for s in stories)
    stories_c, _ = ep.select_policy_c(ep.eligible_pool(pool_records), 0, {})
    stories_d, _ = ep.select_policy_d(ep.eligible_pool(pool_records), 0, {})
    assert all(s["symbol"] != "AAA" for s in stories_c + stories_d)


# --------------------------------------------------------------------------- Test 2
def test_policy_a_three_family_before_two_family():
    records = [make_record("ZZZ", _date(0), families=3), make_record("AAA", _date(0), families=2)]
    stories = ep.select_policy_a(ep.eligible_pool(records))
    assert [s["symbol"] for s in stories] == ["ZZZ", "AAA"]
    assert stories[0]["selection_bucket"] == "3_FAMILY"
    assert stories[1]["selection_bucket"] == "2_FAMILY"


# --------------------------------------------------------------------------- Test 3
def test_policy_b_bucket_ordering():
    records = [
        make_record("D2", _date(0), novelty_type="NEW_CANDIDATE", families=2),
        make_record("D1", _date(0), novelty_type="NEW_CANDIDATE", families=3),
        make_record("B2", _date(0), novelty_type="MULTIPLE_CHANGES", families=2),
        make_record("B1", _date(0), novelty_type="MULTIPLE_CHANGES", families=3),
    ]
    stories = ep.select_policy_b(ep.eligible_pool(records))
    assert [s["symbol"] for s in stories] == ["B1", "B2", "D1", "D2"]
    assert [s["selection_bucket"] for s in stories] == [
        "STATE_CHANGE_3_FAMILY", "STATE_CHANGE_2_FAMILY", "NEW_CANDIDATE_3_FAMILY", "NEW_CANDIDATE_2_FAMILY"]


# --------------------------------------------------------------------------- Test 4
def test_policy_c_suppresses_recently_selected_symbol():
    day0 = [make_record("AAA", _date(0), novelty_type="NEW_CANDIDATE")]
    day1 = [make_record("AAA", _date(1), novelty_type="NEW_CANDIDATE")]

    history = {}
    stories0, _ = ep.select_policy_c(ep.eligible_pool(day0), 0, history)
    for s in stories0:
        history[s["symbol"]] = 0
    assert any(s["symbol"] == "AAA" for s in stories0)

    stories1, suppressed1 = ep.select_policy_c(ep.eligible_pool(day1), 1, history)
    assert all(s["symbol"] != "AAA" for s in stories1)
    assert any(s["symbol"] == "AAA" for s in suppressed1)


# --------------------------------------------------------------------------- Test 5
def test_policy_c_unselected_raw_occurrence_does_not_start_cooldown():
    """A symbol that was ELIGIBLE (raw candidate) but did not make the top-5 cut must not be
    treated as 'published' - cooldown tracks selection, never raw occurrence."""
    # 6 candidates on day 0 -> only 5 fit; the 6th (by policy B order) is not selected.
    day0 = [make_record(f"S{i}", _date(0), novelty_type="NEW_CANDIDATE", families=2)
           for i in range(6)]
    history = {}
    stories0, _ = ep.select_policy_c(ep.eligible_pool(day0), 0, history)
    assert len(stories0) == 5
    not_selected = {r["symbol"] for r in day0} - {s["symbol"] for s in stories0}
    assert len(not_selected) == 1
    for s in stories0:
        history[s["symbol"]] = 0

    leftover_symbol = next(iter(not_selected))
    day1 = [make_record(leftover_symbol, _date(1), novelty_type="NEW_CANDIDATE")]
    stories1, suppressed1 = ep.select_policy_c(ep.eligible_pool(day1), 1, history)
    assert any(s["symbol"] == leftover_symbol for s in stories1)
    assert suppressed1 == []


# --------------------------------------------------------------------------- Tests 6-9: overrides
def _cooldown_case(trigger_codes, *, expect_override: bool):
    day0 = [make_record("AAA", _date(0), novelty_type="NEW_CANDIDATE")]
    history = {"AAA": 0}  # AAA already published on session 0
    day1 = [make_record("AAA", _date(1), novelty_type="MULTIPLE_CHANGES",
                        change_reason_codes=trigger_codes)]
    stories1, suppressed1 = ep.select_policy_c(ep.eligible_pool(day1), 1, history)
    if expect_override:
        assert any(s["symbol"] == "AAA" for s in stories1)
        assert suppressed1 == []
    else:
        assert all(s["symbol"] != "AAA" for s in stories1)
        assert any(s["symbol"] == "AAA" for s in suppressed1)


def test_cooldown_override_attention_escalation():
    _cooldown_case(["ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"], expect_override=True)


def test_cooldown_override_new_evidence_family():
    _cooldown_case(["NEW_FAMILY_VOLUME"], expect_override=True)


def test_cooldown_override_direction_transition():
    _cooldown_case(["DIRECTION_TRANSITION_MIXED_TO_ALIGNED_POSITIVE"], expect_override=True)


def test_new_technical_event_alone_does_not_override_cooldown():
    _cooldown_case(["NEW_TECHNICAL_EVENT_BREAK_ABOVE_20D_RANGE"], expect_override=False)


# --------------------------------------------------------------------------- Test 10
def test_cooldown_uses_trading_session_index_not_calendar_days():
    """Sessions 3 calendar-index apart but only 1 trading-session apart (a holiday/weekend gap
    the caller's list already skips) must behave exactly like any other adjacent pair."""
    monday = dt.date(2026, 9, 14)
    friday_before = dt.date(2026, 9, 11)
    history = {"AAA": 0}  # published at trading-session index 0 (friday_before)
    day1 = [make_record("AAA", monday, novelty_type="NEW_CANDIDATE")]
    stories1, suppressed1 = ep.select_policy_c(ep.eligible_pool(day1), 1, history)
    assert all(s["symbol"] != "AAA" for s in stories1)  # still within 3-session cooldown
    assert any(s["symbol"] == "AAA" for s in suppressed1)


def test_cooldown_boundary_exactly_three_sessions_still_suppressed():
    history = {"AAA": 0}
    day3 = [make_record("AAA", _date(3), novelty_type="NEW_CANDIDATE")]
    stories, suppressed = ep.select_policy_c(ep.eligible_pool(day3), 3, history)
    assert all(s["symbol"] != "AAA" for s in stories)
    assert suppressed


def test_cooldown_expires_after_four_sessions():
    history = {"AAA": 0}
    day4 = [make_record("AAA", _date(4), novelty_type="NEW_CANDIDATE")]
    stories, suppressed = ep.select_policy_c(ep.eligible_pool(day4), 4, history)
    assert any(s["symbol"] == "AAA" for s in stories)
    assert suppressed == []


# --------------------------------------------------------------------------- Test 11/12
def test_policy_d_deterministic():
    records = [make_record("AAA", _date(0), direction="ALIGNED_POSITIVE"),
              make_record("BBB", _date(0), direction="ALIGNED_NEGATIVE"),
              make_record("CCC", _date(0), direction="MIXED")]
    s1, _ = ep.select_policy_d(ep.eligible_pool(records), 0, {})
    s2, _ = ep.select_policy_d(ep.eligible_pool(records), 0, {})
    assert s1 == s2
    assert s1[0]["symbol"] == "AAA" and s1[0]["diversity_role"] == "FIRST_POSITIVE"
    assert s1[1]["symbol"] == "BBB" and s1[1]["diversity_role"] == "FIRST_NEGATIVE"


def test_policy_d_does_not_invent_unavailable_direction():
    """Every candidate is ALIGNED_POSITIVE - Policy D must not fabricate a negative story."""
    records = [make_record(f"S{i}", _date(0), direction="ALIGNED_POSITIVE") for i in range(3)]
    stories, _ = ep.select_policy_d(ep.eligible_pool(records), 0, {})
    assert all(s["direction_compatibility"] == "ALIGNED_POSITIVE" for s in stories)
    assert stories[0]["diversity_role"] == "FIRST_POSITIVE"
    assert all(s["diversity_role"] != "FIRST_NEGATIVE" for s in stories)


# --------------------------------------------------------------------------- Test 13/14
def test_max_stories_never_exceeds_five():
    records = [make_record(f"S{i}", _date(0), families=2) for i in range(20)]
    for fn in (lambda p: ep.select_policy_a(p), lambda p: ep.select_policy_b(p),
              lambda p: ep.select_policy_c(p, 0, {})[0], lambda p: ep.select_policy_d(p, 0, {})[0]):
        stories = fn(ep.eligible_pool(records))
        assert len(stories) <= ep.MAX_STORIES_PER_SESSION


def test_fewer_than_five_allowed_no_backfill():
    records = [make_record("AAA", _date(0))]
    for fn in (ep.select_policy_a, ep.select_policy_b):
        stories = fn(ep.eligible_pool(records))
        assert len(stories) == 1
    assert len(ep.select_policy_c(ep.eligible_pool(records), 0, {})[0]) == 1
    assert len(ep.select_policy_d(ep.eligible_pool(records), 0, {})[0]) == 1


# --------------------------------------------------------------------------- Test 15/16
def test_price_change_does_not_affect_tie_break():
    records = [make_record("ZZZ", _date(0), families=2, price_change_pct=50.0),
              make_record("AAA", _date(0), families=2, price_change_pct=0.01)]
    stories = ep.select_policy_a(ep.eligible_pool(records))
    assert [s["symbol"] for s in stories] == ["AAA", "ZZZ"]


def test_rvol_does_not_affect_tie_break():
    records = [make_record("ZZZ", _date(0), families=2, rvol=50.0),
              make_record("AAA", _date(0), families=2, rvol=0.5)]
    stories = ep.select_policy_a(ep.eligible_pool(records))
    assert [s["symbol"] for s in stories] == ["AAA", "ZZZ"]


# --------------------------------------------------------------------------- Test 17
def test_future_session_cannot_affect_earlier_selection():
    day0 = [make_record("AAA", _date(0), novelty_type="NEW_CANDIDATE")]
    day1 = [make_record("AAA", _date(1), novelty_type="MULTIPLE_CHANGES",
                        change_reason_codes=["ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"])]

    sessions_full = [(_date(0), day0), (_date(1), day1)]
    sessions_prefix = [(_date(0), day0)]

    full = ep.simulate_history(sessions_full)
    prefix = ep.simulate_history(sessions_prefix)
    for policy in ep.ALL_POLICIES:
        assert full["stories"][policy][_date(0)] == prefix["stories"][policy][_date(0)]


# --------------------------------------------------------------------------- Test 18/19
def test_raw_records_unchanged_after_simulation():
    records = [make_record("AAA", _date(0)), make_record("BBB", _date(0), families=2)]
    before = copy.deepcopy(records)
    ep.simulate_history([(_date(0), records)])
    assert records == before


# --------------------------------------------------------------------------- Test 20
def test_repeated_simulation_deterministic():
    records0 = [make_record("AAA", _date(0)), make_record("BBB", _date(0), families=2)]
    records1 = [make_record("AAA", _date(1), novelty_type="MULTIPLE_CHANGES",
                            change_reason_codes=["NEW_FAMILY_VOLUME"])]
    sessions = [(_date(0), records0), (_date(1), records1)]

    r1 = ep.simulate_history(sessions)
    r2 = ep.simulate_history(sessions)
    assert r1 == r2

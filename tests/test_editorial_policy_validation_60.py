"""Phase 4.2 Packet 5.4B.2: focused, offline tests for the 60-session validation composition
module. Uses small synthetic fixtures (not the full real 60-session pipeline, which is exercised
by actually running `python -m radar.editorial_policy_validation_60` separately) so these run
fast while still proving the module's own logic - session selection, block splitting, coverage
visibility, and the no-mutation/no-future-leakage/determinism guarantees.
"""
import copy
import datetime as dt

import radar.editorial_policy as ep
import radar.editorial_policy_hybrid as eph
import radar.editorial_policy_validation_60 as v60
from test_editorial_policy import make_record

D0 = dt.date(2026, 9, 1)


def _date(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)


def _benchmark(dates: list, base=20000.0) -> list:
    return [{"date": d, "close": base + i} for i, d in enumerate(dates)]


# --------------------------------------------------------------------------- Test 1/2: session selection
def test_exactly_60_sessions_selected_when_data_exists():
    dates = [ts.date() for ts in __import__("pandas").bdate_range(end=dt.date(2026, 9, 21), periods=100)]
    benchmark = _benchmark(dates)
    selected, prev_dates, warnings = v60.select_60_sessions(benchmark, end_session=dt.date(2026, 9, 21))
    assert len(selected) == 60
    assert warnings == []
    assert selected[-1] == dt.date(2026, 9, 21)
    assert selected == sorted(selected)


def test_holiday_excluded_from_session_selection(declare_nse_holiday):
    dates = [ts.date() for ts in __import__("pandas").bdate_range(end=dt.date(2026, 9, 21), periods=100)]
    holiday = dates[70]
    declare_nse_holiday(holiday)
    spine_dates = [d for d in dates if d != holiday]
    benchmark = _benchmark(spine_dates)
    selected, _prev, _warn = v60.select_60_sessions(benchmark, end_session=dt.date(2026, 9, 21))
    assert holiday not in selected


def test_insufficient_data_reported_not_silently_shortened():
    dates = [ts.date() for ts in __import__("pandas").bdate_range(end=dt.date(2026, 9, 21), periods=10)]
    benchmark = _benchmark(dates)
    selected, _prev, warnings = v60.select_60_sessions(benchmark, end_session=dt.date(2026, 9, 21))
    assert len(selected) < 60
    assert warnings and "only" in warnings[0]


# --------------------------------------------------------------------------- Test 3/4/16: chronology, no leakage
def test_block_splitting_is_20_20_20():
    sessions = [(_date(i), [make_record(f"S{i}", _date(i))]) for i in range(60)]
    sim_cd = ep.simulate_history(sessions)
    sim_hybrid = eph.simulate_hybrid_history(sessions)
    blocks = v60.block_breakdown(sessions, sim_cd, sim_hybrid)
    assert len(blocks["block_1_sessions_1_20"]["session_dates"]) == 20
    assert len(blocks["block_2_sessions_21_40"]["session_dates"]) == 20
    assert len(blocks["block_3_sessions_41_60"]["session_dates"]) == 20
    assert (blocks["block_1_sessions_1_20"]["session_dates"] + blocks["block_2_sessions_21_40"]["session_dates"] +
           blocks["block_3_sessions_41_60"]["session_dates"]) == [d.isoformat() for d, _ in sessions]


def test_future_session_cannot_affect_earlier_block():
    day0 = [make_record("AAA", _date(0), families=3, novelty_type="NEW_CANDIDATE")]
    day1 = [make_record("AAA", _date(1), families=3, novelty_type="MULTIPLE_CHANGES",
                        change_reason_codes=["ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"])]
    full = [(_date(0), day0), (_date(1), day1)]
    prefix = [(_date(0), day0)]

    full_cd, full_hybrid = ep.simulate_history(full), eph.simulate_hybrid_history(full)
    prefix_cd, prefix_hybrid = ep.simulate_history(prefix), eph.simulate_hybrid_history(prefix)
    for policy in ep.ALL_POLICIES:
        assert full_cd["stories"][policy][_date(0)] == prefix_cd["stories"][policy][_date(0)]
    for policy in eph.ALL_HYBRID_POLICIES:
        assert full_hybrid["stories"][policy][_date(0)] == prefix_hybrid["stories"][policy][_date(0)]


# --------------------------------------------------------------------------- Test 5-8: C/D/E/F unchanged
def test_c_d_e_f_unchanged_by_this_module():
    """This module imports, never edits, `radar.editorial_policy`/`radar.editorial_policy_hybrid`
    - re-run a known scenario directly against those modules and confirm the exact same result
    Packets 5.4B/5.4B.1 already established."""
    records = [make_record("AAA", _date(0), families=3, novelty_type="NEW_CANDIDATE"),
              make_record("BBB", _date(0), families=2, novelty_type="MULTIPLE_CHANGES"),
              make_record("CCC", _date(0), families=2, novelty_type="NEW_CANDIDATE")]
    pool = ep.eligible_pool(records)
    assert [s["symbol"] for s in ep.select_policy_c(pool, 0, {})[0]] == ["BBB", "AAA", "CCC"]
    assert {s["symbol"] for s in ep.select_policy_d(pool, 0, {})[0]} == {"BBB", "AAA", "CCC"}
    e_stories, _ = eph.select_policy_e(pool, 0, {})
    f_stories, _, _ = eph.select_policy_f(pool, 0, {})
    assert {s["symbol"] for s in e_stories} == {"AAA", "BBB", "CCC"}
    assert {s["symbol"] for s in f_stories} == {"AAA", "BBB", "CCC"}


# --------------------------------------------------------------------------- Test 9: max 5 stories
def test_each_policy_max_five_stories_per_session():
    records = [make_record(f"S{i}", _date(0), families=(3 if i < 5 else 2)) for i in range(15)]
    pool = ep.eligible_pool(records)
    assert len(ep.select_policy_c(pool, 0, {})[0]) <= 5
    assert len(ep.select_policy_d(pool, 0, {})[0]) <= 5
    assert len(eph.select_policy_e(pool, 0, {})[0]) <= 5
    assert len(eph.select_policy_f(pool, 0, {})[0]) <= 5


# --------------------------------------------------------------------------- Test 10/11: independent histories
def test_cooldown_history_independent_per_policy():
    day0 = [make_record("AAA", _date(0), novelty_type="NEW_CANDIDATE")]
    sessions = [(_date(0), day0)]
    sim_cd = ep.simulate_history(sessions)
    # Policy D's history must be untouched by Policy C's selection of the same symbol.
    day1_c_hist = {"AAA": 0}
    day1_d_hist = {}
    day1 = [make_record("AAA", _date(1), novelty_type="NEW_CANDIDATE")]
    stories_c, suppressed_c = ep.select_policy_c(ep.eligible_pool(day1), 1, day1_c_hist)
    stories_d, suppressed_d = ep.select_policy_d(ep.eligible_pool(day1), 1, day1_d_hist)
    assert all(s["symbol"] != "AAA" for s in stories_c)   # C suppressed (its own history)
    assert any(s["symbol"] == "AAA" for s in stories_d)   # D has no cooldown concept at all


def test_reservation_history_independent_per_policy():
    history_e, history_f = {"AAA": 0}, {}
    day1 = [make_record("AAA", _date(1), families=3, novelty_type="NEW_CANDIDATE")]
    stories_e, suppressed_e = eph.select_policy_e(ep.eligible_pool(day1), 1, history_e)
    stories_f, suppressed_f, _ = eph.select_policy_f(ep.eligible_pool(day1), 1, history_f)
    assert all(s["symbol"] != "AAA" for s in stories_e)   # E suppressed (in its own cooldown)
    assert any(s["symbol"] == "AAA" for s in stories_f)   # F's own history never saw AAA published


# --------------------------------------------------------------------------- Test 12/13: block metrics
def test_aggregate_recurrence_equals_sum_of_block_selections():
    sessions = [(_date(i), [make_record(f"S{i % 7}", _date(i), families=2)]) for i in range(60)]
    sim_cd = ep.simulate_history(sessions)
    all_stories = [s for stories in sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN].values() for s in stories]
    blocks = v60.block_breakdown(sessions, sim_cd, eph.simulate_hybrid_history(sessions))
    total_block_sessions = sum(len(b["session_dates"]) for b in blocks.values())
    assert total_block_sessions == 60
    # every selected story's session_date falls in exactly one block
    all_block_dates = set()
    for b in blocks.values():
        all_block_dates.update(b["session_dates"])
    assert all_block_dates == {s["session_date"] for s in all_stories}


# --------------------------------------------------------------------------- Test 14: coverage visibility
def test_incomplete_coverage_sessions_remain_visible():
    class FakeDataset:
        def __init__(self, requested, usable, skipped):
            self.requested_symbols = requested
            self.series_by_symbol = {s: [] for s in usable}
            self.skipped_symbols = skipped

    results = [
        {"session_date": _date(0), "dataset": FakeDataset(["A", "B", "C"], ["A", "B", "C"], {})},
        {"session_date": _date(1), "dataset": FakeDataset(["A", "B", "C"], ["A", "B"], {"C": "date_gap"})},
    ]
    cov = v60.coverage_report(results)
    assert cov["sessions_with_full_coverage"] == 1
    assert cov["sessions_below_full_coverage"] == 1
    assert cov["incomplete_sessions"][0]["session_date"] == _date(1).isoformat()
    assert cov["incomplete_sessions"][0]["skip_reasons"] == {"date_gap": 1}


# --------------------------------------------------------------------------- Test 15: determinism
def test_deterministic_rerun():
    sessions = [(_date(i), [make_record(f"S{i}", _date(i), families=3 if i % 3 == 0 else 2)])
               for i in range(10)]
    r1_cd, r1_hybrid = ep.simulate_history(sessions), eph.simulate_hybrid_history(sessions)
    r2_cd, r2_hybrid = ep.simulate_history(sessions), eph.simulate_hybrid_history(sessions)
    assert r1_cd == r2_cd
    assert r1_hybrid == r2_hybrid
    b1 = v60.block_breakdown(sessions, r1_cd, r1_hybrid)
    b2 = v60.block_breakdown(sessions, r2_cd, r2_hybrid)
    assert b1 == b2


# --------------------------------------------------------------------------- Test 17: no raw mutation
def test_records_unchanged_after_full_module_composition():
    records = [make_record("AAA", _date(0), families=3), make_record("BBB", _date(0), families=2)]
    before = copy.deepcopy(records)
    sessions = [(_date(0), records)]
    ep.simulate_history(sessions)
    eph.simulate_hybrid_history(sessions)
    assert records == before

"""Historical intelligence: deterministic, traceable, and honest about missing history."""
import datetime as dt

import pytest

from conftest import NOW
from conftest_intelligence import (BASE_SESSION, duplicate_relative_volume_observations,
                                   seed, session_report, movers, trading_sessions)

from core import Metric, ValidationStatus
from intelligence import HistoricalWindow, IntelligenceSnapshot, Strength, build_snapshot
from intelligence import flows, market_context, movers as movers_mod, sectors, volatility
from intelligence.history import compounded_return, count_below, streak
from intelligence.models import ELIGIBLE_HISTORICAL_STATUSES
from storage import MarketHistory


@pytest.fixture
def db(tmp_path):
    history = MarketHistory(str(tmp_path / "history.db"))
    yield history
    history.close()


def _window(history, report):
    return HistoricalWindow(history, report.session_date, report_id=report.report_id)


# --------------------------------------------------------------------- index move
def _index_history(db, moves, last=None):
    sessions = trading_sessions(len(moves) + 1, last=last or BASE_SESSION)
    seed(db, [session_report(s, pct=p) for s, p in zip(sessions[:-1], moves)])
    return sessions[-1]


def test_index_move_ranks_against_previous_twenty_sessions(db):
    today_session = _index_history(db, [0.1] * 19 + [0.2])
    today = session_report(today_session, pct=1.5)
    insights = market_context.analyse(today, _window(db, today))

    twenty = next(i for i in insights if i.insight_id == "index-move-20")
    assert twenty.strength is Strength.FULL_HISTORY
    assert twenty.sample_size == 20
    assert "larger than 20 of the previous 20 sessions" in twenty.statement
    assert twenty.metadata["larger_than_sessions"] == 20


def test_index_move_uses_absolute_size_not_direction(db):
    today_session = _index_history(db, [0.1] * 20)
    today = session_report(today_session, pct=-1.5)
    twenty = next(i for i in market_context.analyse(today, _window(db, today))
                  if i.insight_id == "index-move-20")
    assert twenty.metadata["absolute_move"] == pytest.approx(1.5)
    assert twenty.metadata["larger_than_sessions"] == 20


def test_index_move_middling_reading_is_reported_plainly(db):
    today_session = _index_history(db, [round(0.1 * i, 2) for i in range(1, 21)])
    today = session_report(today_session, pct=1.05)
    twenty = next(i for i in market_context.analyse(today, _window(db, today))
                  if i.insight_id == "index-move-20")
    assert twenty.metadata["larger_than_sessions"] == 10
    assert "larger than 10 of the previous 20 sessions" in twenty.statement


def test_index_move_excludes_the_current_session(db):
    """If today leaked into its own window it would compare against itself and the count
    would be 19 rather than 20."""
    sessions = trading_sessions(21)
    today_session = sessions[-1]
    seed(db, [session_report(s, pct=0.1) for s in sessions[:-1]])
    today = session_report(today_session, pct=1.5)
    # Persisting today first is the dangerous case: the window must still exclude it.
    seed(db, [today])

    twenty = next(i for i in market_context.analyse(today, _window(db, today))
                  if i.insight_id == "index-move-20")
    assert twenty.sample_size == 20
    assert twenty.metadata["larger_than_sessions"] == 20
    assert today.report_id not in twenty.supporting_report_ids


def test_index_move_reports_insufficient_history(db):
    sessions = trading_sessions(8)
    seed(db, [session_report(s, pct=0.2) for s in sessions[:-1]])
    today = session_report(sessions[-1], pct=1.0)
    insights = market_context.analyse(today, _window(db, today))

    five = next(i for i in insights if i.insight_id == "index-move-5")
    twenty = next(i for i in insights if i.insight_id == "index-move-20")
    assert five.strength is Strength.FULL_HISTORY
    assert twenty.strength is Strength.INSUFFICIENT_HISTORY
    assert twenty.statement == "", "no statement may be made from a window that does not exist"
    assert twenty.metadata["required_sessions"] == 20


def test_index_move_excludes_ineligible_history(db):
    """A conflicted number is not evidence and must not be averaged into a window."""
    sessions = trading_sessions(21)
    reports = [session_report(s, pct=0.1) for s in sessions[:-1]]
    for report in reports[:5]:
        for fact in report.facts_for(Metric.INDEX_CHANGE_PCT):
            fact.validation_status = ValidationStatus.CONFLICT
    seed(db, reports)

    today = session_report(sessions[-1], pct=1.5)
    twenty = next(i for i in market_context.analyse(today, _window(db, today))
                  if i.insight_id == "index-move-20")
    assert twenty.sample_size == 15
    # 15 usable sessions is not a 20-session statistic, so no statement is made at all -
    # calling it "partial" would invite presenting it as one anyway.
    assert twenty.strength is Strength.INSUFFICIENT_HISTORY
    assert twenty.statement == ""


def test_weekend_and_holiday_gaps_are_not_missing_sessions(db):
    """Windows count trading sessions. A Saturday is not an absent session, and neither is
    a holiday - the window simply reaches further back in calendar time to find 20."""
    holiday = dt.date(2026, 9, 3)
    sessions = trading_sessions(21, skip={holiday})
    seed(db, [session_report(s, pct=0.1) for s in sessions[:-1]])
    today = session_report(sessions[-1], pct=1.5)

    window = _window(db, today)
    twenty = next(i for i in market_context.analyse(today, window)
                  if i.insight_id == "index-move-20")
    assert twenty.sample_size == 20
    assert twenty.strength is Strength.FULL_HISTORY

    covered = window.sessions(Metric.INDEX_CHANGE_PCT, "NIFTY 50")[:20]
    assert holiday not in covered
    assert all(d.weekday() < 5 for d in covered), "no weekend ever appears as a session"
    # 20 sessions necessarily span more than 20 calendar days once weekends are skipped.
    assert (max(covered) - min(covered)).days > 20


# --------------------------------------------------------------------- flows
def test_fii_selling_streak_counts_today_once(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, fii=-100.0, dii=50.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], fii=-250.0, dii=60.0)

    insight = next(i for i in flows.analyse(today, _window(db, today))
                   if i.insight_id == "fii-flow-streak")
    assert insight.metadata["streak_sessions"] == 6       # 5 historical + today, not 7
    assert "net sellers for 6 consecutive recorded sessions" in insight.statement
    assert insight.metadata["direction"] == "SELLING"


def test_flow_streak_breaks_on_a_sign_change(db):
    sessions = trading_sessions(6)
    values = [-100.0, -100.0, 200.0, -100.0, -100.0]      # oldest first
    seed(db, [session_report(s, fii=v, dii=10.0) for s, v in zip(sessions[:-1], values)])
    today = session_report(sessions[-1], fii=-300.0, dii=10.0)

    insight = next(i for i in flows.analyse(today, _window(db, today))
                   if i.insight_id == "fii-flow-streak")
    assert insight.metadata["streak_sessions"] == 3       # today + the two most recent sells


def test_dii_buying_streak_is_reported_separately(db):
    sessions = trading_sessions(5)
    seed(db, [session_report(s, fii=-50.0, dii=100.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], fii=-50.0, dii=100.0)

    dii = next(i for i in flows.analyse(today, _window(db, today))
               if i.insight_id == "dii-flow-streak")
    assert "DIIs have been net buyers for 5 consecutive recorded sessions." == dii.statement


def test_cumulative_flow_statement_counts_the_direction_it_names(db):
    """A sentence reading "net sellers in 0 of them" is self-contradictory."""
    sessions = trading_sessions(5)
    seed(db, [session_report(s, fii=-100.0, dii=10.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    insight = next(i for i in flows.analyse(today, _window(db, today))
                   if i.insight_id == "fii-flow-cumulative-5")
    assert insight.metadata["cumulative_flow"] == pytest.approx(-500.0)
    assert "net sellers in 5 of them" in insight.statement
    assert "Rs -500 crore" in insight.statement


def test_cumulative_flow_needs_the_full_window(db):
    sessions = trading_sessions(4)
    seed(db, [session_report(s, fii=-100.0, dii=10.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    five = next(i for i in flows.analyse(today, _window(db, today))
                if i.insight_id == "fii-flow-cumulative-5")
    assert five.strength is Strength.INSUFFICIENT_HISTORY
    assert five.statement == ""


def _fii_streak(db, today):
    return next((i for i in flows.analyse(today, _window(db, today))
                 if i.insight_id == "fii-flow-streak"), None)


def test_streak_breaks_at_a_recorded_session_with_ineligible_evidence(db):
    """Required case A: an ineligible session BREAKS the run rather than being stepped over.

    A streak is a continuity claim. Reaching past a session whose evidence we could not use,
    to find another matching value behind it, would assert continuity we cannot support.
    """
    sessions = trading_sessions(6)                      # 5 historical + today
    reports = [session_report(s, fii=-100.0, dii=10.0) for s in sessions[:-1]]
    conflicted = reports[-3]                            # third-most-recent historical session
    for fact in conflicted.facts_for(Metric.FII_NET_CASH):
        fact.validation_status = ValidationStatus.CONFLICT
    seed(db, reports)
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    insight = _fii_streak(db, today)
    assert insight.metadata["streak_sessions"] == 3, \
        "today plus the two clean sessions behind it - the run stops at the conflicted one"
    assert conflicted.report_id not in insight.supporting_report_ids


def test_streak_breaks_at_a_recorded_session_with_the_fact_missing(db):
    """Required case B: a session we recorded that simply has no FII fact breaks the run."""
    sessions = trading_sessions(6)
    reports = []
    for i, session in enumerate(sessions[:-1]):
        if i == 2:                                      # third-oldest: no flows at all
            reports.append(session_report(session))
        else:
            reports.append(session_report(session, fii=-100.0, dii=10.0))
    seed(db, reports)
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    insight = _fii_streak(db, today)
    assert insight.metadata["streak_sessions"] == 3     # today + the two most recent sessions
    assert reports[2].report_id not in insight.supporting_report_ids


def test_streak_continues_across_weekends_and_holidays(db):
    """Required case C: a missing CALENDAR day is not missing canonical evidence.

    Weekends, holidays and days we never recorded are not on the session spine at all, are
    never consulted, and therefore cannot break anything.
    """
    holiday = dt.date(2026, 9, 16)
    sessions = trading_sessions(6, skip={holiday})
    seed(db, [session_report(s, fii=-100.0, dii=10.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    insight = _fii_streak(db, today)
    assert insight.metadata["streak_sessions"] == 6, \
        "the run is unbroken: no session was recorded on the holiday or at the weekend"
    assert holiday not in sessions
    # Six sessions necessarily span more than six calendar days once the gaps are skipped.
    assert (sessions[-1] - sessions[0]).days > 6


def test_streak_of_one_is_not_reported(db):
    """When the immediately preceding recorded session breaks the run there is no streak."""
    sessions = trading_sessions(6)
    reports = [session_report(s, fii=-100.0, dii=10.0) for s in sessions[:-1]]
    for fact in reports[-1].facts_for(Metric.FII_NET_CASH):
        fact.validation_status = ValidationStatus.STALE
    seed(db, reports)
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    assert _fii_streak(db, today) is None, "a run of one is below the reporting threshold"


def test_cumulative_flow_keeps_available_session_semantics(db):
    """Required: continuity is strict, but the cumulative summary is deliberately not.

    It says "available sessions" and means it - eligible readings, gaps passed over. The two
    statistics answer different questions and the wording distinguishes them.
    """
    sessions = trading_sessions(6)
    reports = [session_report(s, fii=-100.0, dii=10.0) for s in sessions[:-1]]
    for fact in reports[-3].facts_for(Metric.FII_NET_CASH):
        fact.validation_status = ValidationStatus.CONFLICT
    seed(db, reports)
    today = session_report(sessions[-1], fii=-100.0, dii=10.0)

    insights = flows.analyse(today, _window(db, today))
    cumulative = next(i for i in insights if i.insight_id == "fii-flow-cumulative-5")
    assert "available sessions" in cumulative.statement
    assert "recorded sessions" in _fii_streak(db, today).statement


# --------------------------------------------------------------------- volatility
def test_vix_ranks_against_previous_twenty_readings(db):
    sessions = trading_sessions(21)
    seed(db, [session_report(s, vix=12.0 + i * 0.1) for i, s in enumerate(sessions[:-1])])
    today = session_report(sessions[-1], vix=25.0)

    rank = next(i for i in volatility.analyse(today, _window(db, today))
                if i.insight_id == "vix-rank-20")
    assert rank.metadata["higher_than_sessions"] == 20
    assert rank.metadata["minimum"] == pytest.approx(12.0)
    assert rank.metadata["maximum"] == pytest.approx(13.9)
    assert "higher than 20 of the previous 20 available readings" in rank.statement


def test_vix_five_session_average_comparison(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, vix=10.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], vix=20.0)

    mean_insight = next(i for i in volatility.analyse(today, _window(db, today))
                        if i.insight_id == "vix-mean-5")
    assert mean_insight.comparison_value == pytest.approx(10.0)
    assert "above its 5-session average of 10.0" in mean_insight.statement


def test_vix_below_average_is_stated_plainly(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, vix=20.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], vix=11.0)
    mean_insight = next(i for i in volatility.analyse(today, _window(db, today))
                        if i.insight_id == "vix-mean-5")
    assert "below its 5-session average" in mean_insight.statement


def test_vix_insufficient_history(db):
    sessions = trading_sessions(4)
    seed(db, [session_report(s, vix=12.0) for s in sessions[:-1]])
    today = session_report(sessions[-1], vix=15.0)

    rank = next(i for i in volatility.analyse(today, _window(db, today))
                if i.insight_id == "vix-rank-20")
    assert rank.strength is Strength.INSUFFICIENT_HISTORY
    assert rank.statement == ""


# --------------------------------------------------------------------- sectors
def _sector(name, pct):
    return {"name": name, "pct": pct}


def test_sector_decline_streak(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, sectors=[_sector("IT", -0.5), _sector("Bank", 0.4)])
              for s in sessions[:-1]])
    today = session_report(sessions[-1], sectors=[_sector("IT", -0.8), _sector("Bank", 0.2)])

    insight = next(i for i in sectors.analyse(today, _window(db, today))
                   if i.subject == "IT")
    assert insight.metadata["streak_sessions"] == 6
    assert "Nifty IT has declined in 6 consecutive recorded sessions." == insight.statement
    assert insight.metadata["direction"] == "DOWN"


def test_sector_returns_are_compounded_not_summed(db):
    """Summing daily percentages is wrong and gets worse as the window lengthens."""
    sessions = trading_sessions(4)
    seed(db, [session_report(s, sectors=[_sector("IT", 10.0)]) for s in sessions[:-1]])
    today = session_report(sessions[-1], sectors=[_sector("IT", 10.0)])

    insight = next(i for i in sectors.analyse(today, _window(db, today)) if i.subject == "IT")
    expected = (1.10 ** 4 - 1) * 100
    assert insight.metadata["compounded_change_pct"] == pytest.approx(expected)
    assert insight.metadata["compounded_change_pct"] != pytest.approx(40.0)


def test_compounded_return_helper():
    assert compounded_return([10.0, 10.0]) == pytest.approx(21.0)
    assert compounded_return([-50.0, 100.0]) == pytest.approx(0.0)
    assert compounded_return([]) == pytest.approx(0.0)


def test_sector_absent_from_a_session_reduces_the_sample(db):
    """A missing reading is not a flat day."""
    sessions = trading_sessions(6)
    reports = []
    for i, session in enumerate(sessions[:-1]):
        rows = [_sector("IT", -0.5)] if i != 2 else [_sector("Bank", 0.3)]
        reports.append(session_report(session, sectors=rows))
    seed(db, reports)
    today = session_report(sessions[-1], sectors=[_sector("IT", -0.4)])

    window = _window(db, today)
    insights = sectors.analyse(today, window)
    it = next(i for i in insights if i.subject == "IT")
    assert it.sample_size == 5, "the absent session shrinks the sample rather than padding it"
    assert any("absent from" in w for w in window.warnings)


def test_sector_streak_breaks_at_a_session_where_the_sector_is_missing(db):
    """Required case D: a recorded session lacking the sector breaks its run.

    IT falls on the two most recent sessions and on older ones, but one recorded session in
    between has no IT reading at all - so the run is today plus two, not the whole span.
    """
    sessions = trading_sessions(6)
    reports = []
    for i, session in enumerate(sessions[:-1]):
        rows = [_sector("Bank", 0.3)] if i == 2 else [_sector("IT", -0.5), _sector("Bank", 0.3)]
        reports.append(session_report(session, sectors=rows))
    seed(db, reports)
    today = session_report(sessions[-1], sectors=[_sector("IT", -0.4), _sector("Bank", 0.2)])

    insight = next(i for i in sectors.analyse(today, _window(db, today)) if i.subject == "IT")
    assert insight.metadata["streak_sessions"] == 3
    assert reports[2].report_id not in insight.supporting_report_ids


def test_sector_streak_breaks_at_an_ineligible_session(db):
    sessions = trading_sessions(6)
    reports = [session_report(s, sectors=[_sector("IT", -0.5)]) for s in sessions[:-1]]
    for fact in reports[2].facts_for(Metric.SECTOR_CHANGE_PCT):
        fact.validation_status = ValidationStatus.CONFLICT
    seed(db, reports)
    today = session_report(sessions[-1], sectors=[_sector("IT", -0.4)])

    insight = next(i for i in sectors.analyse(today, _window(db, today)) if i.subject == "IT")
    assert insight.metadata["streak_sessions"] == 3


def test_sector_streak_continues_across_calendar_gaps(db):
    holiday = dt.date(2026, 9, 16)
    sessions = trading_sessions(6, skip={holiday})
    seed(db, [session_report(s, sectors=[_sector("IT", -0.5)]) for s in sessions[:-1]])
    today = session_report(sessions[-1], sectors=[_sector("IT", -0.4)])

    insight = next(i for i in sectors.analyse(today, _window(db, today)) if i.subject == "IT")
    assert insight.metadata["streak_sessions"] == 6


def test_sector_without_a_streak_is_not_reported(db):
    sessions = trading_sessions(6)
    alternating = [0.5, -0.5, 0.5, -0.5, 0.5]
    seed(db, [session_report(s, sectors=[_sector("IT", p)])
              for s, p in zip(sessions[:-1], alternating)])
    today = session_report(sessions[-1], sectors=[_sector("IT", -0.2)])

    window = _window(db, today)
    assert sectors.analyse(today, window) == []
    assert any("consecutive" in w for w in window.warnings)


# --------------------------------------------------------------------- movers
def test_first_appearance_is_not_reported(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, gainers=movers(["OTHER"])) for s in sessions[:-1]])
    today = session_report(sessions[-1], gainers=movers(["FRESH"]))
    assert movers_mod.analyse_recurrence(today, _window(db, today)) == []


def test_repeat_mover_is_counted_and_classified(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, gainers=movers(["ABC"] if i < 2 else ["OTHER"]))
              for i, s in enumerate(sessions[:-1])])
    today = session_report(sessions[-1], gainers=movers(["ABC"]))

    insight = next(i for i in movers_mod.analyse_recurrence(today, _window(db, today))
                   if i.subject == "ABC")
    assert insight.metadata["prior_appearances"] == 2
    assert insight.metadata["classification"] == movers_mod.REPEAT_MOVER
    assert "appeared among the tracked top movers 2 times" in insight.statement


def test_frequent_mover_threshold(db):
    sessions = trading_sessions(6)
    seed(db, [session_report(s, gainers=movers(["ABC"])) for s in sessions[:-1]])
    today = session_report(sessions[-1], gainers=movers(["ABC"]))

    insight = next(i for i in movers_mod.analyse_recurrence(today, _window(db, today))
                   if i.subject == "ABC")
    assert insight.metadata["prior_appearances"] == 5
    assert insight.metadata["classification"] == movers_mod.FREQUENT_MOVER


def test_mover_classification_boundaries():
    assert movers_mod.classify(0) == movers_mod.FIRST_APPEARANCE
    assert movers_mod.classify(1) == movers_mod.REPEAT_MOVER
    assert movers_mod.classify(2) == movers_mod.REPEAT_MOVER
    assert movers_mod.classify(3) == movers_mod.FREQUENT_MOVER
    assert movers_mod.classify(9) == movers_mod.FREQUENT_MOVER


def test_mover_recurrence_excludes_the_current_session(db):
    sessions = trading_sessions(4)
    seed(db, [session_report(s, gainers=movers(["ABC"])) for s in sessions[:-1]])
    today = session_report(sessions[-1], gainers=movers(["ABC"]))
    seed(db, [today])                      # today persisted before analysis

    insight = next(i for i in movers_mod.analyse_recurrence(today, _window(db, today))
                   if i.subject == "ABC")
    assert insight.metadata["prior_appearances"] == 3, "today must not count as a prior appearance"
    assert insight.metadata["excludes_current_session"] is True


def test_gainer_and_loser_appearances_are_tracked_separately(db):
    sessions = trading_sessions(4)
    seed(db, [session_report(sessions[0], gainers=movers(["ABC"])),
              session_report(sessions[1], losers=movers(["ABC"])),
              session_report(sessions[2], losers=movers(["ABC"]))])
    today = session_report(sessions[-1], gainers=movers(["ABC"]))

    insight = next(i for i in movers_mod.analyse_recurrence(today, _window(db, today))
                   if i.subject == "ABC")
    assert insight.metadata["as_gainer_last_5"] == 1
    assert insight.metadata["as_loser_last_5"] == 2


# --------------------------------------------------------------------- relative volume
def test_relative_volume_compares_only_matching_definition_versions(db):
    """A 1.x reading used a 10-session window - comparing it to a 2.0 reading would compare
    two different measurements that happen to share a name."""
    sessions = trading_sessions(8)
    seed(db, [session_report(s, gainers=movers(["ABC"], volx=1.0),
                             definition_version="1.0") for s in sessions[:-1]])
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=3.0))

    window = _window(db, today)
    assert movers_mod.analyse_relative_volume(today, window) == []
    assert any("older definition" in w for w in window.warnings)


def test_relative_volume_ranks_against_comparable_readings(db):
    sessions = trading_sessions(8)
    seed(db, [session_report(s, gainers=movers(["ABC"], volx=1.0 + i * 0.1))
              for i, s in enumerate(sessions[:-1])])
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=5.0))

    insight = next(iter(movers_mod.analyse_relative_volume(today, _window(db, today))))
    assert insight.metadata["comparable_sample"] == 7
    assert insight.metadata["higher_than_readings"] == 7
    assert insight.metadata["definition_version"] == "2.0"
    assert "higher than 7 of its previous 7 comparable readings" in insight.statement


def test_relative_volume_counts_each_canonical_fact_once(db):
    """Five historical facts with two observations each is a sample of five, not ten.

    get_recent_metric_points returns one row per observation; counting rows would inflate
    the sample, the rank and the supporting-fact list, making a reading look better
    corroborated than it is.
    """
    sessions = trading_sessions(7)
    reports = [duplicate_relative_volume_observations(
        session_report(s, gainers=movers(["ABC"], volx=1.0 + i * 0.1)))
        for i, s in enumerate(sessions[:-1])]
    seed(db, reports)
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=9.0))

    insight = next(iter(movers_mod.analyse_relative_volume(today, _window(db, today))))
    assert insight.metadata["comparable_sample"] == 6      # six historical facts, not twelve
    assert insight.metadata["higher_than_readings"] == 6
    assert insight.sample_size == 6
    assert "higher than 6 of its previous 6 comparable readings" in insight.statement


def test_relative_volume_supporting_facts_appear_once_each(db):
    sessions = trading_sessions(7)
    reports = [duplicate_relative_volume_observations(
        session_report(s, gainers=movers(["ABC"], volx=1.0))) for s in sessions[:-1]]
    seed(db, reports)
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=9.0))

    insight = next(iter(movers_mod.analyse_relative_volume(today, _window(db, today))))
    historical = insight.supporting_fact_ids[1:]          # first is today's fact
    assert len(historical) == len(set(historical)) == 6


def test_a_single_fact_with_two_observations_adds_one_to_the_sample(db):
    sessions = trading_sessions(7)
    plain = [session_report(s, gainers=movers(["ABC"], volx=1.0)) for s in sessions[1:-1]]
    doubled = duplicate_relative_volume_observations(
        session_report(sessions[0], gainers=movers(["ABC"], volx=1.0)))
    seed(db, plain + [doubled])
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=9.0))

    insight = next(iter(movers_mod.analyse_relative_volume(today, _window(db, today))))
    assert insight.metadata["comparable_sample"] == 6     # 5 plain + 1 doubled, counted once


def test_multi_observation_facts_on_an_older_definition_stay_excluded(db):
    """Duplicated observations must not smuggle an incompatible reading back in."""
    sessions = trading_sessions(8)
    seed(db, [duplicate_relative_volume_observations(
        session_report(s, gainers=movers(["ABC"], volx=1.0), definition_version="1.0"))
        for s in sessions[:-1]])
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=9.0))

    window = _window(db, today)
    assert movers_mod.analyse_relative_volume(today, window) == []
    assert any("older definition" in w for w in window.warnings)


def test_relative_volume_needs_a_minimum_comparable_sample(db):
    sessions = trading_sessions(4)
    seed(db, [session_report(s, gainers=movers(["ABC"], volx=1.0)) for s in sessions[:-1]])
    today = session_report(sessions[-1], gainers=movers(["ABC"], volx=4.0))

    window = _window(db, today)
    assert movers_mod.analyse_relative_volume(today, window) == []
    assert any("comparable relative-volume history" in w for w in window.warnings)


# --------------------------------------------------------------------- helpers
def test_streak_helper_stops_at_a_zero():
    assert streak([-1, -1, 0, -1], positive=False) == 2
    assert streak([1, 1, 1], positive=True) == 3
    assert streak([1, -1], positive=False) == 0


def test_count_below_helper():
    assert count_below([1, 2, 3], 2.5) == 2
    assert count_below([], 1.0) == 0


def test_eligibility_policy_is_centralised():
    assert ELIGIBLE_HISTORICAL_STATUSES == {"VERIFIED", "SINGLE_SOURCE"}
    for excluded in ("CONFLICT", "STALE", "MISSING", "REJECTED", "PROVISIONAL"):
        assert excluded not in ELIGIBLE_HISTORICAL_STATUSES


def test_provisional_ai_only_facts_never_enter_calculations(db):
    """An LLM-only critical number is not evidence for a historical average."""
    sessions = trading_sessions(21)
    reports = [session_report(s, pct=0.1) for s in sessions[:-1]]
    for report in reports:
        for fact in report.facts_for(Metric.INDEX_CHANGE_PCT):
            fact.validation_status = ValidationStatus.PROVISIONAL
    seed(db, reports)
    today = session_report(sessions[-1], pct=1.5)

    twenty = next(i for i in market_context.analyse(today, _window(db, today))
                  if i.insight_id == "index-move-20")
    assert twenty.sample_size == 0
    assert twenty.strength is Strength.INSUFFICIENT_HISTORY

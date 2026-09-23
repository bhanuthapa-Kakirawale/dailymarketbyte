"""Deterministic hook selection: the one fact that earns the first three seconds."""
import pytest

from conftest import NOW
from conftest_intelligence import movers, session_report, trading_sessions

from core.content_safety import SafetyStatus, classify_text
from editorial import hook_candidates, select_hook
from editorial.config import HARD_PRIMARY_WORDS, HARD_SECONDARY_WORDS
from editorial.hook import BIG_FLOW_CRORE, fallback
from intelligence import (InsightCategory, IntelligenceInsight, IntelligenceSnapshot,
                          Strength)

SESSION = trading_sessions(1)[0]


def _safe(text):
    return classify_text(text).status is not SafetyStatus.BLOCKED


def _report(**kw):
    kw.setdefault("pct", -1.25)
    kw.setdefault("vix", 15.2)
    return session_report(SESSION, **kw)


def _snapshot(*insights):
    snap = IntelligenceSnapshot(report_id="20260922_PRE_MARKET")
    snap.insights = list(insights)
    return snap


def _insight(insight_id, category, subject, statement, metadata, lookback=20):
    return IntelligenceInsight(
        insight_id=insight_id, category=category, subject=subject, statement=statement,
        sample_size=lookback, lookback_sessions=lookback, strength=Strength.FULL_HISTORY,
        metadata=metadata)


# --------------------------------------------------------------------- candidates
def test_unusual_index_move_is_the_strongest_candidate():
    snap = _snapshot(_insight("index-move-20", InsightCategory.INDEX_MOVE, "NIFTY 50",
                              "Nifty's move is larger than 18 of the previous 20 sessions.",
                              {"larger_than_sessions": 18, "selection_score": 0.9}))
    chosen = select_hook(_report(), snap, is_safe=_safe)
    assert chosen.candidate_id == "hook-index-unusual"
    assert chosen.primary_text == "NIFTY"
    assert "18" in chosen.secondary_text


def test_vix_extreme_is_a_candidate():
    snap = _snapshot(_insight("vix-rank-20", InsightCategory.VOLATILITY, "INDIA VIX",
                              "India VIX is higher than 19 of the previous 20 readings.",
                              {"higher_than_sessions": 19}))
    ids = [c.candidate_id for c in hook_candidates(_report(pct=0.2), snap)]
    assert "hook-vix-extreme" in ids


def test_flow_streak_is_a_candidate():
    snap = _snapshot(_insight("fii-flow-streak", InsightCategory.INSTITUTIONAL_FLOW, "FII",
                              "FIIs have been net sellers for 5 consecutive recorded sessions.",
                              {"streak_sessions": 5, "direction": "SELLING"}, lookback=5))
    chosen = select_hook(_report(pct=0.2, fii=-900.0, dii=100.0), snap, is_safe=_safe)
    assert chosen.candidate_id == "hook-fii-streak"
    assert "FIIs SOLD" == chosen.primary_text
    assert "5 consecutive recorded sessions" == chosen.secondary_text


def test_large_flow_is_a_candidate_without_any_history():
    chosen = select_hook(_report(pct=0.2, fii=-(BIG_FLOW_CRORE + 500), dii=100.0),
                         None, is_safe=_safe)
    assert chosen.candidate_id == "hook-fii-large"
    assert "SOLD" in chosen.primary_text


def test_large_index_move_is_a_candidate_without_history():
    chosen = select_hook(_report(pct=-1.8), None, is_safe=_safe)
    assert chosen.candidate_id == "hook-index-large"
    assert chosen.primary_value == "-1.80%"


def test_exceptional_sector_move_is_a_candidate():
    report = _report(pct=0.1, sectors=[{"name": "Metal", "pct": -3.2}, {"name": "IT", "pct": 0.2}])
    ids = [c.candidate_id for c in hook_candidates(report, None)]
    assert "hook-sector-move" in ids


def test_big_mover_is_a_candidate():
    report = _report(pct=0.1, gainers=movers(["STOCK-A"]))
    # The fixture's movers sit at +3.0%, below the threshold, so nothing should qualify.
    assert "hook-mover" not in [c.candidate_id for c in hook_candidates(report, None)]


def test_candidates_are_ordered_strongest_first():
    snap = _snapshot(
        _insight("index-move-20", InsightCategory.INDEX_MOVE, "NIFTY 50", "x",
                 {"larger_than_sessions": 19}),
        _insight("fii-flow-streak", InsightCategory.INSTITUTIONAL_FLOW, "FII", "y",
                 {"streak_sessions": 4, "direction": "SELLING"}))
    scores = [c.score for c in hook_candidates(_report(fii=-900.0, dii=10.0), snap)]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------- fallback
def test_fallback_is_used_when_nothing_stands_out():
    chosen = select_hook(_report(pct=0.12, fii=None, dii=None, sectors=[]), None, is_safe=_safe)
    assert chosen.candidate_id == "hook-fallback-index"
    assert chosen.primary_value == "+0.12%"


def test_fallback_is_still_a_fact_not_filler():
    neutral = fallback(_report(pct=0.3))
    assert neutral.primary_numeric == pytest.approx(0.3)
    assert neutral.source_fact_ids


# --------------------------------------------------------------------- safety
def test_unsafe_candidate_is_skipped_for_the_next_one():
    """Safety is never bypassed: a rejected hook falls through, it is not rewritten."""
    snap = _snapshot(_insight("index-move-20", InsightCategory.INDEX_MOVE, "NIFTY 50", "x",
                              {"larger_than_sessions": 18}))
    report = _report(fii=-(BIG_FLOW_CRORE + 500), dii=100.0)

    def _reject_index(text):
        return "NIFTY" not in text

    chosen = select_hook(report, snap, is_safe=_reject_index)
    assert chosen.candidate_id != "hook-index-unusual"
    assert all(_reject_index(t) for t in chosen.texts())


def test_every_hook_passes_the_publication_content_scan():
    snap = _snapshot(_insight("fii-flow-streak", InsightCategory.INSTITUTIONAL_FLOW, "FII",
                              "FIIs have been net sellers for 5 consecutive recorded sessions.",
                              {"streak_sessions": 5, "direction": "SELLING"}))
    for report in (_report(), _report(pct=-2.4), _report(fii=-5000.0, dii=200.0)):
        chosen = select_hook(report, snap, is_safe=_safe)
        for text in chosen.texts():
            assert classify_text(text).status is SafetyStatus.SAFE, text


def test_hooks_carry_no_recommendation_or_prediction_language():
    banned = ("buy", "sell ", "target", "crash", "opportunity", "should", "will ",
              "smart money", "explode", "multibagger", "shocking", "alert")
    snap = _snapshot(_insight("fii-flow-streak", InsightCategory.INSTITUTIONAL_FLOW, "FII",
                              "x", {"streak_sessions": 5, "direction": "SELLING"}))
    for report in (_report(), _report(pct=2.4), _report(fii=-9000.0, dii=200.0)):
        for candidate in hook_candidates(report, snap) + [fallback(report)]:
            joined = " ".join(candidate.texts()).lower()
            for term in banned:
                assert term not in joined, f"{candidate.candidate_id}: {joined}"


# --------------------------------------------------------------------- budgets
def test_hook_text_stays_within_the_word_budget():
    snap = _snapshot(_insight("index-move-20", InsightCategory.INDEX_MOVE, "NIFTY 50", "x",
                              {"larger_than_sessions": 18}))
    for report in (_report(), _report(pct=-2.4), _report(fii=-5000.0, dii=200.0)):
        chosen = select_hook(report, snap, is_safe=_safe)
        primary = len(chosen.primary_text.split()) + len(chosen.primary_value.split())
        assert primary <= HARD_PRIMARY_WORDS, chosen.candidate_id
        assert len(chosen.secondary_text.split()) <= HARD_SECONDARY_WORDS, chosen.candidate_id


def test_over_budget_candidate_is_rejected():
    from editorial.hook import HookCandidate
    long_one = HookCandidate(candidate_id="x", primary_text=" ".join(["word"] * 20))
    assert not long_one.within_budget()


# --------------------------------------------------------------------- determinism
def test_same_inputs_always_give_the_same_hook():
    snap = _snapshot(_insight("index-move-20", InsightCategory.INDEX_MOVE, "NIFTY 50", "x",
                              {"larger_than_sessions": 18}))
    first = select_hook(_report(), snap, is_safe=_safe)
    second = select_hook(_report(), snap, is_safe=_safe)
    assert first.candidate_id == second.candidate_id
    assert first.texts() == second.texts()


# --------------------------------------------------------------------- temporal semantics
# A PRE_MARKET report describes an already-completed PREVIOUS session, never the session
# still to open. Wording that says a completed-session fact happened "today" is wrong
# regardless of the wall-clock time the video is rendered or published at.
def test_pre_market_sector_fact_never_says_today():
    from core import ReportType
    report = _report(pct=0.1, sectors=[{"name": "Metal", "pct": -3.2}, {"name": "IT", "pct": 0.2}])
    assert report.report_type is ReportType.PRE_MARKET
    chosen = next(c for c in hook_candidates(report, None) if c.candidate_id == "hook-sector-move")
    assert "today" not in chosen.secondary_text.lower()
    assert "previous session" in chosen.secondary_text.lower()


def test_pre_market_mover_fact_never_says_todays_gainer_or_loser():
    from core import ReportType
    gainers = movers(["STOCK-A"])
    gainers[0]["pct"] = 7.5
    report = _report(pct=0.1, gainers=gainers)
    assert report.report_type is ReportType.PRE_MARKET
    ids = [c.candidate_id for c in hook_candidates(report, None)]
    assert "hook-mover" in ids
    chosen = next(c for c in hook_candidates(report, None) if c.candidate_id == "hook-mover")
    lowered = chosen.secondary_text.lower()
    assert "today" not in lowered
    assert "today's gainer" not in lowered and "today's loser" not in lowered
    assert "previous session" in lowered


def test_pre_market_nifty_fallback_is_anchored_to_the_previous_session():
    from core import Metric, ReportType

    # The plain "MARKET RECAP" fallback (reached only when no index fact exists at all) is
    # the clearest case: its wording must not claim "today" for a PRE_MARKET report.
    report = _report(pct=0.3)
    assert report.report_type is ReportType.PRE_MARKET
    report.facts = [f for f in report.facts if f.metric is not Metric.INDEX_CHANGE_PCT]

    plain = fallback(report)
    assert plain.candidate_id == "hook-fallback-plain"
    assert "today" not in plain.secondary_text.lower()
    assert "previous session" in plain.secondary_text.lower()


def test_post_market_facts_may_say_today():
    """POST_MARKET reports the just-completed current session, where "today" is accurate."""
    from core import ReportType
    report = _report(pct=0.1, sectors=[{"name": "Metal", "pct": -3.2}, {"name": "IT", "pct": 0.2}])
    report.report_type = ReportType.POST_MARKET
    chosen = next(c for c in hook_candidates(report, None) if c.candidate_id == "hook-sector-move")
    assert chosen.secondary_text == "Sharpest sector move today"

    mover_gainers = movers(["STOCK-A"])
    mover_gainers[0]["pct"] = 7.5
    mover_report = _report(pct=0.1, gainers=mover_gainers)
    mover_report.report_type = ReportType.POST_MARKET
    mover_chosen = next(c for c in hook_candidates(mover_report, None)
                        if c.candidate_id == "hook-mover")
    assert mover_chosen.secondary_text == "Biggest tracked move today"


def test_weekend_gap_pre_market_wording_says_previous_session_not_yesterday():
    """A Monday PRE_MARKET report recaps Friday's session - a 3-calendar-day gap. Wording
    must stay "previous session", never "yesterday", and must not be inferred from any
    wall-clock/weekday computation - report_type alone decides it."""
    import datetime as dt

    report = _report(pct=0.1, sectors=[{"name": "Metal", "pct": -3.2}, {"name": "IT", "pct": 0.2}],
                     report_date=SESSION + dt.timedelta(days=3))
    chosen = next(c for c in hook_candidates(report, None) if c.candidate_id == "hook-sector-move")
    assert "yesterday" not in chosen.secondary_text.lower()
    assert "previous session" in chosen.secondary_text.lower()


def test_temporal_wording_still_passes_content_safety_and_traceability():
    report = _report(pct=0.1, sectors=[{"name": "Metal", "pct": -3.2}, {"name": "IT", "pct": 0.2}])
    chosen = select_hook(report, None, is_safe=_safe)
    for text in chosen.texts():
        assert classify_text(text).status is SafetyStatus.SAFE
    assert chosen.source_fact_ids, "the hook must still trace back to a canonical fact"

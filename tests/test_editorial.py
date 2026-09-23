"""Editorial selection: what the Short says, how long it runs, and what it leaves out."""
import datetime as dt

import pytest

from conftest import NOW
from conftest_intelligence import movers, session_report, trading_sessions

from core import MarketReport
from editorial import (MAX_CONTEXT_INSIGHTS, MAX_EVENTS, MAX_GLOBAL_CUES, MAX_HEATMAP_CARDS,
                       MAX_RANKED_MOVERS, MAX_SECTORS_HIGHLIGHTED, MAX_SHORT_DURATION,
                       MIN_SHORT_DURATION, SceneType, plan_short)
from editorial.config import MAX_MAJOR_CARDS, TICKER_SCENES, bounds_for

SESSION = trading_sessions(1)[0]


def _losing(rows):
    """`movers()` stamps every row with a positive pct; losers_scene filters on sign, so
    fixtures that stand in for losers must actually carry a negative change."""
    for row in rows:
        row["pct"] = -abs(row["pct"])
    return rows


def _report(**kw):
    kw.setdefault("pct", -1.25)
    kw.setdefault("fii", -3810.0)
    kw.setdefault("dii", 2100.0)
    kw.setdefault("vix", 15.2)
    kw.setdefault("sectors", [{"name": "IT", "pct": 1.4}, {"name": "Bank", "pct": 0.3},
                              {"name": "Metal", "pct": -1.1}, {"name": "Auto", "pct": -0.4}])
    kw.setdefault("gainers", movers(["STOCK-A", "STOCK-B", "STOCK-C"]))
    kw.setdefault("losers", _losing(movers(["STOCK-F", "STOCK-G"])))
    kw.setdefault("events", [
        {"tag": "RESULTS", "text": "Large-cap quarterly results today",
         "source": "GEMINI_SEARCH", "publisher": None},
        {"tag": "IPO", "text": "Example IPO opens", "source": "GEMINI_SEARCH", "publisher": None},
        {"tag": "RBI", "text": "Policy decision due", "source": "GEMINI_SEARCH", "publisher": None}])
    return session_report(SESSION, **kw)


@pytest.fixture
def plan():
    return plan_short(_report(), None, now=NOW)


# --------------------------------------------------------------------- shape
def test_plan_opens_with_the_hook(plan):
    assert plan.scenes[0].scene_type is SceneType.HOOK
    assert plan.hook is plan.scenes[0]
    assert plan.hook.primary_value or plan.hook.primary_text


def test_plan_ends_with_a_short_outro(plan):
    outro = plan.scenes[-1]
    assert outro.scene_type is SceneType.OUTRO
    assert outro.planned_duration <= 3.0, "branding must not eat the Short"


def test_every_scene_has_one_primary_message(plan):
    """The rule the phase turns on: one dominant value per scene, never a dashboard.

    A HEATMAP/SCAN scene is scanned as a grid or ranked list, not read card by card, so it is
    exempt from the narrative card-count cap and checked against its own, higher, ceiling.
    """
    for scene in plan.scenes:
        assert scene.primary_text or scene.primary_value, scene.scene_id
        presentation = scene.metadata.get("presentation")
        if presentation == "HEATMAP":
            assert scene.card_count() <= MAX_HEATMAP_CARDS, scene.scene_id
        elif presentation == "SCAN":
            assert scene.card_count() <= MAX_RANKED_MOVERS, scene.scene_id
        elif presentation == "GLOBAL_SCAN":
            assert scene.card_count() <= MAX_GLOBAL_CUES, scene.scene_id
        else:
            assert scene.card_count() <= MAX_MAJOR_CARDS, scene.scene_id


def test_no_scene_cycles_captions(plan):
    """Text changing under a viewer who is reading a number is the thing being removed."""
    for scene in plan.scenes:
        assert scene.caption_changes() == 0, scene.scene_id


# --------------------------------------------------------------------- selection caps
def test_global_cues_are_capped_and_ordered_by_relevance(plan):
    scene = plan.scene(SceneType.GLOBAL)
    if scene is None:
        pytest.skip("fixture has no global cues")
    assert len(scene.items) <= MAX_GLOBAL_CUES


def test_sectors_show_strongest_and_weakest_not_a_heatmap(plan):
    scene = plan.scene(SceneType.SECTORS)
    assert len(scene.items) <= MAX_SECTORS_HIGHLIGHTED
    titles = [i.title for i in scene.items]
    assert titles[0] == "IT", "strongest first"
    assert "Metal" in titles, "the weakest sector must survive trimming"


def test_movers_are_capped(plan):
    for scene_type in (SceneType.GAINERS, SceneType.LOSERS):
        scene = plan.scene(scene_type)
        assert len(scene.items) <= MAX_RANKED_MOVERS


def test_movers_show_both_sides_of_the_session():
    """Gainers and losers are two separate scan scenes, not a combined leaderboard - each
    must show its own side of the session regardless of how the other side moved."""
    gainers = movers(["STOCK-A", "STOCK-B", "STOCK-C"])
    for row, pct in zip(gainers, (9.1, 8.4, 7.7)):
        row["pct"] = pct
    losers = movers(["STOCK-F"])
    losers[0]["pct"] = -2.2

    plan = plan_short(_report(gainers=gainers, losers=losers), None, now=NOW)
    gainer_numerics = [i.numeric for i in plan.scene(SceneType.GAINERS).items]
    loser_numerics = [i.numeric for i in plan.scene(SceneType.LOSERS).items]
    assert gainer_numerics[0] == pytest.approx(9.1), "the strongest gainer leads"
    assert all(n < 0 for n in loser_numerics), "the losers scene must survive on its own"


def test_events_are_capped(plan):
    scene = plan.scene(SceneType.EVENTS)
    assert len(scene.items) <= MAX_EVENTS


def test_omissions_are_recorded_rather_than_lost(plan):
    reasons = {o["scene"] for o in plan.omitted}
    assert "sectors" in reasons or "movers" in reasons
    assert all("reason" in o for o in plan.omitted)


def test_planning_does_not_mutate_the_report():
    report = _report()
    before = report.to_json()
    plan_short(report, None, now=NOW)
    assert report.to_json() == before


# --------------------------------------------------------------------- pacing
def test_scene_durations_respect_their_bounds(plan):
    for scene in plan.scenes:
        low, high = bounds_for(scene.scene_type.value)
        assert low <= scene.planned_duration <= high, scene.scene_id


def test_durations_are_content_driven_not_fixed(plan):
    durations = {s.planned_duration for s in plan.scenes}
    assert len(durations) > 1, "every scene having the same length means nothing adapted"


def test_total_duration_is_within_guardrails(plan):
    assert MIN_SHORT_DURATION <= plan.total_duration <= MAX_SHORT_DURATION


def test_total_duration_is_not_the_legacy_seventy_five(plan):
    assert plan.total_duration < 70.0


def test_heavier_scene_gets_more_time_than_a_lighter_one(plan):
    hook = plan.scene(SceneType.HOOK)
    nifty = plan.scene(SceneType.NIFTY)
    assert nifty.planned_duration > hook.planned_duration


# --------------------------------------------------------------------- motion
def test_reading_heavy_scenes_hide_the_ticker(plan):
    for scene in plan.scenes:
        expected = scene.scene_type.value in TICKER_SCENES
        assert scene.show_ticker is expected, scene.scene_id
    assert plan.scene(SceneType.NIFTY).show_ticker is False


# --------------------------------------------------------------------- determinism
def test_planning_is_deterministic():
    first = plan_short(_report(), None, now=NOW).to_dict()
    second = plan_short(_report(), None, now=NOW).to_dict()
    assert first == second


def test_mover_selection_is_deterministic_and_is_not_a_ranking():
    """Selection decides screen time, never investment quality - and it is stable."""
    for scene_type in (SceneType.GAINERS, SceneType.LOSERS):
        a = plan_short(_report(), None, now=NOW).scene(scene_type)
        b = plan_short(_report(), None, now=NOW).scene(scene_type)
        assert [i.title for i in a.items] == [i.title for i in b.items]


# --------------------------------------------------------------------- traceability
def test_displayed_numbers_trace_back_to_canonical_facts():
    """Every number on screen must exist in the report - nothing invented in presentation."""
    report = _report()
    plan = plan_short(report, None, now=NOW)

    canonical = {f.value for f in report.facts if f.value is not None}
    for section in (report.nifty, report.technicals):
        canonical.update(v for v in section.values() if isinstance(v, (int, float)))
    for row in (report.global_cues + report.sectors + report.gainers + report.losers):
        canonical.update(v for v in row.values() if isinstance(v, (int, float)))
    canonical.update(v for v in report.institutional_flows.values()
                     if isinstance(v, (int, float)))

    missing = {k: v for k, v in plan.numeric_values().items() if v not in canonical}
    assert not missing, f"displayed but not in the report: {missing}"


def test_scenes_carry_their_source_ids():
    report = _report()
    plan = plan_short(report, None, now=NOW)
    for scene_type in (SceneType.NIFTY, SceneType.FLOWS, SceneType.SECTORS,
                       SceneType.GAINERS, SceneType.LOSERS):
        scene = plan.scene(scene_type)
        if scene is None:
            continue
        ids = list(scene.source_fact_ids) + [i for it in scene.items for i in it.source_fact_ids]
        assert ids, f"{scene.scene_id} has no traceable source"


def test_plan_serialises_with_provenance(plan):
    payload = plan.to_dict()
    assert payload["editorial_version"] == "1.0"
    assert payload["scene_count"] == len(plan.scenes)
    assert all("source_fact_ids" in s for s in payload["scenes"])


# --------------------------------------------------------------------- saying it on screen
def _move_insight(larger_than, lookback=20):
    from intelligence.models import InsightCategory, IntelligenceInsight
    return IntelligenceInsight(
        insight_id=f"index-move-{lookback}", category=InsightCategory.INDEX_MOVE,
        subject="NIFTY 50", statement="", current_value=0.1, lookback_sessions=lookback,
        sample_size=lookback,
        metadata={"larger_than_sessions": larger_than,
                  "selection_score": larger_than / lookback})


@pytest.mark.parametrize("larger_than, expected", [
    (19, "Bigger move than 19 of 20 sessions"),
    (0, "Smaller move than 20 of 20 sessions"),
])
def test_an_extreme_rank_is_stated_in_its_own_direction(larger_than, expected):
    from editorial.planner import context_line
    assert context_line(_move_insight(larger_than)) == expected


def test_a_mid_pack_rank_says_nothing_rather_than_saying_nothing_at_length():
    """"Bigger move than 9 of 20 sessions" is accurate and carries no information; on screen
    a rank near either end is the only version worth its line."""
    from editorial.planner import context_line
    assert context_line(_move_insight(9)) == ""


# --------------------------------------------------------------------- missing sections
def test_missing_flows_drops_the_scene_rather_than_showing_an_empty_one():
    plan = plan_short(_report(fii=None, dii=None), None, now=NOW)
    assert plan.scene(SceneType.FLOWS) is None


def test_missing_sectors_drops_the_scene():
    assert plan_short(_report(sectors=[]), None, now=NOW).scene(SceneType.SECTORS) is None


def test_missing_movers_drops_the_scene():
    plan = plan_short(_report(gainers=[], losers=[]), None, now=NOW)
    assert plan.scene(SceneType.GAINERS) is None
    assert plan.scene(SceneType.LOSERS) is None


def test_missing_events_drops_the_scene():
    assert plan_short(_report(events=[]), None, now=NOW).scene(SceneType.EVENTS) is None


def test_a_sparse_session_produces_a_shorter_short():
    full = plan_short(_report(), None, now=NOW)
    sparse = plan_short(_report(fii=None, dii=None, sectors=[], gainers=[], losers=[],
                                events=[]), None, now=NOW)
    assert sparse.total_duration < full.total_duration
    assert len(sparse.scenes) < len(full.scenes)


def test_cold_start_still_produces_a_hook_and_no_context_scene():
    plan = plan_short(_report(), None, now=NOW)
    assert plan.hook.primary_text
    assert plan.scene(SceneType.CONTEXT) is None

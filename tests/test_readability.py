"""Reading-time estimation and the readability publication gate."""
import pytest

from conftest import NOW
from conftest_intelligence import movers, session_report, trading_sessions

from editorial import estimate_read_seconds, plan_short
from editorial.config import (BASE_PROCESSING_SECONDS, CARD_SECONDS, CHART_SECONDS,
                              MAX_MAJOR_CARDS, WORDS_PER_SECOND, bounds_for)
from editorial.models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from editorial.readability import apply_timing, estimate_scene, fits, trim_to_fit
from qa.readability_qa import BLOCK_READ_RATIO, check_plan, check_scene

SESSION = trading_sessions(1)[0]


def _scene(scene_type=SceneType.SECTORS, primary="SECTOR CHECK", secondary="", items=(),
           chart=False, value=""):
    scene = ScenePlan(scene_id=scene_type.value.lower(), scene_type=scene_type,
                      primary_text=primary, secondary_text=secondary,
                      primary_value=value, has_chart=chart, items=list(items))
    return apply_timing(scene)


# --------------------------------------------------------------------- the estimator
def test_estimate_grows_with_words():
    assert estimate_read_seconds(20) > estimate_read_seconds(5)


def test_estimate_includes_base_processing_time():
    assert estimate_read_seconds(0) == pytest.approx(BASE_PROCESSING_SECONDS)


def test_each_card_adds_time():
    assert (estimate_read_seconds(10, cards=3) - estimate_read_seconds(10, cards=1)
            == pytest.approx(2 * CARD_SECONDS))


def test_a_chart_adds_its_own_allowance():
    assert (estimate_read_seconds(10, has_chart=True) - estimate_read_seconds(10)
            == pytest.approx(CHART_SECONDS))


def test_words_are_priced_at_the_documented_reading_speed():
    assert (estimate_read_seconds(26) - estimate_read_seconds(0)
            == pytest.approx(26 / WORDS_PER_SECOND))


def test_numbers_are_glanced_not_read():
    """A formatted value is one fixation, already priced - counting it as prose too is what
    made the planner delete its own content."""
    plain = _scene(items=[EditorialItem(title="IT")])
    with_value = _scene(items=[EditorialItem(title="IT", value="+1.40%")])
    difference = estimate_scene(with_value) - estimate_scene(plain)
    assert difference < 1.0, "a single number must not cost a second of reading"


# --------------------------------------------------------------------- durations
def test_simple_scene_gets_a_short_duration():
    scene = _scene(primary="NIFTY", value="-1.20%", scene_type=SceneType.NIFTY)
    low, high = bounds_for("NIFTY")
    assert low <= scene.planned_duration <= high


def test_duration_expands_with_reading_load():
    light = _scene(items=[EditorialItem(title="IT", value="+1.4%")])
    heavy = _scene(items=[EditorialItem(title="IT", value="+1.4%",
                                        note="a considerably longer supporting note here"),
                          EditorialItem(title="Metal", value="-1.1%",
                                        note="another fairly long supporting note")])
    assert heavy.planned_duration > light.planned_duration


def test_duration_stays_within_its_bounds():
    huge = _scene(secondary=" ".join(["word"] * 60),
                  items=[EditorialItem(title="x " * 30) for _ in range(3)])
    low, high = bounds_for("SECTORS")
    assert low <= huge.planned_duration <= high


def test_overloaded_scene_does_not_fit():
    huge = _scene(secondary=" ".join(["word"] * 60))
    assert not fits(huge)


def test_trim_drops_items_until_the_scene_fits():
    scene = _scene(items=[EditorialItem(title=f"Sector {i}", value="+1.0%",
                                        note="a reasonably long supporting note")
                          for i in range(6)])
    dropped = trim_to_fit(scene, minimum_items=1)
    assert dropped, "an over-full scene must shed rows"
    assert fits(scene)


def test_trim_respects_a_minimum_so_a_scene_is_never_emptied():
    scene = _scene(items=[EditorialItem(title="x " * 40) for _ in range(4)])
    trim_to_fit(scene, minimum_items=2)
    assert len(scene.items) >= 2


# --------------------------------------------------------------------- the gate
def test_comfortable_scene_passes():
    scene = _scene(primary="NIFTY", value="-1.20%", secondary="Closed below its 20-day average",
                   scene_type=SceneType.NIFTY)
    result = check_scene(scene)
    assert result.status == "PASS"
    assert result.reasons == []


def test_scene_that_cannot_be_read_in_time_blocks():
    scene = _scene(secondary=" ".join(["word"] * 40))
    scene.planned_duration = 3.0            # forced, as a broken planner would
    result = check_scene(scene)
    assert result.status == "FAIL"
    assert any("to read but is on screen" in r for r in result.reasons)


def test_marginal_overrun_only_warns():
    scene = _scene(primary="SECTOR CHECK")
    scene.estimated_read_seconds = scene.planned_duration * 1.05
    result = check_scene(scene)
    assert result.status == "WARN"
    assert result.reasons == []
    assert result.warnings


def test_too_many_simultaneous_items_blocks():
    scene = _scene(items=[EditorialItem(title=f"S{i}", value="+1%")
                          for i in range(MAX_MAJOR_CARDS + 2)])
    scene.planned_duration = 6.0
    result = check_scene(scene)
    assert result.status == "FAIL"
    assert any("compete at once" in r for r in result.reasons)


def test_primary_text_over_the_hard_limit_blocks():
    scene = _scene(primary=" ".join(["word"] * 14))
    scene.planned_duration = 6.0
    scene.estimated_read_seconds = 1.0
    result = check_scene(scene)
    assert result.status == "FAIL"
    assert any("primary text" in r for r in result.reasons)


def test_zero_duration_blocks():
    scene = _scene(primary="X")
    scene.planned_duration = 0.0
    assert check_scene(scene).status == "FAIL"


def test_block_ratio_is_documented_and_lenient_enough_to_trust():
    assert 1.0 < BLOCK_READ_RATIO <= 1.5


# --------------------------------------------------------------------- whole plan
def _full_plan():
    report = session_report(
        SESSION, pct=-1.25, fii=-3810.0, dii=2100.0, vix=15.2,
        sectors=[{"name": "IT", "pct": 1.4}, {"name": "Metal", "pct": -1.1}],
        gainers=movers(["STOCK-A", "STOCK-B"]), losers=movers(["STOCK-F"]),
        events=[{"tag": "RESULTS", "text": "Large-cap quarterly results today",
                 "source": "GEMINI_SEARCH", "publisher": None}])
    return plan_short(report, None, now=NOW)


def test_a_planned_short_passes_readability():
    result = check_plan(_full_plan(), now=NOW)
    assert result.passed, result.blocking_issues
    assert result.status in ("PASS", "WARN")


def test_readability_report_covers_every_scene():
    plan = _full_plan()
    result = check_plan(plan, now=NOW)
    assert len(result.scenes) == len(plan.scenes)
    for scene in result.scenes:
        assert scene.duration > 0
        assert scene.caption_changes == 0


def test_readability_report_serialises():
    payload = check_plan(_full_plan(), now=NOW).to_dict()
    assert payload["status"] in ("PASS", "WARN", "FAIL")
    assert payload["checked_at"] == NOW.isoformat()
    assert all({"scene", "duration", "visible_words", "cards", "estimated_read_seconds",
                "status"} <= set(s) for s in payload["scenes"])


def test_an_empty_plan_blocks():
    result = check_plan(ShortsPlan(report_id="x"), now=NOW)
    assert not result.passed
    assert any("no scenes" in issue for issue in result.blocking_issues)


def test_an_over_long_short_blocks():
    plan = _full_plan()
    for scene in plan.scenes:
        scene.planned_duration = 20.0
    result = check_plan(plan, now=NOW)
    assert not result.passed
    assert any("ceiling" in issue for issue in result.blocking_issues)

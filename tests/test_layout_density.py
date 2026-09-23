"""Phase 4.1.2a: the same low-information ScenePlan should occupy more of the safe zone.

These are renderer-only tests - the plan itself (word counts, caps, ordering) is exactly the
Phase 4.1.2 plan and is covered by tests/test_editorial.py; nothing here re-asserts it.
"""
import datetime as dt

import pytest

from conftest import NOW, build_test_report
from conftest_intelligence import market, movers, trading_sessions

import video
from editorial import plan_short

SESSION = trading_sessions(1)[0]

TILES = [
    {"label": "GIFT NIFTY", "value": 25215.0, "pct": 0.30, "dec": 0, "prefix": ""},
    {"label": "DOW JONES", "value": 44120.0, "pct": 0.42, "dec": 0, "prefix": ""},
    {"label": "USD / INR", "value": 86.35, "pct": 0.12, "dec": 2, "prefix": ""},
]


def _losing(rows):
    """`movers()` stamps every row with a positive pct; losers_scene filters on sign, so
    fixtures that stand in for losers must actually carry a negative change."""
    for row in rows:
        row["pct"] = -abs(row["pct"])
    return rows


def _report(**kw):
    kw.setdefault("gainers", movers(["STOCK-A", "STOCK-B", "STOCK-C"]))
    kw.setdefault("losers", _losing(movers(["STOCK-F", "STOCK-G"])))
    kw.setdefault("sectors", [{"name": "IT", "pct": 1.4}, {"name": "Bank", "pct": 0.3},
                              {"name": "Metal", "pct": -1.1}, {"name": "Auto", "pct": -0.4}])
    kw.setdefault("flows", {"fii": -3810.0, "dii": 2100.0, "source": "NSE"})
    kw.setdefault("tiles", TILES)
    kw.setdefault("events", [
        {"tag": "RESULTS", "text": "Large-cap quarterly results today",
         "source": "GEMINI_SEARCH", "publisher": None},
        {"tag": "IPO", "text": "Example IPO opens", "source": "GEMINI_SEARCH", "publisher": None}])
    return build_test_report(market(SESSION, pct=-1.25, vix=15.2),
                             report_date=SESSION + dt.timedelta(days=1), **kw)


@pytest.fixture
def plan():
    return plan_short(_report(), None, now=NOW)


@pytest.fixture
def scenes(plan):
    return video.scenes_from_plan(plan)


def _scene(scenes, kind):
    return next(s for s in scenes if s.plan.scene_type.value == kind)


# --------------------------------------------------------------------- the metric itself
def test_layout_density_is_zero_for_an_empty_layer():
    class _Blank(video.Scene):
        def draw(self, d, L, t):
            pass

    empty = _Blank(5.0, [])
    assert video.content_bbox(empty) is None
    assert video.layout_density(empty) == 0.0


def test_layout_density_reflects_where_content_actually_ends(scenes):
    for scene in scenes:
        bbox = video.content_bbox(scene)
        if bbox is None:
            continue
        density = video.layout_density(scene)
        expected = max(0.0, min(1.0, (bbox[3] - video.TOP) / (video.BAND_BOTTOM - video.TOP)))
        assert density == pytest.approx(expected)


# --------------------------------------------------------------------- the fix
@pytest.mark.parametrize("kind", ["SECTORS", "GAINERS", "LOSERS", "FLOWS", "GLOBAL", "EVENTS"])
def test_the_scenes_phase_4_1_2_left_top_heavy_now_reach_past_the_top_third(scenes, kind):
    scene = _scene(scenes, kind)
    density = video.layout_density(scene)
    assert density >= video.MIN_LAYOUT_DENSITY, (
        f"{kind} content still ends in the top third of the safe zone (density={density})")


def test_check_layout_density_flags_nothing_on_the_full_plan(scenes):
    findings = video.check_layout_density(scenes)
    kinds = {f["scene"] for f in findings}
    assert kinds >= {"SECTORS", "GAINERS", "LOSERS", "FLOWS", "GLOBAL", "EVENTS"}
    top_heavy = [f for f in findings if f["top_heavy"]]
    assert not top_heavy, top_heavy


def test_hook_and_outro_are_exempt_from_the_density_check(scenes):
    findings = video.check_layout_density(scenes)
    kinds = {f["scene"] for f in findings}
    assert "HOOK" not in kinds and "OUTRO" not in kinds


# --------------------------------------------------------------------- nothing else moved
def test_no_information_was_added_by_the_new_layouts(plan):
    """This phase is renderer-only: word counts, card counts and captions are untouched."""
    for scene in plan.scenes:
        assert scene.caption_changes() == 0
    sectors = plan.scene("SECTORS")
    assert len(sectors.items) <= 3
    gainers = plan.scene("GAINERS")
    losers = plan.scene("LOSERS")
    assert len(gainers.items) <= 3
    assert len(losers.items) <= 3


def test_sectors_comparison_shows_strongest_then_weakest(scenes):
    """The comparison panel must not silently re-rank what the planner already ordered."""
    scene = _scene(scenes, "SECTORS")
    assert scene.plan.items[0].numeric >= scene.plan.items[-1].numeric


def test_a_single_context_item_still_gets_a_readable_panel(scenes):
    context = next((s for s in scenes if s.plan.scene_type.value == "CONTEXT"), None)
    if context is None:
        pytest.skip("no context scene for this synthetic report")
    bbox = video.content_bbox(context)
    assert bbox is not None
    assert bbox[3] - bbox[1] > 100, "a lone context card should not render as a sliver"


# --------------------------------------------------------------------- the lone-item case
# `main.demo_data()` (and plenty of quiet real sessions) produce exactly ONE global cue,
# event or context insight - the single most common way this phase's fix could regress back
# to a small card floating under the heading with the rest of the canvas empty. Covered
# directly, at the scene level, rather than hoping a synthetic multi-item plan stands in.
def _one_item_scene(kind, **item_kwargs):
    from editorial.models import EditorialItem, ScenePlan, SceneType
    from editorial.readability import apply_timing

    scene_plan = apply_timing(ScenePlan(
        scene_id=kind.lower(), scene_type=SceneType(kind), primary_text=kind,
        secondary_text="One item only", items=[EditorialItem(**item_kwargs)]))
    scene = video.RowsScene(scene_plan, scene_plan.planned_duration)
    scene.plan = scene_plan
    return scene


@pytest.mark.parametrize("kind, kwargs", [
    ("GLOBAL", dict(title="DOW JONES", value="+0.42%", positive=True)),
    ("EVENTS", dict(label="RESULTS", title="Sample: large-cap quarterly results")),
    ("CONTEXT", dict(label="DII", title="Net buying Rs 7,045 cr over 20 sessions")),
])
def test_a_lone_item_becomes_a_hero_panel_not_a_sliver(kind, kwargs):
    scene = _one_item_scene(kind, **kwargs)
    density = video.layout_density(scene)
    assert density >= video.MIN_LAYOUT_DENSITY, (
        f"a lone {kind} item still ends in the top third of the safe zone (density={density})")
    bbox = video.content_bbox(scene)
    assert bbox[3] - bbox[1] > 200, f"a lone {kind} item should read as a hero panel"

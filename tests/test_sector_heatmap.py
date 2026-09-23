"""Phase 4.1.2c / Packet 1: the sector heatmap.

Below MIN_SECTORS_FOR_HEATMAP the scene stays NARRATIVE (strongest/weakest) - covered by
tests/test_editorial.py's `test_sectors_show_strongest_and_weakest_not_a_heatmap`. Everything
here is about the SCAN/HEATMAP presentation: a reusable grid, deterministic colour scaling,
low text density at high data density, and traceability back to the canonical report.
"""
import pytest

from conftest import NOW
from conftest_intelligence import session_report, trading_sessions

import video
from editorial import MAX_HEATMAP_CARDS, MIN_SECTORS_FOR_HEATMAP, SceneType, plan_short
from editorial.config import HEATMAP_CLIP_PCT
from qa.readability_qa import check_scene

SESSION = trading_sessions(1)[0]


def _sectors(n, start_pct=3.0, step=0.4):
    """`n` deterministic sectors, strongest first by construction (so ordering assertions
    aren't just re-testing the sort)."""
    return [{"name": f"SEC{i:02d}", "pct": round(start_pct - i * step, 3)} for i in range(n)]


def _plan_with_sectors(n, **kw):
    report = session_report(SESSION, sectors=_sectors(n), **kw)
    return plan_short(report, None, now=NOW)


def _heatmap_scene(plan):
    return plan.scene(SceneType.SECTORS)


# --------------------------------------------------------------------- colour scaling
def test_positive_cell_blends_toward_green():
    colour = video.heatmap_cell_colour(1.0)
    assert colour != video.CARD
    # more green than the base card, not more red
    assert colour[1] >= video.CARD[1]


def test_negative_cell_blends_toward_red():
    colour = video.heatmap_cell_colour(-1.0)
    assert colour != video.CARD
    assert colour[0] >= video.CARD[0]


def test_zero_is_visually_neutral():
    assert video.heatmap_intensity(0.0) == 0.0
    assert video.heatmap_cell_colour(0.0) == video.CARD


def test_near_zero_stays_close_to_neutral():
    intensity = video.heatmap_intensity(0.05)
    assert 0 < intensity < 0.05, "a near-flat sector must not look like a strong mover"


def test_magnitude_orders_intensity():
    small = abs(video.heatmap_intensity(0.5))
    large = abs(video.heatmap_intensity(2.5))
    assert large > small, "a bigger move must read as more saturated than a smaller one"


def test_one_outlier_does_not_flatten_the_rest_of_the_scale():
    """Clipping, not compression: an extreme value maxes out instead of shrinking every
    other cell's intensity toward it."""
    normal = video.heatmap_intensity(1.0)
    extreme = video.heatmap_intensity(1.0)   # unaffected by a second, unrelated outlier call
    outlier = video.heatmap_intensity(50.0)
    assert outlier == 1.0
    assert normal == extreme


def test_intensity_is_clipped_at_the_documented_bound():
    assert video.heatmap_intensity(HEATMAP_CLIP_PCT) == 1.0
    assert video.heatmap_intensity(HEATMAP_CLIP_PCT * 10) == 1.0
    assert video.heatmap_intensity(-HEATMAP_CLIP_PCT * 10) == -1.0


def test_scaling_is_deterministic():
    assert video.heatmap_intensity(1.7) == video.heatmap_intensity(1.7)
    assert video.heatmap_cell_colour(-2.3) == video.heatmap_cell_colour(-2.3)


# --------------------------------------------------------------------- mode selection
def test_just_under_the_threshold_stays_narrative():
    n = MIN_SECTORS_FOR_HEATMAP - 1
    plan = _plan_with_sectors(n)
    scene = _heatmap_scene(plan)
    assert scene.metadata.get("presentation") != "HEATMAP"


def test_the_threshold_itself_engages_heatmap_mode():
    plan = _plan_with_sectors(MIN_SECTORS_FOR_HEATMAP)
    scene = _heatmap_scene(plan)
    assert scene.metadata.get("presentation") == "HEATMAP"
    assert len(scene.items) == MIN_SECTORS_FOR_HEATMAP


@pytest.mark.parametrize("n", [5, 9, 13])
def test_cell_count_matches_available_sectors_up_to_the_cap(n):
    plan = _plan_with_sectors(n)
    scene = _heatmap_scene(plan)
    assert len(scene.items) == min(n, MAX_HEATMAP_CARDS)


def test_more_than_the_cap_is_truncated_and_recorded_as_omitted():
    n = MAX_HEATMAP_CARDS + 3
    plan = _plan_with_sectors(n)
    scene = _heatmap_scene(plan)
    assert len(scene.items) == MAX_HEATMAP_CARDS
    omitted = [o for o in plan.omitted if o["scene"] == "sectors"]
    assert omitted, "sectors beyond the cap must be recorded, not silently dropped"
    assert sum(o.get("count", 0) for o in omitted) == 3


# --------------------------------------------------------------------- ordering
def test_ordering_is_strongest_to_weakest():
    plan = _plan_with_sectors(9)
    scene = _heatmap_scene(plan)
    values = [item.numeric for item in scene.items]
    assert values == sorted(values, reverse=True)


def test_ordering_is_deterministic_across_runs():
    a = _heatmap_scene(_plan_with_sectors(9)).items
    b = _heatmap_scene(_plan_with_sectors(9)).items
    assert [i.title for i in a] == [i.title for i in b]


# --------------------------------------------------------------------- traceability & content
def test_every_cell_traces_to_a_canonical_sector_fact():
    report = session_report(SESSION, sectors=_sectors(9))
    plan = plan_short(report, None, now=NOW)
    scene = _heatmap_scene(plan)
    canonical = {s["name"]: s["change_pct"] for s in report.sectors}
    for item in scene.items:
        assert item.title in canonical
        assert item.numeric == canonical[item.title]
        assert item.source_fact_ids, f"{item.title} has no traceable source"


def test_no_prose_is_added_per_cell():
    plan = _plan_with_sectors(13)
    scene = _heatmap_scene(plan)
    for item in scene.items:
        assert item.note == ""
        assert item.label == ""
    # low text density regardless of cell count: no catalyst/technical/recommendation text
    assert scene.visible_words() < 40


def test_breadth_summary_reflects_the_actual_split():
    sectors = [{"name": "A", "pct": 1.0}, {"name": "B", "pct": 2.0}, {"name": "C", "pct": -1.0},
              {"name": "D", "pct": -2.0}, {"name": "E", "pct": -3.0}]
    report = session_report(SESSION, sectors=sectors)
    plan = plan_short(report, None, now=NOW)
    scene = _heatmap_scene(plan)
    assert scene.secondary_text == "2 higher • 3 lower"


def test_planning_does_not_mutate_the_canonical_report():
    report = session_report(SESSION, sectors=_sectors(9))
    before = report.to_json()
    plan_short(report, None, now=NOW)
    assert report.to_json() == before


# --------------------------------------------------------------------- readability
@pytest.mark.parametrize("n", [5, 9, 13])
def test_heatmap_stays_within_the_sectors_scene_bound(n):
    plan = _plan_with_sectors(n)
    scene = _heatmap_scene(plan)
    from editorial.config import bounds_for
    _, max_seconds = bounds_for("SECTORS")
    assert scene.planned_duration <= max_seconds
    assert scene.estimated_read_seconds <= max_seconds + 0.01


@pytest.mark.parametrize("n", [5, 9, 13])
def test_heatmap_readability_qa_never_fails(n):
    plan = _plan_with_sectors(n)
    scene = _heatmap_scene(plan)
    result = check_scene(scene)
    assert result.status in ("PASS", "WARN"), result.reasons


def test_a_heatmap_sized_scene_would_have_blocked_under_the_narrative_card_limit():
    """Confirms the mode-aware QA branch is actually doing something: the OLD narrative
    MAX_MAJOR_CARDS rule would have blocked a 13-cell scene outright."""
    from editorial.config import MAX_MAJOR_CARDS
    plan = _plan_with_sectors(13)
    scene = _heatmap_scene(plan)
    assert len(scene.items) > MAX_MAJOR_CARDS
    assert check_scene(scene).status != "FAIL"


# --------------------------------------------------------------------- rendered layout
def _rendered_sector_scene(n):
    plan = _plan_with_sectors(n)
    scenes = video.scenes_from_plan(plan)
    return next(s for s in scenes if s.plan.scene_type is SceneType.SECTORS)


@pytest.mark.parametrize("n", [5, 9, 13])
def test_heatmap_layout_stays_within_the_safe_content_bounds(n):
    scene = _rendered_sector_scene(n)
    bbox = video.content_bbox(scene)
    assert bbox is not None
    left, top, right, bottom = bbox
    assert left >= 0 and right <= video.W
    assert top >= 0
    assert bottom <= video.BAND_BOTTOM + 1   # allow sub-pixel rounding


def test_heatmap_occupies_more_than_the_top_third():
    scene = _rendered_sector_scene(13)
    assert video.layout_density(scene) >= video.MIN_LAYOUT_DENSITY


def test_draw_heatmap_grid_handles_an_empty_list_without_raising():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (video.W, video.H))
    d = ImageDraw.Draw(img)
    video.draw_heatmap_grid(d, [], video.X0, video.X1, video.TOP, video.BAND_BOTTOM, 1.0)

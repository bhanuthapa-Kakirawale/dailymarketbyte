"""Phase 4.1.2c final polish: the GLOBAL compact scan grid, and GLOBAL/FLOWS duration tuning.

GLOBAL: below this packet, GLOBAL always fell to a narrative card layout capped at 3 cues.
It is now a scan grid (like the sector heatmap and the ranked-mover lists) that can show up to
`MAX_GLOBAL_CUES` (4) cues, balanced whether there are 2, 3 or 4 of them.

FLOWS: unchanged visually (two large panels); only its reading-time estimate changed, since two
highly scannable values were being priced as if read serially, keeping the scene on screen
longer than it needed to be.
"""
import datetime as dt

import pytest

from conftest import NOW, build_test_report
from conftest_intelligence import market, trading_sessions

import video
from editorial import MAX_GLOBAL_CUES, SceneType, plan_short
from editorial.config import bounds_for
from qa.readability_qa import check_plan, check_scene

SESSION = trading_sessions(1)[0]

# Drawn from GLOBAL_CUE_PREFERENCE (editorial/planner.py) so relevance-order assertions are
# meaningful rather than incidental.
ALL_CUES = [
    {"label": "GIFT NIFTY", "value": 25215.0, "pct": 0.30, "dec": 0, "prefix": ""},
    {"label": "DOW JONES", "value": 44120.0, "pct": 0.42, "dec": 0, "prefix": ""},
    {"label": "NASDAQ", "value": 19850.0, "pct": 0.91, "dec": 0, "prefix": ""},
    {"label": "BRENT CRUDE", "value": 78.2, "pct": -1.10, "dec": 2, "prefix": "$"},
    {"label": "USD / INR", "value": 86.35, "pct": 0.12, "dec": 2, "prefix": ""},
    {"label": "GOLD", "value": 2450.0, "pct": 0.35, "dec": 0, "prefix": "$"},
]


def _report(tiles, **kw):
    kw.setdefault("flows", {"fii": -3810.0, "dii": 2100.0, "source": "NSE"})
    return build_test_report(market(SESSION, pct=-1.25, vix=15.2),
                             report_date=SESSION + dt.timedelta(days=1), tiles=tiles, **kw)


def _global_scene(plan):
    return plan.scene(SceneType.GLOBAL)


# --------------------------------------------------------------------- layout by count
@pytest.mark.parametrize("n", [2, 3, 4])
def test_global_grid_shows_exactly_the_available_cues(n):
    plan = plan_short(_report(ALL_CUES[:n]), None, now=NOW)
    scene = _global_scene(plan)
    assert scene is not None
    assert len(scene.items) == n
    assert scene.metadata.get("presentation") == "GLOBAL_SCAN"


def test_one_cue_is_not_enough_for_a_scene():
    """A single validated cue does not earn a GLOBAL scene - `_global_scene` requires at
    least two, exactly as before this packet."""
    plan = plan_short(_report(ALL_CUES[:1]), None, now=NOW)
    assert _global_scene(plan) is None


# --------------------------------------------------------------------- no invented cue
def test_never_more_cues_than_are_actually_validated():
    for n in (2, 3):
        plan = plan_short(_report(ALL_CUES[:n]), None, now=NOW)
        assert len(_global_scene(plan).items) == n


def test_more_than_the_cap_is_truncated_and_recorded_as_omitted():
    plan = plan_short(_report(ALL_CUES), None, now=NOW)   # 6 available, cap is 4
    scene = _global_scene(plan)
    assert len(scene.items) == MAX_GLOBAL_CUES
    omitted = [o for o in plan.omitted if o["scene"] == "global"]
    assert omitted, "cues beyond the cap must be recorded, not silently dropped"
    assert sum(o.get("count", 0) for o in omitted) == 6 - MAX_GLOBAL_CUES


# --------------------------------------------------------------------- deterministic order
def test_cue_order_follows_relevance_preference():
    plan = plan_short(_report([ALL_CUES[2], ALL_CUES[0], ALL_CUES[1]]), None, now=NOW)
    titles = [i.title for i in _global_scene(plan).items]
    assert titles == ["GIFT NIFTY", "DOW JONES", "NASDAQ"], "must follow GLOBAL_CUE_PREFERENCE"


def test_cue_selection_is_deterministic_across_runs():
    a = _global_scene(plan_short(_report(ALL_CUES), None, now=NOW)).items
    b = _global_scene(plan_short(_report(ALL_CUES), None, now=NOW)).items
    assert [i.title for i in a] == [i.title for i in b]


# --------------------------------------------------------------------- traceability & safety
def test_every_cue_traces_to_a_canonical_fact():
    report = _report(ALL_CUES[:4])
    plan = plan_short(report, None, now=NOW)
    canonical = {c["label"]: c["change_pct"] for c in report.global_cues}
    for item in _global_scene(plan).items:
        assert item.title in canonical
        assert item.numeric == canonical[item.title]
        assert item.source_fact_ids, f"{item.title} has no traceable source"


def test_public_text_covers_every_displayed_cue():
    plan = plan_short(_report(ALL_CUES[:4]), None, now=NOW)
    scene = _global_scene(plan)
    fields = " ".join(plan.public_text().values())
    for item in scene.items:
        assert item.title in fields
        assert item.value in fields


def test_planning_does_not_mutate_the_canonical_report():
    report = _report(ALL_CUES[:4])
    before = report.to_json()
    plan_short(report, None, now=NOW)
    assert report.to_json() == before


# --------------------------------------------------------------------- duration: GLOBAL
@pytest.mark.parametrize("n", [2, 3, 4])
def test_global_duration_lands_in_the_tightened_band(n):
    plan = plan_short(_report(ALL_CUES[:n]), None, now=NOW)
    scene = _global_scene(plan)
    low, high = bounds_for("GLOBAL")
    assert (low, high) == (3.5, 4.5)
    assert low <= scene.planned_duration <= high


def test_global_readability_qa_never_fails():
    for n in (2, 3, 4):
        plan = plan_short(_report(ALL_CUES[:n]), None, now=NOW)
        result = check_scene(_global_scene(plan))
        assert result.status in ("PASS", "WARN"), result.reasons


def test_a_four_cue_global_scene_would_have_blocked_under_the_old_narrative_cap():
    """Confirms the GLOBAL_SCAN QA branch is doing something: the old narrative
    MAX_MAJOR_CARDS rule (3) would have blocked a 4-cue scene outright."""
    from editorial.config import MAX_MAJOR_CARDS
    plan = plan_short(_report(ALL_CUES[:4]), None, now=NOW)
    scene = _global_scene(plan)
    assert len(scene.items) > MAX_MAJOR_CARDS
    assert check_scene(scene).status != "FAIL"


# --------------------------------------------------------------------- duration: FLOWS
def test_flows_duration_is_tighter_than_the_old_seven_second_ceiling():
    plan = plan_short(_report(ALL_CUES[:2]), None, now=NOW)
    scene = plan.scene(SceneType.FLOWS)
    assert scene is not None
    assert scene.metadata.get("presentation") == "FLOWS_SCAN"
    low, high = bounds_for("FLOWS")
    assert (low, high) == (3.5, 6.0)
    assert low <= scene.planned_duration <= high
    # An ordinary two-item FLOWS scene with no streak line must land near the tightened
    # floor, not the old (4.0, 7.0) range.
    assert scene.planned_duration <= 4.5


def test_flows_readability_qa_never_fails():
    plan = plan_short(_report(ALL_CUES[:2]), None, now=NOW)
    result = check_scene(plan.scene(SceneType.FLOWS))
    assert result.status in ("PASS", "WARN"), result.reasons


# --------------------------------------------------------------------- rendered layout
def _rendered_global_scene(n):
    plan = plan_short(_report(ALL_CUES[:n]), None, now=NOW)
    scenes = video.scenes_from_plan(plan)
    return next(s for s in scenes if s.plan.scene_type is SceneType.GLOBAL)


@pytest.mark.parametrize("n", [2, 3, 4])
def test_global_grid_layout_stays_within_the_safe_content_bounds(n):
    scene = _rendered_global_scene(n)
    bbox = video.content_bbox(scene)
    assert bbox is not None
    left, top, right, bottom = bbox
    assert left >= 0 and right <= video.W
    assert top >= 0
    assert bottom <= video.BAND_BOTTOM + 1   # allow sub-pixel rounding


def test_draw_global_grid_handles_an_empty_list_without_raising():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (video.W, video.H))
    d = ImageDraw.Draw(img)
    video.draw_global_grid(d, [], video.X0, video.X1, video.TOP, video.BAND_BOTTOM, 1.0)


# --------------------------------------------------------------------- whole-plan gate
def test_full_plan_readability_gate_still_passes():
    plan = plan_short(_report(ALL_CUES), None, now=NOW)
    report = check_plan(plan, now=NOW)
    assert report.passed, report.blocking_issues

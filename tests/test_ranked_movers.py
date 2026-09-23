"""Phase 4.1.2c / Packet 2: the reusable ranked-mover scan list (Top 5 Gainers / Top 5
Losers). Standalone scene builders (`editorial.gainers_scene` / `editorial.losers_scene`) and
a standalone renderer (`video.draw_ranked_mover_list`) - not yet wired into `plan_short`'s
builder list, so these are exercised directly rather than through a full plan.
"""
import pytest

from conftest import NOW
from conftest_intelligence import session_report, trading_sessions

import video
from core.content_safety import SafetyStatus, scan_publication
from editorial import MAX_RANKED_MOVERS, SceneType, gainers_scene, losers_scene
from qa.readability_qa import check_scene

SESSION = trading_sessions(1)[0]


def _rows(pcts, prefix="STOCK"):
    """Deterministic mover rows with distinct percentages, shaped as `get_movers` produces
    them (see `conftest_intelligence.movers`), one per symbol in list order."""
    rows = []
    for i, pct in enumerate(pcts):
        rows.append({"symbol": f"{prefix}-{i:02d}", "name": f"{prefix}-{i:02d} Ltd",
                     "close": 100.0, "pct": pct, "volx": 1.5, "reason": "",
                     "reason_source": "NO_VERIFIED_CATALYST"})
    return rows


def _report(gainer_pcts=(), loser_pcts=()):
    return session_report(SESSION, gainers=_rows(gainer_pcts, "GAIN"),
                          losers=_rows(loser_pcts, "LOSE"))


# --------------------------------------------------------------------- five items, up to five
def test_five_gainers_shown_when_five_are_available():
    report = _report(gainer_pcts=[8.4, 6.7, 5.9, 4.8, 4.1])
    scene = gainers_scene(report)
    assert len(scene.items) == 5


def test_five_losers_shown_when_five_are_available():
    report = _report(loser_pcts=[-7.2, -5.5, -4.0, -3.1, -2.0])
    scene = losers_scene(report)
    assert len(scene.items) == 5


def test_more_than_five_gainers_is_capped_at_five():
    report = _report(gainer_pcts=[9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0])
    scene = gainers_scene(report)
    assert len(scene.items) == MAX_RANKED_MOVERS


def test_fewer_than_five_gainers_shows_fewer_not_invented():
    report = _report(gainer_pcts=[3.5, 2.1])
    scene = gainers_scene(report)
    assert len(scene.items) == 2
    assert {i.title for i in scene.items} == {"GAIN-00", "GAIN-01"}


def test_fewer_than_five_losers_shows_fewer_not_invented():
    report = _report(loser_pcts=[-1.5])
    scene = losers_scene(report)
    assert len(scene.items) == 1
    assert scene.items[0].title == "LOSE-00"


def test_no_gainers_returns_no_scene():
    report = _report(loser_pcts=[-2.0])
    assert gainers_scene(report) is None


def test_no_losers_returns_no_scene():
    report = _report(gainer_pcts=[2.0])
    assert losers_scene(report) is None


# --------------------------------------------------------------------- separate scenes
def test_gainers_and_losers_are_separate_scene_types():
    report = _report(gainer_pcts=[3.0], loser_pcts=[-3.0])
    g, l = gainers_scene(report), losers_scene(report)
    assert g.scene_type is SceneType.GAINERS
    assert l.scene_type is SceneType.LOSERS
    assert g.scene_id != l.scene_id


# --------------------------------------------------------------------- ordering
def test_gainers_ordered_largest_to_smallest_positive():
    report = _report(gainer_pcts=[2.1, 8.4, 5.0])
    scene = gainers_scene(report)
    values = [i.numeric for i in scene.items]
    assert values == sorted(values, reverse=True)
    assert values[0] == 8.4


def test_losers_ordered_by_largest_absolute_move_first():
    report = _report(loser_pcts=[-2.1, -8.4, -5.0])
    scene = losers_scene(report)
    values = [i.numeric for i in scene.items]
    assert values == sorted(values)
    assert values[0] == -8.4


def test_ordering_is_deterministic_across_runs():
    report = _report(gainer_pcts=[4.0, 6.0, 2.0, 9.0])
    a = [i.title for i in gainers_scene(report).items]
    b = [i.title for i in gainers_scene(_report(gainer_pcts=[4.0, 6.0, 2.0, 9.0])).items]
    assert a == b


def test_rank_is_assigned_in_display_order():
    report = _report(gainer_pcts=[9.0, 6.0, 3.0])
    scene = gainers_scene(report)
    assert [i.rank for i in scene.items] == [1, 2, 3]


# --------------------------------------------------------------------- zero/invalid excluded
def test_zero_move_gainer_is_excluded():
    report = _report(gainer_pcts=[3.0, 0.0, 1.5])
    scene = gainers_scene(report)
    assert 0.0 not in [i.numeric for i in scene.items]
    assert len(scene.items) == 2


def test_zero_move_loser_is_excluded():
    report = _report(loser_pcts=[-3.0, 0.0, -1.5])
    scene = losers_scene(report)
    assert 0.0 not in [i.numeric for i in scene.items]
    assert len(scene.items) == 2


# --------------------------------------------------------------------- positive/negative presentation
def test_gainers_are_all_marked_positive():
    report = _report(gainer_pcts=[1.0, 2.0, 3.0])
    scene = gainers_scene(report)
    assert all(i.positive is True for i in scene.items)
    assert all(i.value.startswith("+") for i in scene.items)


def test_losers_are_all_marked_negative():
    report = _report(loser_pcts=[-1.0, -2.0, -3.0])
    scene = losers_scene(report)
    assert all(i.positive is False for i in scene.items)
    assert all(i.value.startswith("-") for i in scene.items)


# --------------------------------------------------------------------- magnitude scaling
def test_scan_max_abs_pct_matches_the_largest_shown_move():
    report = _report(gainer_pcts=[8.4, 6.7, 4.1])
    scene = gainers_scene(report)
    assert scene.metadata["scan_max_abs_pct"] == 8.4


def test_scan_max_abs_pct_is_local_to_the_scene_not_a_fixed_constant():
    small = gainers_scene(_report(gainer_pcts=[1.0, 0.5]))
    large = gainers_scene(_report(gainer_pcts=[9.0, 4.0]))
    assert small.metadata["scan_max_abs_pct"] == 1.0
    assert large.metadata["scan_max_abs_pct"] == 9.0


# --------------------------------------------------------------------- traceability
def test_every_row_traces_to_a_canonical_gainer_fact():
    report = _report(gainer_pcts=[8.4, 6.7, 4.1])
    scene = gainers_scene(report)
    canonical = {r["symbol"]: r["change_pct"] for r in report.gainers}
    for item in scene.items:
        assert item.title in canonical
        assert item.numeric == canonical[item.title]
        assert item.source_fact_ids, f"{item.title} has no traceable source"


def test_every_row_traces_to_a_canonical_loser_fact():
    report = _report(loser_pcts=[-8.4, -6.7, -4.1])
    scene = losers_scene(report)
    canonical = {r["symbol"]: r["change_pct"] for r in report.losers}
    for item in scene.items:
        assert item.title in canonical
        assert item.numeric == canonical[item.title]
        assert item.source_fact_ids, f"{item.title} has no traceable source"


def test_planning_does_not_mutate_the_canonical_report():
    report = _report(gainer_pcts=[8.4, 6.7], loser_pcts=[-5.0])
    before = report.to_json()
    gainers_scene(report)
    losers_scene(report)
    assert report.to_json() == before


# --------------------------------------------------------------------- catalyst policy / safety
def test_no_catalyst_or_label_text_on_rows():
    report = _report(gainer_pcts=[8.4, 6.7, 4.1])
    scene = gainers_scene(report)
    for item in scene.items:
        assert item.note == ""
        assert item.label == ""


@pytest.mark.parametrize("side,pcts", [("gainers", [8.4, 6.7, 4.1]), ("losers", [-8.4, -6.7])])
def test_public_text_passes_the_content_safety_scan(side, pcts):
    report = _report(gainer_pcts=pcts if side == "gainers" else (),
                     loser_pcts=pcts if side == "losers" else ())
    scene = gainers_scene(report) if side == "gainers" else losers_scene(report)
    scan = scan_publication(scene.public_text())
    assert scan.status is SafetyStatus.SAFE


# --------------------------------------------------------------------- readability / safe bounds
def test_five_row_scan_never_fails_readability_qa():
    report = _report(gainer_pcts=[8.4, 6.7, 5.9, 4.8, 4.1])
    scene = gainers_scene(report)
    result = check_scene(scene)
    assert result.status in ("PASS", "WARN"), result.reasons


def test_a_five_row_scan_would_have_blocked_under_the_narrative_card_limit():
    """Confirms the SCAN-aware QA branch is doing something: MAX_MOVERS_DISPLAYED (3) would
    have blocked five rows under the old narrative rule."""
    from editorial.config import MAX_MOVERS_DISPLAYED
    report = _report(gainer_pcts=[8.4, 6.7, 5.9, 4.8, 4.1])
    scene = gainers_scene(report)
    assert len(scene.items) > MAX_MOVERS_DISPLAYED
    assert check_scene(scene).status != "FAIL"


def test_scan_scene_stays_within_its_bound():
    from editorial.config import bounds_for
    report = _report(gainer_pcts=[8.4, 6.7, 5.9, 4.8, 4.1])
    scene = gainers_scene(report)
    _, max_seconds = bounds_for("GAINERS")
    assert scene.planned_duration <= max_seconds


# --------------------------------------------------------------------- rendered layout
def _rendered_scene(scene_plan):
    scenes = video.scenes_from_plan(_wrap(scene_plan))
    return scenes[0]


class _FakePlan:
    def __init__(self, scene):
        self.scenes = [scene]


def _wrap(scene_plan):
    return _FakePlan(scene_plan)


@pytest.mark.parametrize("pcts", [[8.4, 6.7, 5.9, 4.8, 4.1], [3.0]])
def test_rendered_rows_stay_within_the_safe_content_bounds(pcts):
    report = _report(gainer_pcts=pcts)
    scene_plan = gainers_scene(report)
    scene = _rendered_scene(scene_plan)
    bbox = video.content_bbox(scene)
    assert bbox is not None
    left, top, right, bottom = bbox
    assert left >= 0 and right <= video.W
    assert top >= 0
    assert bottom <= video.BAND_BOTTOM + 1


def test_five_rows_occupy_more_than_the_top_third():
    report = _report(gainer_pcts=[8.4, 6.7, 5.9, 4.8, 4.1])
    scene = _rendered_scene(gainers_scene(report))
    assert video.layout_density(scene) >= video.MIN_LAYOUT_DENSITY


def test_draw_ranked_mover_list_handles_an_empty_list_without_raising():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (video.W, video.H))
    d = ImageDraw.Draw(img)
    video.draw_ranked_mover_list(d, [], video.X0, video.X1, video.TOP, video.BAND_BOTTOM, 1.0)

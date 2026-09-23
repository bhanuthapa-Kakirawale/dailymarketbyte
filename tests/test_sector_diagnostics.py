"""Phase 4.1.2c: regression coverage for the "missing sector heatmap" production defect.

Root cause (see the diagnosis report): the heatmap decision, editorial selection, canonical
adaptation and report construction all behaved correctly. The real defect was upstream, in
`market.get_sectors()`'s Yahoo fallback - most configured sector tickers structurally return
no daily-bar history via yfinance, so `get_sectors()` silently produced far fewer than
`MIN_SECTORS_FOR_HEATMAP` sectors, and `_sectors_scene()` correctly fell back to NARRATIVE for
the data it was actually given. These tests pin down that "correct behaviour given the data"
distinction, and the diagnostic fields added so this is visible without a manual investigation
next time.
"""
import pytest

from conftest import NOW
from conftest_intelligence import session_report, trading_sessions

from editorial import MIN_SECTORS_FOR_HEATMAP, SceneType, plan_short
from editorial.config import MAX_HEATMAP_CARDS, MAX_SECTORS_HIGHLIGHTED

SESSION = trading_sessions(1)[0]


def _sectors(n, start_pct=3.0, step=0.4):
    return [{"name": f"SEC{i:02d}", "pct": round(start_pct - i * step, 3)} for i in range(n)]


def _plan_with_sectors(sectors, **kw):
    report = session_report(SESSION, sectors=sectors, **kw)
    return plan_short(report, None, now=NOW)


def _sectors_scene(plan):
    return plan.scene(SceneType.SECTORS)


# --------------------------------------------------------------------- the actual defect shape
def test_a_partial_fetch_like_the_real_defect_falls_back_to_narrative_not_heatmap():
    """Reproduces exactly what the real run produced: only 3 of 12 configured sectors made it
    into the canonical report (the other 9 were dropped upstream in acquisition, never even
    reaching `report.sectors`). This must NOT crash, must NOT invent the missing sectors, and
    must correctly choose NARRATIVE - which is the right choice for 3 eligible sectors."""
    sectors = [{"name": "Pharma", "pct": 1.164543696201048},
              {"name": "Bank", "pct": 0.19863698116147077},
              {"name": "IT", "pct": -0.0819641615781741}]
    plan = _plan_with_sectors(sectors)
    scene = _sectors_scene(plan)
    assert scene is not None
    assert scene.metadata.get("presentation") == "NARRATIVE"
    assert len(scene.items) <= MAX_SECTORS_HIGHLIGHTED
    titles = {i.title for i in scene.items}
    assert titles <= {"Pharma", "Bank", "IT"}, "no sector may be invented"


# --------------------------------------------------------------------- mode threshold
def test_five_or_more_eligible_sectors_engages_heatmap():
    plan = _plan_with_sectors(_sectors(MIN_SECTORS_FOR_HEATMAP))
    scene = _sectors_scene(plan)
    assert scene.metadata.get("presentation") == "HEATMAP"


def test_fewer_than_five_eligible_sectors_stays_narrative():
    plan = _plan_with_sectors(_sectors(MIN_SECTORS_FOR_HEATMAP - 1))
    scene = _sectors_scene(plan)
    assert scene.metadata.get("presentation") == "NARRATIVE"


# --------------------------------------------------------------------- no data lost before planning
def test_every_canonical_sector_reaches_the_planner_when_eligible():
    """The planner must not itself be the place sectors go missing - everything
    `report.sectors` holds with a change_pct must be counted as eligible."""
    n = 9
    report = session_report(SESSION, sectors=_sectors(n))
    assert len(report.sectors) == n
    plan = plan_short(report, None, now=NOW)
    scene = _sectors_scene(plan)
    assert scene.metadata.get("sectors_eligible") == n


def test_sectors_with_no_change_pct_are_available_but_not_eligible():
    """A canonical sector fact can exist without a usable value (e.g. an unverified reading);
    `sectors_available` counts the canonical rows, `sectors_eligible` only the usable ones -
    the two must diverge correctly rather than being silently conflated."""
    sectors = _sectors(6) + [{"name": "Broken", "pct": None}]
    report = session_report(SESSION, sectors=sectors)
    plan = plan_short(report, None, now=NOW)
    scene = _sectors_scene(plan)
    assert scene.metadata.get("sectors_available") == 7
    assert scene.metadata.get("sectors_eligible") == 6


# --------------------------------------------------------------------- canonical values unchanged
def test_planning_does_not_mutate_or_alter_canonical_sector_values():
    sectors = [{"name": "Pharma", "pct": 1.164543696201048},
              {"name": "Bank", "pct": 0.19863698116147077},
              {"name": "IT", "pct": -0.0819641615781741}]
    report = session_report(SESSION, sectors=sectors)
    before = report.to_json()
    plan_short(report, None, now=NOW)
    assert report.to_json() == before

    canonical = {s["name"]: s["change_pct"] for s in report.sectors}
    plan = plan_short(report, None, now=NOW)
    for item in _sectors_scene(plan).items:
        assert item.numeric == canonical[item.title], "displayed value must match the canonical fact exactly"


# --------------------------------------------------------------------- no invented sector
def test_narrative_mode_never_shows_more_sectors_than_are_eligible():
    for n in (2, 3, 4):
        plan = _plan_with_sectors(_sectors(n))
        scene = _sectors_scene(plan)
        assert len(scene.items) <= n


def test_heatmap_mode_never_shows_more_sectors_than_are_eligible_or_the_cap():
    for n in (5, 8, MAX_HEATMAP_CARDS, MAX_HEATMAP_CARDS + 4):
        plan = _plan_with_sectors(_sectors(n))
        scene = _sectors_scene(plan)
        assert len(scene.items) == min(n, MAX_HEATMAP_CARDS)


# --------------------------------------------------------------------- traceability
def test_every_displayed_sector_traces_to_a_canonical_fact_in_both_modes():
    for sectors in (_sectors(3), _sectors(9)):
        report = session_report(SESSION, sectors=sectors)
        plan = plan_short(report, None, now=NOW)
        scene = _sectors_scene(plan)
        canonical = {s["name"]: s["change_pct"] for s in report.sectors}
        for item in scene.items:
            assert item.title in canonical
            assert item.numeric == canonical[item.title]
            assert item.source_fact_ids, f"{item.title} has no traceable source"


# --------------------------------------------------------------------- diagnostics
def test_narrative_scene_records_a_fallback_reason():
    plan = _plan_with_sectors(_sectors(3))
    scene = _sectors_scene(plan)
    meta = scene.metadata
    assert meta.get("presentation") == "NARRATIVE"
    assert meta.get("sectors_available") == 3
    assert meta.get("sectors_eligible") == 3
    assert meta.get("sectors_displayed") == len(scene.items)
    assert "MIN_SECTORS_FOR_HEATMAP" in meta.get("fallback_reason", "")


def test_heatmap_scene_records_an_empty_fallback_reason():
    plan = _plan_with_sectors(_sectors(MIN_SECTORS_FOR_HEATMAP))
    scene = _sectors_scene(plan)
    meta = scene.metadata
    assert meta.get("presentation") == "HEATMAP"
    assert meta.get("fallback_reason") == ""
    assert meta.get("sectors_displayed") == len(scene.items)
    assert meta.get("sectors_omitted") == 0


def test_diagnostics_survive_serialisation_into_the_plan_artifact():
    """These fields must actually reach the QA/editorial-plan JSON, not just live on the
    in-memory scene, since that artifact is the tool for diagnosing this class of defect."""
    plan = _plan_with_sectors(_sectors(3))
    payload = plan.to_dict()
    sectors_scene = next(s for s in payload["scenes"] if s["scene_type"] == "SECTORS")
    assert sectors_scene["metadata"]["sectors_eligible"] == 3
    assert sectors_scene["metadata"]["presentation"] == "NARRATIVE"

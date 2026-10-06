"""POST data-readiness + editorial-floor follow-up (feature/institutional-flow-intelligence-v1).

Diagnosis of the real 2026-10-06 thin POST (~18s, HOOK/PULSE/SECTORS/CLOSE only): the REPORT
job ran early (~16:49 IST, not the normal ~19:30), so (a) NSE had not yet published the 6 Oct
FII/FPI+DII figure (the canonical report correctly carried NO institutional_flows at all - not
even a stale 5 Oct number relabelled as 6 Oct) and (b) the NIFTY 200 constituent-level OHLCV
Market Structure needs had not backfilled on Yahoo yet (the persisted snapshot's own metrics
show 0/0 coverage on every count). Every omission that day was a genuine, correctly-handled data
gap (case A) - not a planner/editorial defect (case B). These tests prove that fact AND add the
diagnostic surfacing (reason codes, run-summary) the brief asks for, without changing selection
behaviour: no new minimum, no forced scene, no relaxed coverage gate.
"""
from __future__ import annotations

import datetime as dt

import pytest

from test_official_snapshots import (EVENING, FRIDAY, MONDAY, Sources, _manifest,
                                     _radar_with_structure, _use, offline_pipeline,
                                     two_runners)  # noqa: F401  (fixture re-export)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- 1-2. real pipeline
def test_under_the_surface_is_selected_with_real_coverage(two_runners):
    """§7 guarantee, proven on the SAME fixture the existing suite already uses to show STRUCTURE
    renders (test_official_snapshots.test_post_consumes_the_persisted_market_structure_snapshot)."""
    m = two_runners["manifest"]
    assert "STRUCTURE" in m["scenes"]
    assert m["optional_sections"]["MARKET_STRUCTURE"]["code"] == "RENDERED"


def test_section_decisions_and_readiness_are_in_the_real_manifest(two_runners):
    m = two_runners["manifest"]
    decisions = m["post_section_decisions"]
    assert decisions["PULSE"]["selected"] is True
    assert decisions["PULSE"]["reason"].startswith("core")
    assert "MARKET_STRUCTURE" in decisions and decisions["MARKET_STRUCTURE"]["selected"] is True
    assert isinstance(m["post_data_readiness"], dict)   # present even when empty


# --------------------------------------------------------------------------- 3. zero coverage (the real incident)
def _zero_coverage_snapshot():
    """The real 2026-10-06 shape: a Market Structure snapshot exists, but every metric has 0/0
    coverage (constituent-level OHLCV had not backfilled on Yahoo yet when it was built)."""
    from market_structure.aggregator import SUPPRESSED, Metric, MarketStructureSnapshot
    metrics = {k: Metric(key=k, numerator=0, denominator=0, universe_size=200, coverage_pct=0.0,
                         status=SUPPRESSED, by_sector={}, observation_ids=[])
              for k in ("UNUSUAL_VOLUME", "RANGE_UP", "RANGE_DOWN", "ADVANCES", "DECLINES")}
    return MarketStructureSnapshot(session_date="2026-10-06", universe="NIFTY200",
                                   universe_label="NIFTY 200", constituent_count=200,
                                   metrics=metrics, sector_mapping={}, universe_source={},
                                   definitions={}, unclassified=[])


def test_zero_coverage_market_structure_is_omitted_not_forced():
    """§7's guarantee is conditional on a MEANINGFUL fact existing - with every metric suppressed
    (0/0 coverage, exactly the real 6 Oct snapshot), Under the Surface is correctly omitted as
    INSUFFICIENT_COVERAGE, never forced or shown partially."""
    from market_structure.editorial import select_insights
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections
    from publication import PublicationGate, resolve_profile

    snap = _zero_coverage_snapshot()
    assert select_insights(snap, nifty_pct=0.6) == ([], {
        "BREADTH": "omitted: below its threshold or coverage suppressed",
        "UNUSUAL_VOLUME": "omitted: below its threshold or coverage suppressed",
        "RANGE": "omitted: below its threshold or coverage suppressed"})

    intel = PublicIntelligence(structure=snap, structure_status="PERSISTED_SNAPSHOT")
    ps = plan_public_sections(PublicationGate(resolve_profile(None)), intel,
                              dt.date(2026, 10, 6), "POST", nifty_pct=0.6)
    assert ps.structure == []
    assert ps.audit["omitted_sections"]["MARKET_STRUCTURE"]["code"] == "INSUFFICIENT_COVERAGE"


# --------------------------------------------------------------------------- 4. no stale flow
def test_no_same_session_flow_is_never_shown_as_a_stale_prior_session_number():
    """The canonical-report level guarantee: with no institutional_flows fact for the session,
    the FLOWS scene is simply absent - never built from an older session's figure."""
    from editorial.planner import _flows_scene
    from core.report import MarketReport

    class _EmptyReport:
        institutional_flows = {}

        def facts_for(self, metric):
            return []

    assert _flows_scene(_EmptyReport(), None, set()) is None


def test_nse_same_session_status_distinguishes_awaiting_from_available(tmp_path):
    from institutional_flows.context import (AWAITING_PUBLICATION, NO_SNAPSHOT,
                                              SAME_SESSION_AVAILABLE, nse_same_session_status)
    from institutional_flows.models import (NSE, RepresentedPeriod, SCHEMA_VERSION, SUCCESS,
                                            InstitutionalFlowSnapshot)
    from institutional_flows.store import write_revision

    assert nse_same_session_status(str(tmp_path), dt.date(2026, 10, 6))["status"] == NO_SNAPSHOT

    snap = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=NSE, source_class="EXCHANGE_PROVISIONAL",
        report_type="fii_dii_daily", report_key="2026-10-05", report_date="2026-10-05",
        data_as_of="2026-10-05", represented_period=RepresentedPeriod(), retrieved_at="x",
        first_retrieved_at="x", status=SUCCESS, facts=[])
    write_revision(str(tmp_path), snap)

    status = nse_same_session_status(str(tmp_path), dt.date(2026, 10, 6))
    assert status["status"] == AWAITING_PUBLICATION
    assert status["latest_report_key"] == "2026-10-05" and status["sessions_behind"] == 1

    same = nse_same_session_status(str(tmp_path), dt.date(2026, 10, 5))
    assert same["status"] == SAME_SESSION_AVAILABLE


# --------------------------------------------------------------------------- 5-6. readiness wording
def test_data_readiness_explains_the_flows_omission_with_the_real_reason(tmp_path):
    from dataclasses import dataclass, field

    from institutional_flows.models import NSE, RepresentedPeriod, SCHEMA_VERSION, SUCCESS, \
        InstitutionalFlowSnapshot
    from institutional_flows.store import write_revision
    from products.post_unified import data_readiness

    snap = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=NSE, source_class="EXCHANGE_PROVISIONAL",
        report_type="fii_dii_daily", report_key="2026-10-05", report_date="2026-10-05",
        data_as_of="2026-10-05", represented_period=RepresentedPeriod(), retrieved_at="x",
        first_retrieved_at="x", status=SUCCESS, facts=[])
    write_revision(str(tmp_path), snap)

    @dataclass
    class _Scene:
        kind: str

    @dataclass
    class _SB:
        scenes: list = field(default_factory=list)
        post_plan: dict = field(default_factory=dict)
        public_audit: dict | None = None

    sb = _SB(scenes=[_Scene("PULSE"), _Scene("SECTORS"), _Scene("CLOSING")],
            post_plan={"reasons": {"FLOWS": "omitted: no validated FII/DII flow facts in the "
                                            "report - not fabricated"}})
    out = data_readiness(sb, str(tmp_path), dt.date(2026, 10, 6))
    assert "SAME_SESSION_FLOW_NOT_YET_AVAILABLE" in out["NSE_FII_DII"]
    assert "2026-10-05" in out["NSE_FII_DII"]


def test_data_readiness_is_empty_when_flows_was_actually_selected(tmp_path):
    from dataclasses import dataclass, field
    from products.post_unified import data_readiness

    @dataclass
    class _Scene:
        kind: str

    @dataclass
    class _SB:
        scenes: list = field(default_factory=list)
        post_plan: dict = field(default_factory=dict)
        public_audit: dict | None = None

    sb = _SB(scenes=[_Scene("FLOWS")], post_plan={"reasons": {"FLOWS": "included: large move"}})
    assert data_readiness(sb, str(tmp_path), dt.date(2026, 10, 6)) == {}


# --------------------------------------------------------------------------- 7. independent sections
def test_flows_omission_does_not_suppress_other_optional_sections():
    """presentation.post_plan.plan_post_sections decides each optional section independently -
    FLOWS being unavailable must never cascade into omitting MOVERS/GLOBAL/EVENT."""
    from tests.test_post_phase3 import _pres, _plan
    from presentation.post_plan import plan_post_sections
    pres = _pres()
    plan = _plan(gainers=(("STOCK-E", 8.0),), losers=(("STOCK-F", -1.2),), flows=None)
    out = plan_post_sections(pres, plan)
    assert out.show_flows is False
    assert out.reasons["FLOWS"].startswith("omitted: no validated")
    # MOVERS is independently eligible/ineligible on its own merits - never gated by FLOWS
    assert "FLOWS" not in out.reasons["MOVERS"]


# --------------------------------------------------------------------------- 8-9. unchanged behaviour
def test_short_post_is_still_allowed_no_new_minimum_duration():
    """No forced floor: the pipeline's actual duration bound is untouched (15-70s hard QA
    range); nothing in this follow-up raises it or forces padding."""
    from editorial.config import MIN_SHORT_DURATION
    assert MIN_SHORT_DURATION <= 20.0, "a genuinely thin, data-poor session may still be short"


def test_public_rights_behaviour_is_unchanged():
    from publication.rights import review_required_policy, BLOCK
    from core.sources import SRC_NSE, SRC_NSE_FIIDII_API
    from publication.rights import rights_for
    assert review_required_policy() == BLOCK
    assert rights_for(SRC_NSE).status.value == "REVIEW_REQUIRED"
    assert rights_for(SRC_NSE_FIIDII_API).status.value == "REVIEW_REQUIRED"


def test_section_decisions_never_crashes_on_a_bare_storyboard():
    """section_decisions/data_readiness degrade gracefully on minimal inputs (e.g. a legacy or
    partially-built storyboard) rather than raising."""
    from dataclasses import dataclass, field
    from products.post_unified import section_decisions, data_readiness

    @dataclass
    class _SB:
        scenes: list = field(default_factory=list)
        post_plan: dict | None = None
        public_audit: dict | None = None
        radar_guard: list | None = None

    sb = _SB()
    assert section_decisions(sb) == {}
    assert data_readiness(sb, None, dt.date(2026, 10, 6)) == {}

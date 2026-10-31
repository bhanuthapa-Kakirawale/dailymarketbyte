"""Issue 1 (partial-coverage hook selection): a breadth divergence becomes a hook only when the
breadth metrics are PUBLISHABLE.

A PARTIAL breadth insight (95% <= coverage < 100%) may still play in UNDER THE SURFACE, worded
"of N covered". But the hook's "n of N" display has no room for that qualifier, and its
BREADTH_MOVE claim requires PUBLISHABLE (publication.scene_claims._breadth_evidence_ok). Offering
it would turn a usable POST into CONTENT_FAILURE, so `publication.public_hooks._add_divergence`
never builds the fact and records why in `sheet.metadata["structure_hook_omitted"]`. Every other
candidate is scored exactly as before, and the claim resolver's own checks are unchanged.

The PARTIAL snapshot is real data, not a patched status: the DIVERGENCE fixture's own
observations with four advancers marked not breadth-covered, re-aggregated by
`market_structure.aggregate`, give ADVANCES 66 / DECLINES 130 over 196 of 200 (98%) - still a
divergence on a +0.42% Nifty day.
"""
import dataclasses
import json

import pytest

import market_structure as ms
from daily_video.public_storyboard import audit_storyboard
from hooks import plan_hook, post_market_sheet
from hooks.candidates import LEAD_STORY_BONUS, build_candidates
from hooks.models import HookSource
from market_structure.aggregator import PARTIAL, PUBLISHABLE, SUPPRESSED
from operations.daily_check import audit_verdict
from products import public_fixtures as PF
from publication.audit import content_checks_passed
from publication.public_hooks import add_structure_facts
from test_public_publication import NEXT, PREV, SESSION, _pres, _radar
from test_public_publication import _plan as _shorts_plan
import daily_video.storyboard as sbm

PCT = 0.42                      # Nifty up: the divergence counts the decliners
DIV = "post-breadth-divergence"
OMIT_KEY = "structure_hook_omitted"


# --------------------------------------------------------------------------- builders
def _snapshot(uncovered_advancers: int = 0, session=SESSION):
    """The DIVERGENCE snapshot, with `uncovered_advancers` advancers' breadth coverage removed
    and the snapshot re-aggregated from those observations (never a status override)."""
    snap, uni, obs = PF.structure(SESSION, PREV, "DIVERGENCE")
    if uncovered_advancers:
        drop = {o.symbol for o in obs
                if o.price_change_pct is not None and o.price_change_pct > 0}
        drop = set(sorted(drop)[:uncovered_advancers])
        obs = [dataclasses.replace(o, breadth_covered=False) if o.symbol in drop else o
               for o in obs]
        snap = ms.aggregate(obs, uni, SESSION)
    if str(session) != snap.session_date:
        snap = dataclasses.replace(snap, session_date=str(session))
    return snap


def _intel(snap="DEFAULT"):
    base = PF.intelligence(SESSION, PREV, NEXT, ipo_day=SESSION, structure_scenario="DIVERGENCE",
                           exchange_scenario="FNO_BAN")
    return base if snap == "DEFAULT" else dataclasses.replace(base, structure=snap)


def _storyboard(snap="DEFAULT", **kw):
    rp, rr = _radar("STOCK-001", "STOCK-002")
    return sbm.build_storyboard(_shorts_plan(), _pres(PCT), rp, rr, {}, "Nifty 100", {},
                                intelligence=_intel(snap), **kw)


def _breadth_insight(snap):
    ins, _ = ms.select_insights(snap, PCT)
    return next((i for i in ins if i.kind == "BREADTH"), None)


def _sheet(snap, lead_section=None):
    """The POST hook sheet exactly as build_storyboard assembles it, before candidates."""
    p, pr = _shorts_plan(), _pres(PCT)
    sheet = post_market_sheet(p, pr, (), None, ["PULSE", "STRUCTURE", "SECTORS", "FLOWS"],
                              "Nifty 100")
    ins = _breadth_insight(snap)
    add_structure_facts(sheet, [ins] if ins else [], nifty_pct=PCT)
    if lead_section:
        sheet.metadata = dict(sheet.metadata, lead_section=lead_section)
    return sheet


def _audit(sb):
    a = audit_storyboard(sb, "POST_UNIFIED")
    return a, audit_verdict(a["final"], a["failed_checks"])


@pytest.fixture(scope="module")
def full():
    return _snapshot()


@pytest.fixture(scope="module")
def partial():
    snap = _snapshot(uncovered_advancers=4)
    adv, dec = snap.metric("ADVANCES"), snap.metric("DECLINES")
    assert (adv.status, dec.status) == (PARTIAL, PARTIAL)
    assert (adv.numerator, dec.numerator, dec.denominator, dec.coverage_pct) == (66, 130, 196, 98.0)
    return snap


# --------------------------------------------------------------------------- 1. PUBLISHABLE
def test_publishable_breadth_still_builds_the_divergence_fact_and_hook(full):
    assert full.metric("DECLINES").status == PUBLISHABLE
    sheet = _sheet(full)
    f = sheet.fact("structure.divergence")
    assert f is not None and f.kind == "BREADTH_MOVE" and f.display == "130 of 200"
    assert OMIT_KEY not in sheet.metadata
    assert sheet.metadata["divergence_cov"] == 200
    assert DIV in [c.candidate_id for c in build_candidates(sheet)]

    sb = _storyboard()
    assert sb.hook_plan["candidate_id"] == DIV
    a, verdict = _audit(sb)
    assert a["scans"]["displayed_claims"] == {"passed": True, "issues": []}
    assert verdict == "RIGHTS_BLOCK_ONLY"


# --------------------------------------------------------------------------- 2-3. PARTIAL
def test_partial_breadth_builds_no_divergence_fact_and_offers_no_divergence_hook(partial):
    ins = _breadth_insight(partial)
    assert ins is not None and ins.coverage_status == PARTIAL      # the insight itself survives
    sheet = _sheet(partial)
    assert sheet.fact("structure.divergence") is None
    assert not sheet.facts_of_kind("BREADTH_MOVE")
    assert DIV not in [c.candidate_id for c in build_candidates(sheet)]


def test_partial_omission_reason_is_recorded_in_the_sheet_metadata(partial):
    sheet = _sheet(partial)
    assert sheet.metadata[OMIT_KEY] == {
        "structure.divergence": "BREADTH_NOT_PUBLISHABLE: coverage_status=PARTIAL (98%)"}
    # none of the divergence's own metadata is written for a fact that does not exist
    for key in ("divergence_cov", "divergence_share", "structure_universe"):
        assert key not in sheet.metadata, key


def test_recording_the_omission_preserves_unrelated_metadata_and_merges_prior_entries(partial):
    sheet = post_market_sheet(_shorts_plan(), _pres(PCT), (), None, ["PULSE", "STRUCTURE"],
                              "Nifty 100")
    sheet.metadata = dict(sheet.metadata, sentinel="keep-me",
                          **{OMIT_KEY: {"structure.other": "prior reason"}})
    prior = dict(sheet.metadata)
    add_structure_facts(sheet, [_breadth_insight(partial)], nifty_pct=PCT)
    for key, value in prior.items():
        if key != OMIT_KEY:
            assert sheet.metadata[key] == value, key
    assert sheet.metadata[OMIT_KEY] == {
        "structure.other": "prior reason",
        "structure.divergence": "BREADTH_NOT_PUBLISHABLE: coverage_status=PARTIAL (98%)"}


# --------------------------------------------------------------------------- 4. another hook
def test_partial_day_selects_another_eligible_hook_and_audits_rights_block_only(partial):
    sb = _storyboard(partial)
    assert sb.hook_plan["candidate_id"] != DIV
    offered = [c[0] for c in sb.hook_plan["candidates"]]
    assert DIV not in offered and sb.hook_plan["candidate_id"] == offered[0]
    a, verdict = _audit(sb)
    assert not [c for c in a["displayed_claims"] if c["metric"] == "breadth_move"]
    assert a["scans"]["displayed_claims"] == {"passed": True, "issues": []}
    assert verdict == "RIGHTS_BLOCK_ONLY" and content_checks_passed(a)
    assert a["failed_checks"] == ["publication_rights"]           # rights still block


# --------------------------------------------------------------------------- 5. scores unchanged
@pytest.mark.parametrize("lead", [None, "STRUCTURE", "SECTORS", "FLOWS"])
def test_non_divergence_candidate_scores_are_identical(full, partial, lead):
    before = {c.candidate_id: c.score for c in build_candidates(_sheet(full, lead))}
    after = {c.candidate_id: c.score for c in build_candidates(_sheet(partial, lead))}
    assert DIV in before and DIV not in after
    for cid, score in before.items():
        if cid != DIV and cid in after:
            assert after[cid] == score, (lead, cid)
    # a candidate that only appears now was cut by MAX_CANDIDATES before, never re-scored up
    for cid in set(after) - set(before):
        assert after[cid] <= min(before.values()), (lead, cid)


def test_storyboard_hook_candidate_scores_are_identical(full, partial):
    before = {c[0]: c[2] for c in _storyboard(full).hook_plan["candidates"]}
    after = {c[0]: c[2] for c in _storyboard(partial).hook_plan["candidates"]}
    assert {k: v for k, v in before.items() if k != DIV} == \
        {k: v for k, v in after.items() if k in before}


# --------------------------------------------------------------------------- 6. UNDER THE SURFACE
def test_under_the_surface_still_plays_with_partial_coverage_wording(partial):
    sb = _storyboard(partial)
    (st,) = [s for s in sb.scenes if s.kind == "STRUCTURE" and s.data.get("kind") == "BREADTH"]
    assert st.data["universe"] == {"label": "NIFTY 200", "denominator_text": "130 of 196 covered",
                                   "coverage_pct": 98.0, "status": "PARTIAL"}
    assert st.texts["hero_value"] == "130 / 196"
    assert st.texts["takeaway"] == "66 of 196 covered closed higher."
    a, _ = _audit(sb)
    scene = [c for c in a["displayed_claims"] if c["scene_id"].endswith("_structure")]
    assert scene and all(c["fact_ids"] for c in scene)            # the scene's own claims resolve


# --------------------------------------------------------------------------- 7. SUPPRESSED / missing
def test_suppressed_breadth_has_no_insight_no_fact_and_no_omission_record():
    snap = _snapshot(uncovered_advancers=12)                    # 188 / 200 = 94% < 95%
    assert snap.metric("DECLINES").status == SUPPRESSED
    assert _breadth_insight(snap) is None
    sheet = _sheet(snap)
    assert sheet.fact("structure.divergence") is None and OMIT_KEY not in sheet.metadata
    sb = _storyboard(snap)
    assert sb.hook_plan["candidate_id"] != DIV
    assert not [s for s in sb.scenes if s.kind == "STRUCTURE" and s.data.get("kind") == "BREADTH"]
    _, verdict = _audit(sb)
    assert verdict == "RIGHTS_BLOCK_ONLY"


def test_missing_snapshot_has_no_structure_hook_and_no_content_failure():
    sb = _storyboard(None)
    assert sb.hook_plan["candidate_id"] != DIV
    assert not [s for s in sb.scenes if s.kind == "STRUCTURE"]
    a, verdict = _audit(sb)
    assert not [c for c in a["displayed_claims"] if c["metric"] == "breadth_move"]
    assert verdict == "RIGHTS_BLOCK_ONLY"


# --------------------------------------------------------------------------- 8. wrong session
def test_mismatched_session_snapshot_still_fails_closed():
    """Wrong-session evidence is never routed around: the hook is still offered (the insight
    cannot see the session) and its claim stays unresolved -> CONTENT_FAILURE, as before."""
    snap = _snapshot(session=PREV)
    sb = _storyboard(snap)
    assert sb.hook_plan["candidate_id"] == DIV
    a, verdict = _audit(sb)
    assert "00_dynamic_hook: claim '130 of 200' resolves to no fact and no approved derivation" \
        in a["scans"]["displayed_claims"]["issues"]
    assert verdict == "CONTENT_FAILURE"


# --------------------------------------------------------------------------- 9. Gemini / stub
def test_gemini_choosing_the_withheld_breadth_hook_falls_back_deterministically(full, partial):
    div = next(c for c in build_candidates(_sheet(full)) if c.candidate_id == DIV)
    reply = json.dumps({"candidate_id": div.candidate_id, "archetype": div.archetype.value,
                        "hero_visual": div.default_hero.value,
                        "teaser_beats": list(div.default_beats),
                        "curiosity_line": div.curiosity_line, "summary_line": div.summary_line,
                        "fact_ids_used": list(div.fact_ids)})
    seen = {}

    def client(prompt, schema):
        seen["schema"] = schema
        return reply

    sb = _storyboard(partial, hook_ai=True, hook_client=client)
    enum = seen["schema"]["properties"]["candidate_id"]["enum"]
    assert DIV not in enum                                      # never offered to the model
    hp = sb.hook_plan
    assert hp["source"] == HookSource.DETERMINISTIC.value
    assert hp["fallback_reason"] == "Gemini choice failed structural validation"
    assert any("unknown candidate_id" in i for i in hp["validation_issues"])
    assert hp["candidate_id"] == enum[0] != DIV
    _, verdict = _audit(sb)
    assert verdict == "RIGHTS_BLOCK_ONLY"


def test_sheet_level_stub_choice_of_the_withheld_hook_falls_back(full, partial):
    div = next(c for c in build_candidates(_sheet(full)) if c.candidate_id == DIV)
    reply = json.dumps({"candidate_id": DIV, "archetype": div.archetype.value,
                        "hero_visual": div.default_hero.value,
                        "teaser_beats": list(div.default_beats),
                        "curiosity_line": div.curiosity_line, "summary_line": div.summary_line,
                        "fact_ids_used": list(div.fact_ids)})
    sheet = _sheet(partial)
    hp = plan_hook(sheet, client=lambda p, s: reply)
    assert hp.source is HookSource.DETERMINISTIC and hp.candidate_id != DIV
    assert hp.candidate_id == build_candidates(sheet)[0].candidate_id


# --------------------------------------------------------------------------- 10. unchanged
def test_publishable_lead_section_bonus_is_unchanged(full):
    base = {c.candidate_id: c.score for c in build_candidates(_sheet(full))}
    led = {c.candidate_id: c.score for c in build_candidates(_sheet(full, "STRUCTURE"))}
    assert led[DIV] == pytest.approx(base[DIV] + LEAD_STORY_BONUS)
    for cid in base:
        if cid != DIV and cid in led and cid != "post-quiet-structure":
            assert led[cid] == base[cid], cid


def test_structure_count_hook_fact_is_unchanged_for_partial_unusual_volume():
    """STRUCTURE_COUNT keeps its existing behaviour (out of scope here): a PARTIAL unusual-volume
    insight still yields `structure.unusual`."""
    snap, _, _ = PF.structure(SESSION, PREV, "CONCENTRATED")
    ins = next(i for i in ms.select_insights(snap, 0.12)[0] if i.kind == "UNUSUAL_VOLUME")
    ins = dataclasses.replace(ins, coverage_status=PARTIAL, coverage_pct=98.0)
    sheet = post_market_sheet(_shorts_plan(), _pres(0.12), (), None, ["PULSE", "STRUCTURE"],
                              "Nifty 100")
    add_structure_facts(sheet, [ins], nifty_pct=0.12)
    f = sheet.fact("structure.unusual")
    assert f is not None and f.kind == "STRUCTURE_COUNT"
    assert OMIT_KEY not in sheet.metadata


def test_divergence_gate_lives_only_in_the_post_structure_path():
    """PRE never builds structure hook facts, and the gate is not in the frozen storyboard."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "add_structure_facts" not in (root / "daily_video/pre_storyboard.py").read_text(
        encoding="utf-8")
    assert "BREADTH_NOT_PUBLISHABLE" not in (root / "daily_video/storyboard.py").read_text(
        encoding="utf-8")
    assert "BREADTH_NOT_PUBLISHABLE" in (root / "publication/public_hooks.py").read_text(
        encoding="utf-8")

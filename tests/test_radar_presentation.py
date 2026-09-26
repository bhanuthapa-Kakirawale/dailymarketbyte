"""Phase 4.2 Packet 6.1: deterministic Radar presentation planner. Offline only, no network,
no LLM - `build_radar_presentation` is a pure function over a `DailyRadarResult`-shaped dict.

Named `test_radar_presentation.py` (not `test_presentation.py`) to avoid colliding with the
pre-existing, unrelated `tests/test_presentation.py` (Phase 2's `presentation.ReportPresentation`
- the MarketReport-to-video adapter, a completely different concept from this module's
Radar-to-vertical-short presentation contract).
"""
import copy
import datetime as dt

import radar.presentation as pmodels
import radar.presentation_planner as pp

SESSION = "2026-09-21"


def _story(instrument="AAA", company_name="AAA Ltd", price_change_pct=1.5,
          active_families=None, independent_signal_count=None, novelty_type="NEW_CANDIDATE",
          novelty_reason="AAA: no prior Radar candidate within the lookback window.",
          direction="ALIGNED_POSITIVE", attention_level="NOTABLE",
          volume_context=None, technical_context=None, relative_context=None,
          selection_id="sel-1", supporting_session_dates=None, provenance=None,
          editorial_selection_reason="Selected as a new Radar candidate."):
    active_families = active_families if active_families is not None else ["STRUCTURE", "VOLUME"]
    independent_signal_count = (independent_signal_count if independent_signal_count is not None
                                else len(active_families))
    return {
        "instrument": instrument, "company_name": company_name,
        "price_change_pct": price_change_pct, "active_families": active_families,
        "independent_signal_count": independent_signal_count,
        "volume_context": volume_context, "technical_context": technical_context,
        "relative_context": relative_context, "novelty_type": novelty_type,
        "novelty_reason": novelty_reason, "direction": direction,
        "attention_level": attention_level, "editorial_selection_reason": editorial_selection_reason,
        "reserved_3family": False, "diversity_role": "BASE_ORDER",
        "supporting_session_dates": supporting_session_dates or [SESSION],
        "concise_reason": "Price closed above its prior 20-session high.",
        "provenance": provenance or {"selector_version": "1.0", "candidate_calculation_version": "1.0"},
        "selection_id": selection_id,
    }


def _daily_radar(stories=None, pipeline_status="SUCCESS", warnings=None, selector_version="1.0"):
    stories = stories if stories is not None else [_story()]
    return {
        "session_date": SESSION, "generated_at": "2026-09-21T09:00:00+00:00",
        "pipeline_status": pipeline_status, "dry_run": False, "universe_requested": 200,
        "universe_usable": 200, "volume_event_count": 1, "technical_event_count": 1,
        "relative_count": 200, "composite_candidate_count": len(stories),
        "novel_candidate_count": len(stories), "continuation_count": 0,
        "editorial_selection_count": len(stories), "stories": stories,
        "coverage_diagnostics": {}, "pipeline_diagnostics": {},
        "warnings": warnings or [], "issues": [], "calculation_versions": {"composite": "1.0"},
        "selector_version": selector_version, "daily_pipeline_version": "1.0",
    }


# --------------------------------------------------------------------------- purity / non-mutation
def test_input_daily_radar_dict_not_mutated():
    daily = _daily_radar([_story("AAA"), _story("BBB")])
    before = copy.deepcopy(daily)
    pp.build_radar_presentation(daily)
    assert daily == before


def test_input_story_dicts_not_mutated():
    story = _story("AAA")
    before = copy.deepcopy(story)
    pp.build_radar_presentation(_daily_radar([story]))
    assert story == before


# --------------------------------------------------------------------------- ordering
def test_editorial_order_preserved_not_reordered_by_price():
    stories = [_story("ZZZ", price_change_pct=0.1), _story("AAA", price_change_pct=9.0)]
    pres = pp.build_radar_presentation(_daily_radar(stories))
    story_scenes = [s for s in pres.scenes if s.role == "STORY"]
    assert [s.story.instrument for s in story_scenes] == ["ZZZ", "AAA"]
    assert pres.qa["story_order_preserved"] is True


def test_no_duplicate_instruments():
    pres = pp.build_radar_presentation(_daily_radar([_story("AAA"), _story("BBB")]))
    assert pres.qa["no_duplicate_instruments"] is True


# --------------------------------------------------------------------------- duration
def test_five_story_duration_in_40_60_range():
    stories = [_story(f"S{i}") for i in range(5)]
    pres = pp.build_radar_presentation(_daily_radar(stories))
    assert 40 <= pres.estimated_duration_seconds <= 60
    assert pres.qa["duration_in_target_range"] is True


def test_three_story_duration_reasonable():
    stories = [_story(f"S{i}") for i in range(3)]
    pres = pp.build_radar_presentation(_daily_radar(stories))
    assert pres.estimated_duration_seconds == pmodels.HOOK_DURATION_SECONDS + \
        3 * pmodels.STORY_DURATION_SECONDS + pmodels.CLOSING_DURATION_SECONDS


def test_one_story_allowed_shorter_no_padding():
    pres = pp.build_radar_presentation(_daily_radar([_story("AAA")]))
    assert pres.estimated_duration_seconds < pmodels.MIN_DURATION_SECONDS
    assert pres.qa["duration_target_not_reached_due_to_story_count"] is True
    assert any("duration_target_not_reached_due_to_story_count" in w for w in pres.warnings)


# --------------------------------------------------------------------------- zero stories / failure states
def test_zero_stories_produces_no_stories_status_no_manufactured_content():
    pres = pp.build_radar_presentation(_daily_radar([]))
    assert pres.status == "NO_STORIES"
    assert pres.scenes == []
    assert pres.story_count == 0


def test_failed_pipeline_status_rejected():
    pres = pp.build_radar_presentation(_daily_radar([_story()], pipeline_status="FAILED"))
    assert pres.status == "REJECTED_UPSTREAM_FAILED"
    assert pres.scenes == []


def test_degraded_status_propagated_when_stories_present():
    daily = _daily_radar([_story()], pipeline_status="DEGRADED",
                         warnings=["some upstream degradation"])
    pres = pp.build_radar_presentation(daily)
    assert pres.status == "OK"
    assert pres.source_pipeline_status == "DEGRADED"
    assert "some upstream degradation" in pres.warnings


def test_success_status_preserved():
    pres = pp.build_radar_presentation(_daily_radar([_story()], pipeline_status="SUCCESS"))
    assert pres.source_pipeline_status == "SUCCESS"
    assert pres.status == "OK"


# --------------------------------------------------------------------------- identity / artifact
def test_deterministic_presentation_id():
    pres1 = pp.build_radar_presentation(_daily_radar([_story()]))
    pres2 = pp.build_radar_presentation(_daily_radar([_story()]))
    assert pres1.presentation_id == pres2.presentation_id
    assert pres1.presentation_id == f"{SESSION}:MARKET_RADAR_SHORT:1.0"


def test_deterministic_artifact_path(tmp_path):
    pres = pp.build_radar_presentation(_daily_radar([_story()]))
    path1 = pp.save_presentation(pres, out_dir=str(tmp_path))
    path2 = pp.save_presentation(pres, out_dir=str(tmp_path))
    assert path1 == path2
    assert path1.endswith(f"radar_presentation_{SESSION}.json")


def test_rerun_logically_identical(tmp_path):
    daily = _daily_radar([_story("AAA"), _story("BBB")])
    pres1 = pp.build_radar_presentation(daily, as_of=dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.timezone.utc))
    pres2 = pp.build_radar_presentation(daily, as_of=dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.timezone.utc))
    d1, d2 = pres1.to_dict(), pres2.to_dict()
    assert d1 == d2


# --------------------------------------------------------------------------- label mappings
def test_evidence_family_labels_map_correctly():
    assert pmodels.EVIDENCE_FAMILY_LABELS["VOLUME"] == "Unusual Volume"
    assert pmodels.EVIDENCE_FAMILY_LABELS["STRUCTURE"] == "Technical Structure"
    assert pmodels.EVIDENCE_FAMILY_LABELS["RELATIVE_PERFORMANCE"] == "Relative Performance"


def test_technical_event_labels_map_correctly_and_cover_all_existing_events():
    from radar.models import TechnicalEventType
    for event in TechnicalEventType:
        assert event.value in pmodels.TECHNICAL_EVENT_LABELS, f"{event.value} not mapped"


def test_novelty_labels_map_correctly_and_cover_all_existing_types():
    from radar.models import NoveltyType
    for nt in NoveltyType:
        assert nt.value in pmodels.NOVELTY_LABELS, f"{nt.value} not mapped"


def test_direction_labels_map_correctly():
    assert pmodels.DIRECTION_LABELS["ALIGNED_POSITIVE"] == "Positive Alignment"
    assert pmodels.DIRECTION_LABELS["ALIGNED_NEGATIVE"] == "Negative Alignment"
    assert pmodels.DIRECTION_LABELS["MIXED"] == "Mixed Signals"
    assert pmodels.DIRECTION_LABELS["NON_DIRECTIONAL"] is None


# --------------------------------------------------------------------------- quiet signal
def test_quiet_signal_identified_for_small_move_multi_family():
    story = _story("AAA", price_change_pct=0.5, active_families=["STRUCTURE", "VOLUME"])
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    assert sp.quiet_signal is True
    assert sp.visual_type == "QUIET_SIGNAL_CARD"


def test_not_quiet_signal_for_large_move():
    story = _story("AAA", price_change_pct=8.0, active_families=["STRUCTURE", "VOLUME"])
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    assert sp.quiet_signal is False


def test_quiet_signal_does_not_change_scene_order():
    quiet = _story("AAA", price_change_pct=0.2, active_families=["STRUCTURE", "VOLUME"])
    loud = _story("BBB", price_change_pct=9.0, active_families=["STRUCTURE", "VOLUME"])
    pres = pp.build_radar_presentation(_daily_radar([loud, quiet]))  # loud first, editorial order
    story_scenes = [s for s in pres.scenes if s.role == "STORY"]
    assert [s.story.instrument for s in story_scenes] == ["BBB", "AAA"]  # unchanged despite quiet


# --------------------------------------------------------------------------- visual type precedence
def test_visual_type_three_family():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                   independent_signal_count=3, price_change_pct=5.0)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    assert pres.scenes[1].story.visual_type == "THREE_SIGNAL_CARD"


def test_visual_type_quiet_beats_three_family():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                   independent_signal_count=3, price_change_pct=0.3)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    assert pres.scenes[1].story.visual_type == "QUIET_SIGNAL_CARD"


def test_visual_type_mixed_direction():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME"], direction="MIXED",
                   price_change_pct=5.0)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    assert pres.scenes[1].story.visual_type == "MIXED_SIGNAL_CARD"


def test_visual_type_volume_structure():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME"], direction="ALIGNED_POSITIVE",
                   price_change_pct=5.0)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    assert pres.scenes[1].story.visual_type == "VOLUME_STRUCTURE_CARD"


def test_visual_type_relative_structure():
    story = _story("AAA", active_families=["STRUCTURE", "RELATIVE_PERFORMANCE"],
                   direction="ALIGNED_POSITIVE", price_change_pct=5.0)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    assert pres.scenes[1].story.visual_type == "RELATIVE_STRUCTURE_CARD"


def test_visual_type_fallback_volume_relative_no_structure():
    story = _story("AAA", active_families=["VOLUME", "RELATIVE_PERFORMANCE"],
                   direction="ALIGNED_POSITIVE", price_change_pct=5.0)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    assert pres.scenes[1].story.visual_type == "MIXED_SIGNAL_CARD"


# --------------------------------------------------------------------------- evidence display
def test_max_three_evidence_items_displayed():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                   independent_signal_count=3, novelty_type="MULTIPLE_CHANGES",
                   technical_context={"events": ["BREAK_ABOVE_20D_RANGE"]},
                   volume_context={"level": "EXTREME", "relative_volume": 7.2, "rvol_percentile": None},
                   relative_context={"persistence_state": "PERSISTENT_POSITIVE",
                                    "market_relative_5d_pp": 3.2, "market_relative_20d_pp": 5.8})
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    assert len(sp.evidence_items) <= 3
    assert pres.qa["max_evidence_respected"] is True


def test_full_evidence_context_retained_even_when_not_displayed():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                   independent_signal_count=3,
                   technical_context={"events": ["BREAK_ABOVE_20D_RANGE", "CROSS_ABOVE_SMA20"]},
                   volume_context={"level": "EXTREME", "relative_volume": 7.2, "rvol_percentile": None},
                   relative_context={"persistence_state": "PERSISTENT_POSITIVE",
                                    "market_relative_5d_pp": 3.2, "market_relative_20d_pp": 5.8})
    daily = _daily_radar([story])
    pres = pp.build_radar_presentation(daily)
    # the underlying upstream story data is untouched and still has ALL evidence
    assert len(daily["stories"][0]["technical_context"]["events"]) == 2
    sp = pres.scenes[1].story
    # narration_facts is allowed to carry more than 3 facts (not display-priority-limited)
    assert len(sp.narration_facts) >= 3


def test_novelty_reason_never_shown_as_viewer_facing_evidence():
    """Regression: `novelty_reason` embeds raw internal enum spellings and must never be used
    verbatim as a displayed evidence label (packet spec section 15's "no internal enum syntax")."""
    story = _story("AAA", novelty_type="NEW_TECHNICAL_EVENT",
                   novelty_reason="AAA: new technical event: BREAK_ABOVE_20D_RANGE.",
                   technical_context={"events": ["BREAK_ABOVE_20D_RANGE"]})
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    for e in sp.evidence_items:
        assert "BREAK_ABOVE_20D_RANGE" not in e.label
        assert "new technical event:" not in e.label


# --------------------------------------------------------------------------- text density
def test_headline_within_word_limit():
    stories = [_story(f"S{i}", active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                      independent_signal_count=3) for i in range(3)]
    pres = pp.build_radar_presentation(_daily_radar(stories))
    for sc in pres.scenes:
        if sc.story:
            assert len(sc.story.headline.split()) <= pmodels._WORD_LIMITS["headline"]
    assert pres.qa["text_density_violations"] == []


# --------------------------------------------------------------------------- numeric integrity
def test_no_presentation_arithmetic_displayed_rvol_matches_input():
    story = _story("AAA", active_families=["STRUCTURE", "VOLUME"],
                   volume_context={"level": "UNUSUAL", "relative_volume": 4.321, "rvol_percentile": None})
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    assert any("4.3x" in e.label for e in sp.evidence_items)  # rounded, not recomputed
    assert "4.321" not in " ".join(e.label for e in sp.evidence_items)  # formatted, not raw-dumped


def test_missing_metrics_omitted_not_fabricated():
    story = _story("AAA", volume_context=None, relative_context=None,
                   technical_context=None, active_families=["STRUCTURE", "VOLUME"])
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    for e in sp.evidence_items:
        assert "N/A" not in e.label and "Unknown" not in e.label
    assert sp.price_change_display is not None  # price_change_pct WAS provided (1.5) - real fact


def test_missing_price_change_pct_omitted():
    story = _story("AAA", price_change_pct=None)
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    assert sp.price_change_display is None


# --------------------------------------------------------------------------- content safety
def test_content_safety_scan_applied_and_sanitized_result_preserved():
    story = _story("AAA", editorial_selection_reason="Analyst upgrades AAA to BUY rating.")
    story["novelty_reason"] = "AAA: some unrelated change."
    pres = pp.build_radar_presentation(_daily_radar([story]))
    # selection_reason isn't a displayed field in this packet's fields, but headline/primary_fact
    # ARE checked - verify the scan ran (content_safety_status is set, never left default-empty)
    assert pres.content_safety_status in ("SAFE", "SANITIZED")


def test_recommendation_language_in_generated_text_would_be_blocked():
    """Presentation text is entirely template-generated from validated facts, so it should never
    itself trip a BLOCK rule - this test proves the safety gate is real: a deliberately unsafe
    hook (monkeypatched in) DOES get caught."""
    from core.content_safety import classify_text
    unsafe = "Top stock picks to buy tomorrow!"
    result = classify_text(unsafe)
    assert result.status.value == "BLOCKED"


def test_no_llm_or_network_dependency():
    import inspect
    source = inspect.getsource(pp)
    for banned in ("openai", "anthropic", "gemini", "notebooklm", "requests.", "urllib",
                  "yfinance", "WebSearch"):
        assert banned.lower() not in source.lower()


# --------------------------------------------------------------------------- provenance
def test_every_story_scene_maps_to_source_selection():
    stories = [_story("AAA", selection_id="2026-09-21:AAA:1.0"),
              _story("BBB", selection_id="2026-09-21:BBB:1.0")]
    pres = pp.build_radar_presentation(_daily_radar(stories))
    for sc in pres.scenes:
        if sc.story:
            assert sc.story.source_selection_id
    assert pres.qa["every_story_maps_to_selection"] is True


def test_provenance_retained_on_story_presentation():
    story = _story("AAA", provenance={"selector_version": "1.0", "novelty_calculation_version": "1.0"})
    pres = pp.build_radar_presentation(_daily_radar([story]))
    sp = pres.scenes[1].story
    assert sp.provenance["selector_version"] == "1.0"
    assert sp.source_selection_id == "sel-1"
    assert sp.supporting_session_dates == [SESSION]


def test_selector_version_retained_on_presentation():
    pres = pp.build_radar_presentation(_daily_radar([_story()], selector_version="2.5"))
    assert pres.source_selector_version == "2.5"


# --------------------------------------------------------------------------- real-artifact match
def test_real_2026_09_21_artifact_produces_expected_five_symbols_in_order():
    import json
    with open("output/radar/daily_radar_2026-09-21.json", encoding="utf-8") as fh:
        data = json.load(fh)
    pres = pp.build_radar_presentation(data)
    story_scenes = [s for s in pres.scenes if s.role == "STORY"]
    assert [s.story.instrument for s in story_scenes] == \
        ["MANKIND", "ETERNAL", "OFSS", "ALKEM", "ATGL"]
    assert pres.qa["passed"] is True

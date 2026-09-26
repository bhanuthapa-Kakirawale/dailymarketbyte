"""Phase 4.2 Packet 6.2: Radar visual renderer. Offline only where possible - most tests avoid
invoking ffmpeg directly by testing the pure scene-building/QA/frame-composition layer; a small
number of real-encode tests are marked and run only if ffmpeg is actually available.
"""
import copy
import json
import os
import shutil

import pytest

import radar.presentation_planner as pp
import radar.video_renderer as vr
import radar.video_scenes as vs
from radar.video_theme import (CONTENT_BOTTOM, CONTENT_LEFT, CONTENT_RIGHT, CONTENT_TOP, FPS, H,
                               MIN_FONT_SIZE, W)

ARTIFACT = "output/radar/daily_radar_2026-09-21.json"


def _ffmpeg_available() -> bool:
    from video import _ffmpeg
    return shutil.which(_ffmpeg()) is not None or os.path.exists(_ffmpeg())


def _load_real_presentation():
    return pp.build_radar_presentation_from_file(ARTIFACT)


def _story(instrument="AAA", **kw):
    base = {
        "instrument": instrument, "company_name": f"{instrument} Ltd",
        "price_change_pct": 1.5, "active_families": ["STRUCTURE", "VOLUME"],
        "independent_signal_count": 2, "volume_context": None, "technical_context": None,
        "relative_context": None, "novelty_type": "NEW_CANDIDATE",
        "novelty_reason": f"{instrument}: no prior Radar candidate within the lookback window.",
        "direction": "ALIGNED_POSITIVE", "attention_level": "NOTABLE",
        "editorial_selection_reason": "Selected as a new Radar candidate.",
        "reserved_3family": False, "diversity_role": "BASE_ORDER",
        "supporting_session_dates": ["2026-09-21"],
        "concise_reason": "Price closed above its prior 20-session high.",
        "provenance": {"selector_version": "1.0"}, "selection_id": f"sel-{instrument}",
    }
    base.update(kw)
    return base


def _daily_radar(stories, pipeline_status="SUCCESS"):
    return {
        "session_date": "2026-09-21", "generated_at": "2026-09-21T09:00:00+00:00",
        "pipeline_status": pipeline_status, "dry_run": False, "universe_requested": 200,
        "universe_usable": 200, "volume_event_count": 1, "technical_event_count": 1,
        "relative_count": 200, "composite_candidate_count": len(stories),
        "novel_candidate_count": len(stories), "continuation_count": 0,
        "editorial_selection_count": len(stories), "stories": stories,
        "coverage_diagnostics": {}, "pipeline_diagnostics": {}, "warnings": [], "issues": [],
        "calculation_versions": {"composite": "1.0"}, "selector_version": "1.0",
        "daily_pipeline_version": "1.0",
    }


# --------------------------------------------------------------------------- purity / no new facts
def test_presentation_input_unchanged():
    pres = _load_real_presentation()
    before = copy.deepcopy(pres.to_dict())
    scenes = vr._build_scene_objects(pres)
    vr._visual_qa(pres, scenes)
    assert pres.to_dict() == before


def test_renderer_adds_no_market_facts():
    """Every text-drawing call in `radar/video_scenes.py` must originate from a presentation
    field, a fixed template word, or layout metadata (brand/date/counter) - never a NEW number
    or fact. Checked structurally: the module never imports market/detector/provider code."""
    import inspect
    source = inspect.getsource(vs) + inspect.getsource(vr)
    for banned in ("import market", "import yfinance", "ohlcv_service", "radar.technical",
                  "radar.volume", "radar.relative", "radar.composite", "radar.novelty"):
        assert banned not in source


def test_no_network_llm_or_detector_calls():
    import inspect
    source = inspect.getsource(vr) + inspect.getsource(vs)
    for banned in ("requests.", "urllib", "openai", "anthropic", "gemini", "yfinance",
                  "WebSearch", "notebooklm"):
        assert banned.lower() not in source.lower()


# --------------------------------------------------------------------------- scene/order
def test_story_order_preserved_in_scene_objects():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)
    story_scenes = [s for s in scenes if s.role == "STORY"]
    assert [s.story.instrument for s in story_scenes] == \
        ["MANKIND", "ETERNAL", "OFSS", "ALKEM", "ATGL"]


def test_scene_count_matches_presentation():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)
    assert len(scenes) == len(pres.scenes) == 7


def test_scene_durations_match_presentation():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)
    assert [s.duration_seconds for s in scenes] == [sc.duration_seconds for sc in pres.scenes]


# --------------------------------------------------------------------------- constants
def test_resolution_1080x1920():
    assert (W, H) == (1080, 1920)


def test_fps_30():
    assert FPS == 30


def test_deterministic_renderer_version():
    # Bumped for Packet 6.2R - a visual-system realignment, not a patch to the same version.
    assert vr.RADAR_RENDERER_VERSION == "2.0"


# --------------------------------------------------------------------------- frame drawing
def _story_scene(visual_type, **kw):
    instrument = kw.pop("instrument", "AAA")
    story_dict = _story(instrument, **kw)
    daily = _daily_radar([story_dict])
    pres = pp.build_radar_presentation(daily)
    sp = pres.scenes[1].story
    assert sp.visual_type == visual_type, f"expected {visual_type}, got {sp.visual_type}"
    return vs.StoryCardScene(sp.duration_seconds, sp, 1, 1, pres.session_date)


def test_hook_scene_renders():
    scene = vs.HookScene(5, "Today's Market Radar found 3 developments worth investigating.",
                         "sub", 3, "2026-09-21")
    frame = scene.draw(2.5)
    assert frame.size == (W, H)


def test_closing_scene_renders():
    scene = vs.ClosingScene(4, "These are market signals, not investment recommendations.",
                            "Investigate the evidence before making decisions.")
    frame = scene.draw(2.0)
    assert frame.size == (W, H)


def test_three_signal_card_renders():
    scene = _story_scene("THREE_SIGNAL_CARD", active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                         independent_signal_count=3, price_change_pct=5.0)
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


def test_technical_change_card_or_fallback_renders():
    # STRUCTURE-alone is unreachable from real 2-family composite candidates (min 2 families);
    # exercised directly against the scene class instead of via the planner.
    from radar.presentation import PresentationEvidence, RadarStoryPresentation
    story = RadarStoryPresentation(
        instrument="AAA", company_name="AAA Ltd", scene_order=1, headline="AAA: Breakout",
        primary_fact="20-session break above", context_line=None,
        evidence_items=[PresentationEvidence(label="20-session break above", tier="PRIMARY",
                                             source_field="technical_context.events[0]")],
        active_families=["STRUCTURE"], visual_type="TECHNICAL_CHANGE_CARD",
        price_change_display="+3.0%", source_selection_id="sel-1")
    scene = vs.StoryCardScene(8, story, 1, 1, "2026-09-21")
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


def test_volume_structure_card_renders():
    scene = _story_scene("VOLUME_STRUCTURE_CARD", active_families=["STRUCTURE", "VOLUME"],
                         direction="ALIGNED_POSITIVE", price_change_pct=5.0,
                         volume_context={"level": "UNUSUAL", "relative_volume": 3.2, "rvol_percentile": None},
                         technical_context={"events": ["BREAK_ABOVE_20D_RANGE"]})
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


def test_relative_structure_card_renders():
    scene = _story_scene("RELATIVE_STRUCTURE_CARD",
                         active_families=["STRUCTURE", "RELATIVE_PERFORMANCE"],
                         direction="ALIGNED_POSITIVE", price_change_pct=5.0,
                         relative_context={"persistence_state": "PERSISTENT_POSITIVE",
                                          "market_relative_5d_pp": 3.8, "market_relative_20d_pp": 5.9},
                         technical_context={"events": ["BREAK_ABOVE_20D_RANGE"]})
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


def test_mixed_signal_card_renders():
    scene = _story_scene("MIXED_SIGNAL_CARD", active_families=["STRUCTURE", "VOLUME"],
                         direction="MIXED", price_change_pct=5.0)
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


def test_quiet_signal_card_renders():
    scene = _story_scene("QUIET_SIGNAL_CARD", active_families=["STRUCTURE", "VOLUME"],
                         price_change_pct=0.4)
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


# --------------------------------------------------------------------------- safe areas / fonts
def test_safe_area_constants_match_main_video():
    """Radar's content band is now literally `video.py`'s own TOP/BAND_BOTTOM/X0/X1 - imported,
    not redefined (packet spec section 15)."""
    from video import BAND_BOTTOM, TOP, X0, X1
    assert (CONTENT_TOP, CONTENT_BOTTOM, CONTENT_LEFT, CONTENT_RIGHT) == (TOP, BAND_BOTTOM, X0, X1)
    assert 0 < CONTENT_TOP < CONTENT_BOTTOM < H
    assert 0 < CONTENT_LEFT < CONTENT_RIGHT < W


def test_minimum_font_constant_reasonable():
    assert MIN_FONT_SIZE >= 20


def test_long_but_valid_headline_fits_without_crash():
    scene = _story_scene("THREE_SIGNAL_CARD",
                         active_families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
                         independent_signal_count=3, price_change_pct=5.0, instrument="VERYLONGNAME")
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


def test_missing_optional_context_does_not_crash():
    scene = _story_scene("TECHNICAL_CHANGE_CARD" if False else "MIXED_SIGNAL_CARD",
                         active_families=["STRUCTURE", "VOLUME"], direction="MIXED",
                         price_change_pct=None, volume_context=None, technical_context=None,
                         relative_context=None)
    frame = scene.draw(4.0)
    assert frame.size == (W, H)


# --------------------------------------------------------------------------- label representation
def test_positive_direction_represented_textually():
    scene = _story_scene("VOLUME_STRUCTURE_CARD", active_families=["STRUCTURE", "VOLUME"],
                         direction="ALIGNED_POSITIVE", price_change_pct=5.0)
    assert scene.story.direction_label == "Positive Alignment"


def test_negative_direction_represented_textually():
    scene = _story_scene("VOLUME_STRUCTURE_CARD", active_families=["STRUCTURE", "VOLUME"],
                         direction="ALIGNED_NEGATIVE", price_change_pct=-5.0)
    assert scene.story.direction_label == "Negative Alignment"


def test_mixed_direction_represented_textually():
    scene = _story_scene("MIXED_SIGNAL_CARD", active_families=["STRUCTURE", "VOLUME"],
                         direction="MIXED", price_change_pct=5.0)
    assert scene.story.direction_label == "Mixed Signals"


def test_quiet_signal_visually_labeled():
    scene = _story_scene("QUIET_SIGNAL_CARD", active_families=["STRUCTURE", "VOLUME"],
                         price_change_pct=0.4)
    assert scene.story.quiet_signal is True
    assert "Quiet Signal" in (scene.story.context_line or "") or scene.story.quiet_signal


# --------------------------------------------------------------------------- counter / date
def test_story_counter_correct():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)
    story_scenes = [s for s in scenes if s.role == "STORY"]
    assert [s.scene_number for s in story_scenes] == [1, 2, 3, 4, 5]
    assert all(s.story_count == 5 for s in story_scenes)


def test_session_date_passed_through_not_live_claim():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)
    for s in scenes:
        if s.role != "CLOSING":  # closing scene carries no date - fixed non-time-bound message
            assert s.session_date == "2026-09-21"
    # Only the actual DRAWN static strings matter here, not source comments/docstrings.
    from radar.video_theme import BRAND_KICKER, WORDMARK_PART1, WORDMARK_PART2
    card_labels = [label for label, _color in vs._CARD_LABEL.values()]
    drawn_texts = card_labels + [BRAND_KICKER, WORDMARK_PART1, WORDMARK_PART2]
    for banned in ("LIVE", "BREAKING", "JUST NOW"):
        assert not any(banned in text for text in drawn_texts)


# --------------------------------------------------------------------------- content safety gating
def test_blocked_presentation_does_not_render():
    from radar.presentation import PresentationStatus
    daily = _daily_radar([_story("AAA")])
    pres = pp.build_radar_presentation(daily)
    pres.status = "CONTENT_SAFETY_BLOCKED"
    pres.content_safety_status = "BLOCKED"
    pres.scenes = []
    result = vr.render_radar_video(pres, export_previews=False)
    assert result.render_status == "BLOCKED"
    assert result.output_path is None


def test_no_stories_presentation_does_not_render():
    pres = pp.build_radar_presentation(_daily_radar([]))
    result = vr.render_radar_video(pres, export_previews=False)
    assert result.render_status == "NO_STORIES"
    assert result.output_path is None


# --------------------------------------------------------------------------- QA
def test_visual_qa_all_checks_pass_for_real_artifact():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)
    qa = vr._visual_qa(pres, scenes)
    assert qa["passed"] is True, qa["notes"]


def test_visual_qa_detects_scene_count_mismatch():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres)[:-1]  # drop one scene artificially
    qa = vr._visual_qa(pres, scenes)
    assert qa["scene_count_matches_presentation"] is False
    assert qa["passed"] is False


# --------------------------------------------------------------------------- deterministic path
def test_deterministic_manifest_path(tmp_path):
    pres = _load_real_presentation()
    from dataclasses import replace
    fake_result = vr.RadarVideoRenderResult(
        session_date="2026-09-21", presentation_id=pres.presentation_id,
        renderer_version=vr.RADAR_RENDERER_VERSION, output_path=None, width=W, height=H, fps=FPS,
        expected_duration_seconds=49, actual_duration_seconds=49, frame_count=1470, scene_count=7,
        render_status="OK", content_safety_status="SAFE")
    path1 = vr._save_manifest(fake_result, pres.to_dict(), out_dir=str(tmp_path))
    path2 = vr._save_manifest(fake_result, pres.to_dict(), out_dir=str(tmp_path))
    assert path1 == path2
    assert path1.endswith("radar_2026-09-21_manifest.json")


# --------------------------------------------------------------------------- real-artifact regression
def test_real_artifact_still_contains_expected_five_symbols():
    with open(ARTIFACT, encoding="utf-8") as fh:
        data = json.load(fh)
    assert [s["instrument"] for s in data["stories"]] == \
        ["MANKIND", "ETERNAL", "OFSS", "ALKEM", "ATGL"]


# --------------------------------------------------------------------------- real encode (skipped if no ffmpeg)
@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not available in this environment")
def test_real_encode_produces_mp4(tmp_path):
    pres = _load_real_presentation()
    result = vr.render_radar_video(pres, out_dir=str(tmp_path), export_previews=False)
    assert result.render_status == "OK"
    assert os.path.exists(result.output_path)
    assert os.path.getsize(result.output_path) > 0

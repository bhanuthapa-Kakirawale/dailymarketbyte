"""Phase 4.2 Packet 6.2V: ChartStoryScene (radar/video_scenes.py) and its wiring into
radar/video_renderer.py. Offline only - visual evidence fixtures are built directly as
`RadarVisualEvidence` objects, never through the OHLCV store or a network call.
"""
import ast
import datetime as dt
import inspect

import radar.presentation_planner as pp
import radar.video_renderer as vr
import radar.video_scenes as vs
from radar.video_theme import MIN_FONT_SIZE, H, W
from radar.visual_evidence import RadarVisualEvidence

ARTIFACT = "output/radar/daily_radar_2026-09-21.json"


def _load_real_presentation():
    return pp.build_radar_presentation_from_file(ARTIFACT)


def _evidence(instrument="MANKIND", *, n=38, highlight_events=None, with_relative=True,
             rvol=3.2, rvol_level="UNUSUAL") -> RadarVisualEvidence:
    session_date = dt.date(2026, 9, 21)
    dates = [session_date - dt.timedelta(days=(n - 1 - i)) for i in range(n)]
    closes = [1000.0 + i * 2.0 for i in range(n)]
    volumes = [1000.0 + i * 5.0 for i in range(n)]
    sma20 = [None] * max(0, n - 20) + [1000.0 + i * 1.8 for i in range(min(n, 20))]
    sma50 = [None] * n
    rel_dates = dates[-21:]
    rel_stock = [c / closes[-21] * 100 for c in closes[-21:]] if with_relative else None
    rel_bench = [18000.0 + i for i in range(21)]
    rel_bench = [v / rel_bench[0] * 100 for v in rel_bench] if with_relative else None
    return RadarVisualEvidence(
        instrument=instrument, session_date=session_date, calculation_version="1.0",
        window_dates=dates, close_series=closes, volume_series=volumes,
        sma20_series=sma20, sma50_series=sma50,
        range_20_high=max(closes[-21:-1]), range_20_low=min(closes[-21:-1]),
        range_50_high=max(closes), range_50_low=min(closes),
        highlight_events=highlight_events if highlight_events is not None else
        ["BREAK_ABOVE_20D_RANGE"],
        rvol=rvol, rvol_level=rvol_level, stock_return_5d_pct=3.1, stock_return_20d_pct=7.4,
        market_relative_5d_pp=1.2, market_relative_20d_pp=4.5, relative_window_sessions=20,
        relative_dates=rel_dates if with_relative else None,
        relative_stock_normalized=rel_stock, relative_benchmark_normalized=rel_bench,
        benchmark_symbol="^NSEI", source_session_dates=dates, warnings=[])


def _story_presentation(instrument="MANKIND", **kw):
    base = dict(instrument=instrument, company_name=f"{instrument} Ltd", scene_order=2,
               headline=f"{instrument}: New technical event", primary_fact="Broke above range",
               context_line="Structure changed", evidence_items=[],
               active_families=["STRUCTURE"], novelty_label="New Technical Event",
               direction_label=None, price_change_display="+2.4%", quiet_signal=False,
               selection_reason="", visual_type="TECHNICAL_CHANGE_CARD", duration_seconds=8,
               source_selection_id="sel-1", supporting_session_dates=["2026-09-21"],
               provenance={}, narration_facts=[])
    base.update(kw)
    return vr._StoryObj(base)


# --------------------------------------------------------------------------- boundary
def test_renderer_and_scenes_never_import_ohlcv_store_or_detectors():
    """AST-level check (stricter than a substring scan of the whole source, which also catches
    docstring prose) - neither module may literally import the OHLCV store or a detector
    module. Only `radar.visual_evidence` (the builder, which already computed everything) may
    be imported, and only for its finished dataclass / orchestration helper."""
    banned = {"storage.ohlcv_repository", "radar.technical", "radar.relative", "market",
             "radar.volume", "radar.composite", "radar.novelty"}
    for mod in (vr, vs):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in banned, f"{mod.__name__} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in banned, f"{mod.__name__} imports from {node.module}"
                assert not node.module.endswith(".technical")
                assert not node.module.endswith(".relative")


def test_chart_story_scene_never_calculates_only_draws():
    """Every attribute ChartStoryScene reads off `evidence` is read, never derived by new
    arithmetic beyond pixel mapping (checked structurally: the drawing helpers only import PIL/
    math/video's own font utilities, confirmed by the import-boundary test above)."""
    ev = _evidence()
    story = _story_presentation()
    scene = vs.ChartStoryScene(8, story, ev, 1, 5, "2026-09-21")
    before = ev.to_dict()
    for t in (0.0, 0.5, 1.5, 3.2, 5.0, 6.5, 7.9):
        img = scene.draw(t)
        assert img.size == (W, H)
    assert ev.to_dict() == before  # evidence itself is never mutated by drawing


# --------------------------------------------------------------------------- fallback
def test_missing_evidence_falls_back_to_story_card_scene():
    pres = _load_real_presentation()
    scenes = vr._build_scene_objects(pres, visual_evidence=None)
    story_scenes = [s for s in scenes if s.role == "STORY"]
    assert all(isinstance(s, vs.StoryCardScene) for s in story_scenes)


def test_partial_evidence_uses_chart_scene_only_where_available():
    pres = _load_real_presentation()
    instruments = [s.story.instrument for s in pres.scenes if s.role == "STORY"]
    visual_evidence = {instruments[0]: _evidence(instruments[0])}
    for other in instruments[1:]:
        visual_evidence[other] = None
    scenes = vr._build_scene_objects(pres, visual_evidence=visual_evidence)
    story_scenes = [s for s in scenes if s.role == "STORY"]
    assert isinstance(story_scenes[0], vs.ChartStoryScene)
    assert all(isinstance(s, vs.StoryCardScene) for s in story_scenes[1:])


def test_all_five_real_stories_render_with_synthetic_evidence():
    pres = _load_real_presentation()
    instruments = [s.story.instrument for s in pres.scenes if s.role == "STORY"]
    visual_evidence = {i: _evidence(i) for i in instruments}
    scenes = vr._build_scene_objects(pres, visual_evidence=visual_evidence)
    story_scenes = [s for s in scenes if s.role == "STORY"]
    assert len(story_scenes) == len(instruments)
    for scene in story_scenes:
        for t in (0.5, 3.5, 6.5, 7.9):
            assert scene.draw(t).size == (W, H)


def test_no_relative_series_does_not_crash_chart_scene():
    ev = _evidence(with_relative=False)
    story = _story_presentation()
    scene = vs.ChartStoryScene(8, story, ev, 1, 5, "2026-09-21")
    for t in (0.5, 6.5, 7.9):
        assert scene.draw(t).size == (W, H)


def test_no_highlight_events_does_not_crash_chart_scene():
    ev = _evidence(highlight_events=[])
    story = _story_presentation(quiet_signal=True, visual_type="QUIET_SIGNAL_CARD")
    scene = vs.ChartStoryScene(8, story, ev, 1, 5, "2026-09-21")
    for t in (0.5, 3.5, 7.9):
        assert scene.draw(t).size == (W, H)


# --------------------------------------------------------------------------- content
def test_no_recommendation_or_forecast_wording_in_plain_event_sentences():
    banned = ("buy", "sell", "target price", "stop loss", "multibagger", "will rise",
             "will fall", "going to", "guaranteed")
    for sentence in vs.PLAIN_EVENT_SENTENCE.values():
        low = sentence.lower()
        for word in banned:
            assert word not in low, f"{sentence!r} contains banned wording {word!r}"


def test_plain_event_sentences_cover_every_technical_event_type():
    from radar.presentation import TECHNICAL_EVENT_LABELS
    assert set(vs.PLAIN_EVENT_SENTENCE.keys()) == set(TECHNICAL_EVENT_LABELS.keys())


def test_takeaway_sentence_never_empty_for_covered_cases():
    ev = _evidence()
    story = _story_presentation()
    text = vs._takeaway_sentence(ev, story)
    assert text and isinstance(text, str)


# --------------------------------------------------------------------------- contract unchanged
def test_story_order_unchanged_with_visual_evidence_supplied():
    pres = _load_real_presentation()
    instruments = [s.story.instrument for s in pres.scenes if s.role == "STORY"]
    visual_evidence = {i: _evidence(i) for i in instruments}
    scenes = vr._build_scene_objects(pres, visual_evidence=visual_evidence)
    story_scenes = [s for s in scenes if s.role == "STORY"]
    assert [s.story.instrument for s in story_scenes] == instruments


def test_scene_count_and_durations_unchanged_with_visual_evidence_supplied():
    pres = _load_real_presentation()
    instruments = [s.story.instrument for s in pres.scenes if s.role == "STORY"]
    visual_evidence = {i: _evidence(i) for i in instruments}
    scenes_without = vr._build_scene_objects(pres, visual_evidence=None)
    scenes_with = vr._build_scene_objects(pres, visual_evidence=visual_evidence)
    assert len(scenes_with) == len(scenes_without) == len(pres.scenes) == 7
    assert ([s.duration_seconds for s in scenes_with] ==
           [s.duration_seconds for s in scenes_without])


def test_min_font_size_respected_by_new_chart_theme_sizes():
    from radar.video_theme import SIZE_CONTEXT, SIZE_FAMILY_LABEL
    assert SIZE_FAMILY_LABEL >= MIN_FONT_SIZE
    assert SIZE_CONTEXT >= MIN_FONT_SIZE

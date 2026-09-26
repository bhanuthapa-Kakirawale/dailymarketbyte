"""Radar visual renderer (Phase 4.2 Packet 6.2).

    RadarPresentation / radar_presentation_<date>.json
            |
    render_radar_video (this module)
            |
    radar_<date>.mp4 + radar_<date>_manifest.json

## Hard boundary (packet spec sections 2/35; extended by Phase 4.2 Packet 6.2V)

This module still reruns no calculation and still never touches raw OHLCV or a provider - it
consumes `radar.presentation.RadarPresentation` plus, optionally, a
`{instrument: RadarVisualEvidence | None}` dict that the sibling `visual_evidence` module (in
this same package) already fully computed (see that module for where the chart's numbers/series
actually come from). This module never builds that evidence itself and never imports the OHLCV
store or the detector packages - it only decides, per story, whether to draw the chart it was
handed (`ChartStoryScene`) or fall back to the existing text card (`StoryCardScene`) when no
evidence is available for that instrument. Every number or fact drawn on screen was already
present on the input presentation or the input evidence; this module may add ONLY layout
elements that are not market facts - a brand mark, a scene counter, the session date, and fixed
template words ("Positive Alignment") that themselves came from already-validated label fields.

## Content safety (packet spec section 34)

If `presentation.status == "CONTENT_SAFETY_BLOCKED"` (or, defensively,
`presentation.content_safety_status == "BLOCKED"`), this module refuses to render at all -
`render_radar_video` returns a `RadarVideoRenderResult` with `render_status="BLOCKED"` and no
output file, never a replacement/sanitized video of its own invention.

## Audio (packet spec section 40)

No narration, no music. The output MP4 has NO audio stream at all - `ffmpeg` is invoked with a
single video-only input, so there's nothing to mux; the file is silent by omission, not by a
muted/empty audio track.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

import config
from core.content_safety import SafetyStatus
from video import _ffmpeg

from .video_scenes import (Backdrop, ChartStoryScene, ClosingScene, HookScene, StoryCardScene,
                          TRANSITION_SECONDS, draw_disclaimer, draw_progress_line,
                          radar_header_layer)
from .video_theme import (CONTENT_BOTTOM, CONTENT_LEFT, CONTENT_RIGHT, CONTENT_TOP, FPS, H,
                          MIN_FONT_SIZE, RADAR_RENDERER_VERSION, W)


def _format_session_date(iso_date: str) -> str:
    """The SAME human date format `main.py`'s own header uses ("Wednesday, 23 September
    2026") - built from the presentation's own `session_date` field, never a new date source."""
    try:
        return dt.date.fromisoformat(iso_date).strftime("%A, %d %B %Y")
    except Exception:
        return iso_date

ARTIFACT_DIR = os.path.join(config.OUT_DIR, "radar", "video")
PREVIEW_SUBDIR = "previews"


@dataclass
class RadarVideoRenderResult:
    session_date: str
    presentation_id: str
    renderer_version: str
    output_path: str | None
    width: int
    height: int
    fps: int
    expected_duration_seconds: float
    actual_duration_seconds: float
    frame_count: int
    scene_count: int
    render_status: str  # "OK" | "BLOCKED" | "NO_STORIES" | "FAILED"
    content_safety_status: str
    manifest_path: str | None = None
    preview_paths: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    qa: dict = field(default_factory=dict)
    stage_timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date, "presentation_id": self.presentation_id,
            "renderer_version": self.renderer_version, "output_path": self.output_path,
            "width": self.width, "height": self.height, "fps": self.fps,
            "expected_duration_seconds": self.expected_duration_seconds,
            "actual_duration_seconds": self.actual_duration_seconds,
            "frame_count": self.frame_count, "scene_count": self.scene_count,
            "render_status": self.render_status,
            "content_safety_status": self.content_safety_status,
            "manifest_path": self.manifest_path, "preview_paths": list(self.preview_paths),
            "warnings": list(self.warnings), "qa": dict(self.qa),
            "stage_timings": dict(self.stage_timings),
        }


def _build_scene_objects(presentation, visual_evidence: dict | None = None) -> list:
    """`RadarPresentationScene` (pure data) -> a drawable scene object. One-to-one, in the
    SAME order the presentation already has (packet spec section 3: story order preserved).

    `visual_evidence` (Phase 4.2 Packet 6.2V) is an optional `{instrument:
    RadarVisualEvidence | None}` dict, already fully built by `radar.visual_evidence`. A STORY
    scene whose instrument has real evidence gets `ChartStoryScene`; every other STORY scene
    (evidence missing, unavailable, or `visual_evidence` not supplied at all) keeps the
    existing, unchanged `StoryCardScene` text card - the safe fallback the packet requires, not
    a rendering failure."""
    visual_evidence = visual_evidence or {}
    scenes = []
    story_scenes = [s for s in presentation.scenes if s.role == "STORY"]
    story_count = len(story_scenes)
    story_index = 0
    for sc in presentation.scenes:
        if sc.role == "HOOK":
            scenes.append(HookScene(sc.duration_seconds, sc.headline, sc.subheadline,
                                    presentation.story_count, presentation.session_date))
        elif sc.role == "STORY":
            story_index += 1
            evidence = visual_evidence.get(sc.story.instrument)
            if evidence is not None:
                scenes.append(ChartStoryScene(sc.duration_seconds, sc.story, evidence,
                                              story_index, story_count,
                                              presentation.session_date))
            else:
                scenes.append(StoryCardScene(sc.duration_seconds, sc.story, story_index,
                                             story_count, presentation.session_date))
        elif sc.role == "CLOSING":
            scenes.append(ClosingScene(sc.duration_seconds, sc.headline, sc.subheadline))
    return scenes


def _visual_qa(presentation, scenes: list) -> dict:
    """Deterministic layout/content QA (packet spec section 36) - no subjective evaluation."""
    notes = []
    empty_headlines = [sc.role for sc in presentation.scenes
                       if sc.role in ("HOOK", "CLOSING") and not sc.headline]
    story_scenes = [sc for sc in presentation.scenes if sc.role == "STORY"]
    empty_story_headlines = [sc.story.instrument for sc in story_scenes if not sc.story.headline]
    counters_valid = all(
        1 <= scenes[i].scene_number <= scenes[i].story_count
        for i in range(len(scenes)) if getattr(scenes[i], "role", None) == "STORY")
    scene_count_match = len(scenes) == len(presentation.scenes)
    duration_match = abs(sum(s.duration_seconds for s in scenes) -
                         presentation.estimated_duration_seconds) < 1e-6
    all_scenes_have_draw = all(hasattr(s, "draw") for s in scenes)

    if empty_headlines:
        notes.append(f"empty headline on scene role(s): {empty_headlines}")
    if empty_story_headlines:
        notes.append(f"empty story headline for: {empty_story_headlines}")

    return {
        "no_empty_headline": not empty_headlines and not empty_story_headlines,
        "story_counter_valid": counters_valid,
        "scene_count_matches_presentation": scene_count_match,
        "duration_matches_presentation": duration_match,
        "all_scenes_render": all_scenes_have_draw,
        "min_font_size": MIN_FONT_SIZE,
        "safe_area": {"top": CONTENT_TOP, "bottom": CONTENT_BOTTOM, "left": CONTENT_LEFT,
                      "right": CONTENT_RIGHT},
        "notes": notes,
        "passed": (not empty_headlines and not empty_story_headlines and counters_valid and
                  scene_count_match and duration_match and all_scenes_have_draw),
    }


def _composite_frame(scenes: list, starts: list, t: float, total_duration: float,
                     backdrop: "Backdrop", header_img: Image.Image) -> Image.Image:
    """Mirrors `video.render()`'s own per-frame composition exactly: the animated gradient
    background, then the (static, pre-built) header, then the current scene's own content
    layer with its transition fade, then the progress line and disclaimer on top - every
    Radar frame goes through the SAME layering order the main product's own frames do."""
    i = int(np.searchsorted(starts, t, side="right") - 1)
    i = max(0, min(i, len(scenes) - 1))
    scene, tl = scenes[i], t - starts[i]

    frame = backdrop.frame(t).convert("RGB")
    frame.paste(header_img, (0, 0), header_img)

    layer = scene.draw(tl)
    alpha_scale = 1.0
    if tl < TRANSITION_SECONDS:
        alpha_scale = min(alpha_scale, tl / TRANSITION_SECONDS)
    remaining = scene.duration_seconds - tl
    if remaining < TRANSITION_SECONDS:
        alpha_scale = min(alpha_scale, max(remaining, 0.0) / TRANSITION_SECONDS)
    if alpha_scale < 1.0:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha_scale)))
    frame.paste(layer, (0, 0), layer)

    d = ImageDraw.Draw(frame)
    draw_progress_line(d, t / total_duration if total_duration else 0.0)
    draw_disclaimer(d)
    return frame


def render_radar_video(presentation, *, out_dir: str | None = None,
                       export_previews: bool = True,
                       visual_evidence: dict | None = None) -> RadarVideoRenderResult:
    """The ONE rendering implementation (packet spec section 3). `presentation` is a
    `radar.presentation.RadarPresentation` (or an equivalent dict from
    `radar_presentation_<date>.json`).

    `visual_evidence` (Phase 4.2 Packet 6.2V) is an optional, already-built
    `{instrument: radar.visual_evidence.RadarVisualEvidence | None}` dict - see
    `radar.visual_evidence.build_visual_evidence_for_presentation` for the usual way to build
    it. Omitted or `None` means every story falls back to the existing text card, unchanged."""
    out_dir = out_dir or config.OUT_DIR
    data = presentation.to_dict() if hasattr(presentation, "to_dict") else dict(presentation)
    session_date = data["session_date"]
    pres_id = data["presentation_id"]
    status = data.get("status")
    content_safety_status = data.get("content_safety_status", SafetyStatus.SAFE.value)

    if status == "CONTENT_SAFETY_BLOCKED" or content_safety_status == SafetyStatus.BLOCKED.value:
        return RadarVideoRenderResult(
            session_date=session_date, presentation_id=pres_id,
            renderer_version=RADAR_RENDERER_VERSION, output_path=None, width=W, height=H,
            fps=FPS, expected_duration_seconds=0, actual_duration_seconds=0, frame_count=0,
            scene_count=0, render_status="BLOCKED", content_safety_status=content_safety_status,
            warnings=["presentation content safety BLOCKED; refusing to render"])

    scenes_data = data.get("scenes") or []
    if not scenes_data:
        return RadarVideoRenderResult(
            session_date=session_date, presentation_id=pres_id,
            renderer_version=RADAR_RENDERER_VERSION, output_path=None, width=W, height=H,
            fps=FPS, expected_duration_seconds=0, actual_duration_seconds=0, frame_count=0,
            scene_count=0, render_status="NO_STORIES", content_safety_status=content_safety_status,
            warnings=list(data.get("warnings") or []))

    stage_timings: dict = {}
    t0 = time.time()
    scenes = _build_scene_objects(presentation if hasattr(presentation, "to_dict") else
                                  _AsObj(data), visual_evidence)
    qa = _visual_qa(presentation if hasattr(presentation, "to_dict") else _AsObj(data), scenes)
    stage_timings["scene_build_ms"] = round((time.time() - t0) * 1000, 1)

    story_instruments = [s["story"]["instrument"] for s in scenes_data
                         if s.get("role") == "STORY" and s.get("story")]
    evidence_coverage = {
        "stories_total": len(story_instruments),
        "stories_with_chart_evidence": sum(
            1 for i in story_instruments if (visual_evidence or {}).get(i) is not None),
    }

    expected_duration = sum(s.duration_seconds for s in scenes)
    starts = list(np.cumsum([0] + [s.duration_seconds for s in scenes[:-1]]))

    video_dir = os.path.join(out_dir, "radar", "video")
    os.makedirs(video_dir, exist_ok=True)
    output_path = os.path.join(video_dir, f"radar_{session_date}.mp4")
    preview_dir = os.path.join(video_dir, PREVIEW_SUBDIR)

    frame_count = int(round(expected_duration * FPS))
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-movflags", "+faststart", "-t", f"{expected_duration:.2f}", output_path]

    backdrop = Backdrop()
    header_img = radar_header_layer(_format_session_date(session_date))

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    preview_paths: list = []
    preview_targets = _preview_frame_targets(scenes, starts) if export_previews else {}
    if export_previews:
        os.makedirs(preview_dir, exist_ok=True)

    for fi in range(frame_count):
        t = fi / FPS
        frame = _composite_frame(scenes, starts, t, expected_duration, backdrop, header_img)
        proc.stdin.write(frame.tobytes())
        label = preview_targets.get(fi)
        if label:
            path = os.path.join(preview_dir, f"{session_date}_{label}.png")
            frame.save(path)
            preview_paths.append(path)
    proc.stdin.close()
    ffmpeg_ok = proc.wait() == 0
    stage_timings["encode_ms"] = round((time.time() - t0) * 1000, 1)
    stage_timings["total_ms"] = round(sum(stage_timings.values()), 1)

    warnings = list(data.get("warnings") or [])
    if not ffmpeg_ok:
        warnings.append("ffmpeg exited with a non-zero status")

    qa["visual_evidence_coverage"] = evidence_coverage

    result = RadarVideoRenderResult(
        session_date=session_date, presentation_id=pres_id,
        renderer_version=RADAR_RENDERER_VERSION,
        output_path=output_path if ffmpeg_ok else None, width=W, height=H, fps=FPS,
        expected_duration_seconds=round(expected_duration, 2),
        actual_duration_seconds=round(frame_count / FPS, 2), frame_count=frame_count,
        scene_count=len(scenes), render_status="OK" if ffmpeg_ok else "FAILED",
        content_safety_status=content_safety_status, preview_paths=preview_paths,
        warnings=warnings, qa=qa, stage_timings=stage_timings)

    manifest_path = _save_manifest(result, data, out_dir=video_dir)
    result.manifest_path = manifest_path
    if visual_evidence:
        _save_visual_evidence_artifact(visual_evidence, session_date, out_dir=out_dir)
    return result


def _save_visual_evidence_artifact(visual_evidence: dict, session_date: str,
                                   out_dir: str) -> str:
    """Debug/traceability artifact only (Phase 4.2 Packet 6.2V) - never read back by the video
    itself, written alongside `radar_presentation_<date>.json` so a chart's exact underlying
    series/numbers can be audited after the fact."""
    presentation_dir = os.path.join(out_dir, "radar", "presentation")
    os.makedirs(presentation_dir, exist_ok=True)
    path = os.path.join(presentation_dir, f"radar_visual_evidence_{session_date}.json")
    payload = {instrument: (ev.to_dict() if ev is not None else None)
              for instrument, ev in visual_evidence.items()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


class _AsObj:
    """Adapts a plain dict (e.g. loaded from `radar_presentation_<date>.json`) to the small
    attribute surface `_build_scene_objects`/`_visual_qa` read off a `RadarPresentation` object -
    so this module has ONE code path regardless of whether the caller passed a live object or a
    loaded dict (packet spec section 3)."""
    def __init__(self, data: dict):
        self._data = data
        self.session_date = data["session_date"]
        self.story_count = data.get("story_count", 0)
        self.estimated_duration_seconds = data.get("estimated_duration_seconds", 0)
        self.scenes = [_SceneObj(s) for s in data.get("scenes") or []]


class _SceneObj:
    def __init__(self, s: dict):
        self.scene_order = s["scene_order"]
        self.role = s["role"]
        self.duration_seconds = s["duration_seconds"]
        self.headline = s.get("headline")
        self.subheadline = s.get("subheadline")
        self.story = _StoryObj(s["story"]) if s.get("story") else None


class _StoryObj:
    def __init__(self, s: dict):
        self.instrument = s["instrument"]
        self.company_name = s.get("company_name")
        self.headline = s.get("headline")
        self.primary_fact = s.get("primary_fact")
        self.context_line = s.get("context_line")
        self.evidence_items = [_EvidenceObj(e) for e in s.get("evidence_items") or []]
        self.active_families = s.get("active_families") or []
        self.novelty_label = s.get("novelty_label")
        self.direction_label = s.get("direction_label")
        self.price_change_display = s.get("price_change_display")
        self.quiet_signal = s.get("quiet_signal", False)
        self.visual_type = s.get("visual_type")
        self.duration_seconds = s.get("duration_seconds")
        self.source_selection_id = s.get("source_selection_id")
        self.supporting_session_dates = s.get("supporting_session_dates") or []
        self.provenance = s.get("provenance") or {}
        self.narration_facts = s.get("narration_facts") or []


class _EvidenceObj:
    def __init__(self, e: dict):
        self.label = e["label"]
        self.tier = e["tier"]
        self.source_field = e.get("source_field")


def _preview_frame_targets(scenes: list, starts: list) -> dict:
    """One representative frame index per scene, around its midpoint (packet spec section 37),
    labelled by role (`hook`/`closing`) or instrument (STORY scenes)."""
    targets = {}
    for i, scene in enumerate(scenes):
        mid_t = starts[i] + scene.duration_seconds / 2
        fi = int(round(mid_t * FPS))
        if getattr(scene, "role", None) == "STORY":
            label = scene.story.instrument
        else:
            label = scene.role.lower()
        targets[fi] = label
    return targets


def render_radar_video_from_file(path: str, *, out_dir: str | None = None,
                                 export_previews: bool = True,
                                 radar_result_path: str | None = None) -> RadarVideoRenderResult:
    """`radar_result_path` (Phase 4.2 Packet 6.2V), when given, is the `daily_radar_<date>.json`
    that produced `path`'s presentation - loaded and handed to
    `radar.visual_evidence.build_visual_evidence_for_presentation` to build real chart evidence
    for every story it can. Omitted: every story renders as the existing text card, unchanged -
    this parameter is purely additive."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    visual_evidence = None
    if radar_result_path:
        from .visual_evidence import build_visual_evidence_for_presentation
        with open(radar_result_path, encoding="utf-8") as fh:
            radar_result = json.load(fh)
        visual_evidence = build_visual_evidence_for_presentation(
            data, radar_result, out_dir=out_dir or config.OUT_DIR)
    return render_radar_video(data, out_dir=out_dir, export_previews=export_previews,
                              visual_evidence=visual_evidence)


def _save_manifest(result: RadarVideoRenderResult, presentation_data: dict, out_dir: str) -> str:
    manifest = {
        "presentation_id": presentation_data.get("presentation_id"),
        "presentation_version": presentation_data.get("presentation_version"),
        "renderer_version": result.renderer_version,
        "scene_order": [
            {"scene_order": s["scene_order"], "role": s["role"],
             "duration_seconds": s["duration_seconds"],
             "instrument": (s.get("story") or {}).get("instrument")}
            for s in presentation_data.get("scenes") or []],
        "source_artifact": presentation_data.get("source_radar_artifact"),
        "output_mp4": result.output_path,
        "render_timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "render_status": result.render_status,
        "qa": result.qa,
    }
    path = os.path.join(out_dir, f"radar_{result.session_date}_manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, default=str)
    return path


__all__ = ["RadarVideoRenderResult", "render_radar_video", "render_radar_video_from_file",
          "RADAR_RENDERER_VERSION"]

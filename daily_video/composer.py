"""Render a `Storyboard` into ONE continuous Daily Market Byte MP4, plus freeze frames and a
freeze-frame QA report built from the actual draw calls (text boxes and annotation marks
recorded while the frame was painted - not guessed after the fact).
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import time

import numpy as np
from PIL import Image

from video import _ffmpeg, ease

from . import theme
from .chrome import Chrome
from .hook_scene import DynamicHookScene
from .market_scenes import (GlobalContextScene, MarketPulseScene, MarketStructureScene,
                            MoversDuelScene, QuickCloseScene, SectorBoardScene, SpecialEventScene)
from .pre_scenes import (PreEventScene, PreOvernightScene, PreStockWatchScene, PreVixScene,
                         PreWatchScene)
from .public_scenes import (ExchangeWatchScene, IPOWatchScene, PrimaryMarketScene,
                            StructureScene)
from .radar_scenes import RadarIntroScene
from .radar_story_scene import RadarStockScene
from .scenes import AheadScene, FlowsScene, HookScene
from .typography import Ink, Recorder

SCENE_CLASSES = {
    "HOOK": HookScene, "PULSE": MarketPulseScene, "NIFTY": MarketStructureScene,
    "FLOWS": FlowsScene, "SECTORS": SectorBoardScene, "MOVERS": MoversDuelScene,
    "GLOBAL": GlobalContextScene, "EVENT": SpecialEventScene, "RADAR_INTRO": RadarIntroScene,
    "RADAR_STORY": RadarStockScene, "AHEAD": AheadScene, "CLOSING": QuickCloseScene,
    "DYNAMIC_HOOK": DynamicHookScene,
    # PRE-MARKET V1 (the setup, sectors, flows and close reuse the POST scenes above)
    "PRE_OVERNIGHT": PreOvernightScene, "PRE_VIX": PreVixScene, "PRE_EVENT": PreEventScene,
    "PRE_STOCKS": PreStockWatchScene, "PRE_WATCH": PreWatchScene,
    # public market intelligence V1 (PUBLIC_UNREGISTERED)
    "STRUCTURE": StructureScene, "EXCHANGE_WATCH": ExchangeWatchScene,
    "IPO_WATCH": IPOWatchScene, "IPO_BOARD": PrimaryMarketScene,
}

REQUIRED_MARKS = {
    # Radar stock stories (Phase 2): keyed by the story model's event family
    "RANGE_UP": {"candles", "range_band", "event_candle", "price_tag"},
    "RANGE_DOWN": {"candles", "range_band", "event_candle", "price_tag"},
    "MA_UP": {"candles", "ma_line", "event_candle", "price_tag"},
    "MA_DOWN": {"candles", "ma_line", "event_candle", "price_tag"},
    "COMPRESSION": {"candles", "event_candle"},
    # no validated technical event (POST final edge-case patch): never an event_candle
    "VOLUME": {"candles", "volume_bars", "volume_spike", "price_tag"},
    "SESSION": {"candles", "day_range", "price_tag"},
    "TEXT": {"text_card"},
    # legacy Radar story mechanisms (pre-Phase 2 scene, kept for the standalone renderer tests)
    "CONVERGENCE": {"range_band", "breakout", "volume_spike", "relative_gap"},
    "BREAKOUT": {"range_band", "breakout", "relative_gap"},
    "BREAKDOWN": {"range_band", "breakdown", "relative_gap"},
    "QUIET": {"spotlight", "magnifier", "ma_cross", "relative_gap"},
    "MIXED": {"mixed_split", "ma_cross", "relative_gap"},
    "NIFTY": {"close_marker", "ema_gap"},
    "PULSE": {"close_marker", "session_range"},
    "SECTORS": {"heat_tile"},
    "DYNAMIC_HOOK": {"hook_hero"},
    "PRE_OVERNIGHT": {"cue_card"},
    "PRE_VIX": {"vix_bars"},
    "PRE_EVENT": {"event_card", "event_calendar"},
    "PRE_STOCKS": {"stock_card"},
    "PRE_WATCH": {"watch_card"},
    "STRUCTURE": {"structure_hero"},
    "EXCHANGE_WATCH": {"exchange_card"},
    "IPO_WATCH": {"ipo_card"},
    "IPO_BOARD": {"ipo_row"},
}


def build_scenes(storyboard) -> list:
    return [SCENE_CLASSES[s.kind](s) for s in storyboard.scenes]


class Composer:
    def __init__(self, storyboard, watermark: str | None = None):
        """`watermark`: a red corner badge on every frame (e.g. "SYNTHETIC DATA - NOT REAL")
        so preview renders built from fixtures can never be mistaken for a real session."""
        self.sb = storyboard
        self.watermark = watermark
        self.scenes = build_scenes(storyboard)
        self.starts = storyboard.starts()
        self.total = storyboard.total_duration
        self.chrome = Chrome(storyboard.date_label, storyboard.kicker, storyboard.sections(),
                             self.total)

    def scene_at(self, t):
        i = 0
        for k, s0 in enumerate(self.starts):
            if t >= s0:
                i = k
        return i, t - self.starts[i]

    def frame(self, t: float, recorder: Recorder | None = None) -> Image.Image:
        i, tl = self.scene_at(t)
        scene = self.scenes[i]
        spec = scene.spec
        ink = Ink(recorder)
        frame = self.chrome.background(t, dim=spec.dim_background)
        self.chrome.draw_header(frame, t, counter=spec.counter, ink=ink)
        entry = not getattr(spec, "self_animated", False)
        # Hand-off (Phase 3). The outgoing scene stays whole until the cut, then lifts away:
        # its HEADLINE zone clears almost at once (so it never overprints the incoming headline,
        # which every scene places in the same spot), while the rest fades on an ease-in curve
        # that keeps the screen occupied until the incoming scene - whose own elements start
        # from zero - has built up. Measured: no frame at any cut falls to the contrast of an
        # empty background. (A full-screen vertical push was rejected: inside a Short it looks
        # like the feed's own swipe to the next video.)
        T = theme.TRANSITION_IN
        if i > 0 and tl < T:
            prev = self.scenes[i - 1]
            old = prev.draw(max(0.0, prev.duration - 0.02), None)
            a_top = max(0.0, 1.0 - tl / (0.35 * T))
            a_body = 1.0 - (tl / T) ** 1.6
            # feathered boundary: no line of text is ever visibly sliced
            rows = np.arange(old.height, dtype=np.float32)
            w = np.clip((rows - (HEADLINE_ZONE - FEATHER / 2)) / FEATHER, 0.0, 1.0)
            mult = a_top * (1 - w) + a_body * w
            a_arr = np.asarray(old.getchannel("A"), dtype=np.float32) * mult[:, None]
            old.putalpha(Image.fromarray(a_arr.astype(np.uint8), "L"))
            frame.alpha_composite(old, (0, -int(ease(tl / T) * 40)))
        layer = scene.draw(tl, recorder)
        k = ease(tl / (0.5 * T)) if entry and tl < 0.5 * T else 1.0
        if k < 1.0:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * k)))
        dy = int((1 - ease(min(1.0, tl / T))) * 26) if entry else 0
        frame.alpha_composite(layer, (0, dy))
        self.chrome.draw_footer(frame, ink=ink)
        if self.watermark:
            _badge(frame, self.watermark)
        return frame

    def render(self, out_path: str, fps: int = theme.FPS) -> dict:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{theme.CANVAS_W}x{theme.CANVAS_H}", "-r", str(fps), "-i", "-",
               "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", "-t", f"{self.total:.3f}", out_path]
        t0 = time.time()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        frames = int(round(self.total * fps))
        for fi in range(frames):
            proc.stdin.write(self.frame(fi / fps).convert("RGB").tobytes())
        proc.stdin.close()
        ok = proc.wait() == 0
        return {"output_path": out_path if ok else None, "ok": ok, "frames": frames,
                "fps": fps, "duration": round(frames / fps, 3),
                "render_seconds": round(time.time() - t0, 1)}

    # ------------------------------------------------------------------ freeze-frame QA
    def freeze(self, index: int, t_local: float | None = None):
        spec = self.sb.scenes[index]
        tl = spec.freeze.get("t", spec.duration * 0.8) if t_local is None else t_local
        rec = Recorder()
        img = self.frame(self.starts[index] + tl, rec)
        return img, rec, qa_check(spec, rec)

    def export_freeze_frames(self, out_dir: str, names: list | None = None,
                             extra_times: dict | None = None) -> dict:
        os.makedirs(out_dir, exist_ok=True)
        report = {"scenes": [], "passed": True}
        for i, spec in enumerate(self.sb.scenes):
            name = (names[i] if names else f"{i:02d}_{spec.kind.lower()}")
            img, rec, qa = self.freeze(i)
            path = os.path.join(out_dir, f"{name}.png")
            img.convert("RGB").save(path)
            entry = {"scene": name, "kind": spec.kind, "freeze_t": spec.freeze.get("t"),
                     "image": path, "headline": spec.headline, "what": spec.freeze.get("what"),
                     "where": spec.freeze.get("where"), "why": spec.freeze.get("why"),
                     "mode": spec.freeze.get("mode"), "qa": qa}
            for label, tl in (extra_times or {}).get(name, {}).items():
                img2, _, _ = self.freeze(i, tl)
                p2 = os.path.join(out_dir, f"{name}_{label}.png")
                img2.convert("RGB").save(p2)
                entry.setdefault("progressive", {})[label] = p2
            report["scenes"].append(entry)
            report["passed"] = report["passed"] and qa["passed"]
        with open(os.path.join(out_dir, "freeze_frame_qa.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
        return report


HEADLINE_ZONE = 520      # every scene's headline block (incl. the hook's 2-line curiosity) ends above
FEATHER = 90


def _badge(frame, text):
    from PIL import ImageDraw
    from .typography import font, tlen
    d = ImageDraw.Draw(frame, "RGBA")
    f = font(theme.T_SMALL)
    w = tlen(text, f) + 28
    x1, y0 = theme.X1, 70
    d.rounded_rectangle((x1 - w, y0, x1, y0 + f.size + 18), 10, fill=(200, 30, 40, 235))
    d.text((x1 - w + 14, y0 + 8), text, font=f, fill=(255, 255, 255))


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def qa_check(spec, rec: Recorder) -> dict:
    """Deterministic checks on one frame's recorded geometry."""
    issues = []
    for tb in rec.texts:
        x0, y0, x1, y1 = tb.box
        if x0 < 30 or x1 > theme.CANVAS_W - 30 or y0 < 40 or y1 > theme.RESERVED_BOTTOM:
            issues.append(f"text outside safe area: {tb.text!r} {tb.box}")
        if tb.size < theme.MIN_FONT:
            issues.append(f"font below minimum ({tb.size}): {tb.text!r}")
        if y1 > theme.RIGHT_RAIL_Y + 20 and x1 > theme.RIGHT_RAIL_X + 40 and tb.role != "disclaimer":
            issues.append(f"text under the Shorts action rail: {tb.text!r} {tb.box}")
    for a, b in itertools.combinations(rec.texts, 2):
        area = _overlap(a.box, b.box)
        if area > 0.08 * min((a.box[2] - a.box[0]) * (a.box[3] - a.box[1]),
                             (b.box[2] - b.box[0]) * (b.box[3] - b.box[1])):
            issues.append(f"overlapping labels: {a.text!r} / {b.text!r}")
    for kind, box in rec.marks:
        if kind in ("bar",):
            continue
        if box[1] < theme.STAGE_TOP - 10 or box[3] > theme.STAGE_BOTTOM + 10 or box[0] < 20 \
                or box[2] > theme.CANVAS_W - 20:
            issues.append(f"annotation out of bounds: {kind} {box}")
    callouts = [box for k, box in rec.marks if k == "callout"]
    for a, b in itertools.combinations(callouts, 2):
        if _overlap(a, b) > 0:
            issues.append(f"overlapping callouts: {a} / {b}")
    for tb in rec.texts:
        for box in callouts:
            inside = (tb.box[0] >= box[0] - 2 and tb.box[2] <= box[2] + 2 and
                      tb.box[1] >= box[1] - 2 and tb.box[3] <= box[3] + 2)
            if not inside and _overlap(tb.box, box) > 0:
                issues.append(f"label collides with callout: {tb.text!r}")
    kinds = {k for k, _ in rec.marks}
    need = REQUIRED_MARKS.get(spec.freeze.get("mode") or spec.kind, set())
    if isinstance((spec.texts or {}).get("provenance"), dict):
        need = set(need) | {"provenance"}       # a factual scene must SHOW its source/date
    missing = sorted(need - kinds)
    if missing:
        issues.append(f"missing required visual evidence: {missing}")
    brand = any("DAILY MARKET" in (tb.text or "") for tb in rec.texts)
    return {"passed": not issues, "issues": issues, "marks": sorted(kinds),
            "text_count": len(rec.texts), "min_font": min((tb.size for tb in rec.texts), default=0),
            "brand_in_frame": brand}


__all__ = ["Composer", "build_scenes", "qa_check", "SCENE_CLASSES", "REQUIRED_MARKS"]

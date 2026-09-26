"""Hook Motion Toolkit - the editorial/neon visual vocabulary of the Daily Market Byte hook.

Generic components only: nothing here knows a stock, an archetype or a market rule. Each is
handed pixel positions, already-formatted strings and a progress value, and draws one
deliberate gesture:

    Slip             an off-screen card surface (paper / panel / night) drawn in local
                     coordinates, then placed with a small rotation, scale, shadow and alpha;
                     its text boxes are mapped back to frame coordinates for QA
    neon_ring        a hand-drawn marker circle with glow, drawn on progressively
    marker_underline a hand-drawn underline stroke with glow
    marker_arrow     a curved hand-drawn arrow
    stamp            a rubber-stamp label that lands with a small scale-down
    tape             a strip of translucent highlight tape
    number_reveal    a number that rises into a masked slot (never counts through
                     intermediate values - an unvalidated figure must never be on screen)
    snap / settle    spring-style easing for cards snapping into place

Neon is used sparingly (the selected number, a contrast, an event, a Radar pulse); paper,
panel and ink come from the product palette. Rotations stay within a few degrees.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from video import ease

from . import theme
from .annotations import Ctx
from .chartkit import HiRes
from .typography import Recorder, TextBox, font, tlen

# --------------------------------------------------------------------------- palette
PAPER = (241, 237, 226)
PAPER_EDGE = (208, 199, 180)
PAPER_INK = (22, 26, 42)
PAPER_MUTED = (98, 102, 120)
INK_POS = (6, 146, 94)             # green that holds contrast on paper
INK_NEG = (208, 34, 66)            # red that holds contrast on paper
TAPE = (255, 214, 10)
NEON = theme.BRAND                  # cyan - the product accent
NEON_WARM = theme.HIGHLIGHT         # yellow - "look here"
NIGHT = (8, 12, 34)


class Palette:
    def __init__(self, fill, ink, muted, pos, neg, grid, border):
        self.fill, self.ink, self.muted = fill, ink, muted
        self.pos, self.neg, self.grid, self.border = pos, neg, grid, border

    def signed(self, positive):
        if positive is None:
            return self.ink
        return self.pos if positive else self.neg


PAPER_PAL = Palette(PAPER, PAPER_INK, PAPER_MUTED, INK_POS, INK_NEG, (40, 60, 110, 16), PAPER_EDGE)
PANEL_PAL = Palette(theme.PANEL, theme.TEXT_PRIMARY, theme.TEXT_SECONDARY, theme.POSITIVE,
                    theme.NEGATIVE, (255, 255, 255, 11), theme.PANEL_BORDER)
NIGHT_PAL = Palette(NIGHT, theme.TEXT_PRIMARY, theme.TEXT_SECONDARY, theme.POSITIVE,
                    theme.NEGATIVE, (120, 150, 255, 10), (40, 52, 104))
PALETTES = {"paper": PAPER_PAL, "panel": PANEL_PAL, "night": NIGHT_PAL}


def a(color, p):
    return (*color[:3], max(0, min(255, int((color[3] if len(color) > 3 else 255) * p))))


# --------------------------------------------------------------------------- easing
def clamp(x):
    return max(0.0, min(1.0, x))


def ramp(t, start, dur):
    return ease((t - start) / dur) if dur > 0 else (1.0 if t >= start else 0.0)


def back(x, s=1.6):
    """Ease-out with a small overshoot - a card that snaps, not one that slides."""
    x = clamp(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def snap(t, start, dur=0.3, s=1.6):
    return back((t - start) / dur, s) if t > start else 0.0


# --------------------------------------------------------------------------- textures
@lru_cache(maxsize=8)
def _grain(w, h, seed):
    rng = np.random.RandomState(seed)
    n = rng.normal(0, 1, (h // 2 + 1, w // 2 + 1)).astype(np.float32)
    img = Image.fromarray(np.clip(128 + n * 38, 0, 255).astype(np.uint8), "L")
    return img.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.6))


@lru_cache(maxsize=16)
def _texture(w, h, style):
    """Paper: warm fibre grain + a faint graph grid; panel/night: a faint grid only."""
    pal = PALETTES[style]
    tex = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(tex, "RGBA")
    step = 30
    for x in range(step, w, step):
        d.line([(x, 0), (x, h)], fill=pal.grid, width=1)
    for y in range(step, h, step):
        d.line([(0, y), (w, y)], fill=pal.grid, width=1)
    if style == "paper":
        g = _grain(w, h, 7)
        speck = Image.new("RGBA", (w, h), (90, 70, 40, 0))
        speck.putalpha(g.point(lambda v: max(0, v - 140) // 3))
        tex.alpha_composite(speck)
    return tex


# --------------------------------------------------------------------------- Slip
class Slip:
    """A card drawn in its own local coordinates, then placed on the frame.

    Draw on `self.ctx` (an annotations.Ctx over the card image) using `self.x0/y0` as the
    card's top-left. `place()` rotates/scales/fades it, adds a soft shadow, composites it
    centred on (cx, cy) and maps every recorded text box and mark into frame coordinates.
    """
    PAD = 46

    def __init__(self, w, h, style="paper", radius=22, border=True, texture=True, blank=False):
        self.w, self.h, self.style = int(w), int(h), style
        self.pal = PALETTES[style]
        p = self.PAD
        self.img = Image.new("RGBA", (self.w + 2 * p, self.h + 2 * p), (0, 0, 0, 0))
        self.rec = Recorder()
        self.ctx = Ctx(self.img, self.rec)
        self.x0, self.y0 = p, p
        self.x1, self.y1 = p + self.w, p + self.h
        if blank:
            return
        hr = HiRes((0, 0, self.img.width, self.img.height))
        hr.rect((self.x0, self.y0, self.x1, self.y1), fill=a(self.pal.fill, 1.0), radius=radius,
                outline=a(self.pal.border, 0.9) if border else None, width=1.6)
        hr.composite(self.img)
        if texture:
            mask = Image.new("L", (self.w, self.h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, self.w - 1, self.h - 1), radius, fill=255)
            tex = _texture(self.w, self.h, style).copy()
            tex.putalpha(Image.fromarray(np.minimum(np.asarray(tex.getchannel("A")),
                                                    np.asarray(mask))))
            self.img.alpha_composite(tex, (self.x0, self.y0))

    @property
    def box(self):
        return (self.x0, self.y0, self.x1, self.y1)

    def text(self, xy, s, f, fill, role="text"):
        return self.ctx.ink.text(self.ctx.d, xy, s, f, fill, role)

    def hr(self, box=None):
        return HiRes(box or (0, 0, self.img.width, self.img.height))

    def place(self, fctx: Ctx, cx, cy, angle=0.0, scale=1.0, alpha_=1.0, shadow=0.5,
              record=True, mark="hook_card"):
        self.ctx.flush()
        img = self.img
        if abs(scale - 1.0) > 1e-3:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                             Image.BICUBIC)
        if abs(angle) > 0.01:
            img = img.rotate(angle, Image.BICUBIC, expand=True)
        if alpha_ < 0.999:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha_)))
        ox, oy = int(round(cx - img.width / 2)), int(round(cy - img.height / 2))
        if shadow > 0:
            small = img.getchannel("A").resize((max(1, img.width // 4), max(1, img.height // 4)))
            small = small.filter(ImageFilter.GaussianBlur(5))
            sh = Image.new("RGBA", small.size, (0, 0, 0, 0))
            sh.putalpha(small.point(lambda v: int(v * shadow * 0.8)))
            sh = sh.resize(img.size, Image.BILINEAR)
            _safe_composite(fctx.layer, sh, ox + 10, oy + 16)
        _safe_composite(fctx.layer, img, ox, oy)
        rec = fctx.ink.rec
        if rec is not None and record and alpha_ >= 0.5:
            tf = self._transform(img.size, scale, angle, ox, oy)
            for tb in self.rec.texts:
                rec.texts.append(TextBox(tb.text, tb.role, tf(tb.box), max(1, int(tb.size * scale))))
            for kind, box in self.rec.marks:
                rec.marks.append((kind, tf(box)))
            if mark:
                rec.marks.append((mark, tf(self.box)))
        return tf_box(self._transform(img.size, scale, angle, ox, oy), self.box)

    def _transform(self, out_size, scale, angle, ox, oy):
        cx0, cy0 = self.img.width / 2, self.img.height / 2
        cx1, cy1 = out_size[0] / 2, out_size[1] / 2
        r = math.radians(angle)
        cs, sn = math.cos(r), math.sin(r)

        def tf(box):
            pts = []
            for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
                dx, dy = (x - cx0) * scale, (y - cy0) * scale
                pts.append((cx1 + dx * cs + dy * sn + ox, cy1 - dx * sn + dy * cs + oy))
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        return tf


def tf_box(tf, box):
    return tf(box)


def _safe_composite(layer, im, x, y):
    """alpha_composite that tolerates a card partly off-canvas (a snap-in from the side)."""
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(im.width - sx0, layer.width - dx0)
    h = min(im.height - sy0, layer.height - dy0)
    if w <= 0 or h <= 0:
        return
    layer.alpha_composite(im.crop((sx0, sy0, sx0 + w, sy0 + h)), (dx0, dy0))


# --------------------------------------------------------------------------- strokes
def _stroke(hr, pts, color, p=1.0, core=3.6, glow=True):
    if len(pts) < 2:
        return
    if glow:
        hr.line(pts, a(color, 0.16 * p), core * 4.2)
        hr.line(pts, a(color, 0.34 * p), core * 2.2)
    hr.line(pts, a(color, p), core)


def neon_ring(hr, cx, cy, rx, ry, p, color=NEON, seed=1, width=3.6, glow=True):
    """A marker circle drawn on by hand: starts upper-left, runs ~1.1 turns, drifts outward
    slightly so its ends don't meet exactly - a person drew it, not a compass."""
    if p <= 0:
        return
    a0 = -2.35 + 0.2 * math.sin(seed)
    span = 2 * math.pi * 1.1 * clamp(p)
    n = max(3, int(90 * clamp(p)))
    pts = []
    for i in range(n + 1):
        th = a0 + span * i / n
        k = 1 + 0.035 * math.sin(3 * th + seed) + 0.05 * (i / max(n, 1))
        pts.append((cx + rx * k * math.cos(th), cy + ry * k * math.sin(th)))
    _stroke(hr, pts, color, 1.0, width, glow)


def marker_underline(hr, x0, x1, y, p, color=NEON, seed=2, width=5.0, glow=True):
    if p <= 0:
        return
    xe = x0 + (x1 - x0) * clamp(p)
    n = max(2, int((xe - x0) / 12))
    pts = [(x0 + (xe - x0) * i / n,
            y + 2.4 * math.sin(i * 0.9 + seed) - 3 * (i / max(n, 1))) for i in range(n + 1)]
    _stroke(hr, pts, color, 1.0, width, glow)


def marker_arrow(hr, p0, p1, bend, p, color=NEON_WARM, width=4.0):
    """A curved hand-drawn arrow from p0 to p1; the head lands when the stroke arrives."""
    if p <= 0:
        return
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    ln = math.hypot(nx, ny) or 1
    cx, cy = mx + nx / ln * bend, my + ny / ln * bend
    n = 40
    q = clamp(p)
    pts = []
    for i in range(int(n * q) + 1):
        u = i / n
        pts.append(((1 - u) ** 2 * x0 + 2 * (1 - u) * u * cx + u * u * x1,
                    (1 - u) ** 2 * y0 + 2 * (1 - u) * u * cy + u * u * y1))
    _stroke(hr, pts, color, 1.0, width, glow=True)
    if q > 0.85 and len(pts) > 2:
        (ax, ay), (bx, by) = pts[-3], pts[-1]
        ang = math.atan2(by - ay, bx - ax)
        s = 18
        for d in (2.6, -2.6):
            hr.line([(bx, by), (bx + s * math.cos(ang + d), by + s * math.sin(ang + d))],
                    a(color, 1.0), width)


def tape(hr, cx, cy, w, h, angle, p=1.0, color=TAPE):
    """Translucent highlight tape, slightly rotated, with torn (zig-zag) ends."""
    if p <= 0:
        return
    r = math.radians(angle)
    cs, sn = math.cos(r), math.sin(r)

    def rot(x, y):
        return (cx + x * cs - y * sn, cy + x * sn + y * cs)
    teeth = 5
    top = [rot(-w / 2 + (w * i / teeth), -h / 2) for i in range(teeth + 1)]
    right = [rot(w / 2 + (4 if i % 2 else 0), -h / 2 + h * i / 4) for i in range(5)]
    bottom = [rot(w / 2 - (w * i / teeth), h / 2) for i in range(teeth + 1)]
    left = [rot(-w / 2 - (4 if i % 2 else 0), h / 2 - h * i / 4) for i in range(5)]
    hr.polygon(top + right + bottom + left, a(color, 0.62 * p))


# --------------------------------------------------------------------------- labels
def stamp(fctx, text, cx, cy, p, color=NEON_WARM, angle=-4.0, size=34, fill=None):
    """A rubber-stamp label: lands from 1.35x to 1.0x with a quick fade-in."""
    if p <= 0 or not text:
        return
    f = font(size)
    w, h = tlen(text, f) + 44, size + 30
    s = Slip(w, h, style="panel", blank=True)
    hr = s.hr()
    if fill is not None:
        hr.rect(s.box, fill=a(fill, 0.94), radius=12)
    hr.rect(s.box, outline=a(color, 1.0), radius=12, width=3.4)
    hr.rect((s.x0 + 6, s.y0 + 6, s.x1 - 6, s.y1 - 6), outline=a(color, 0.45), radius=8, width=1.4)
    hr.composite(s.img)
    s.text((s.x0 + 22, s.y0 + 13), text, f, a(color, 1.0), "stamp")
    k = clamp(p)
    s.place(fctx, cx, cy, angle=angle, scale=1.0 + 0.35 * (1 - ease(k)), alpha_=min(1.0, k * 1.6),
            shadow=0.25, mark="hook_stamp")


def pill(hr_or_none, fctx, x, y, text, bg, fg, size=theme.T_SMALL, role="chip", p=1.0):
    f = font(size)
    w, h = tlen(text, f) + 30, size + 18
    hr = HiRes((x, y, x + w, y + h))
    hr.rect((x, y, x + w, y + h), fill=a(bg, p), radius=h / 2)
    hr.composite(fctx.layer)
    fctx.ink.text(fctx.d, (x + 15, y + 8), text, f, a(fg, p), role)
    return x + w


def number_reveal(fctx, x, y, text, f, color, p, role="value"):
    """The number rises into place inside a masked slot. Only the final, validated string is
    ever drawn - no count-up through figures that were never validated."""
    if p <= 0 or not text:
        return None
    x, y = int(round(x)), int(round(y))
    box = fctx.d.textbbox((x, y), text, font=f)
    w, h = int(box[2] - box[0] + 8), int(box[3] - box[1] + 8)
    tmp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((x - box[0] + 4, y - box[1] + 4), text, font=f, fill=color)
    off = int((1 - ease(p)) * h * 0.7)
    if off >= h:
        return None
    part = tmp.crop((0, 0, w, h - off))
    _safe_composite(fctx.layer, part, int(box[0] - 4), int(box[1] - 4 + off))
    if fctx.ink.rec is not None and p >= 0.999:
        fctx.ink.rec.texts.append(TextBox(text, role, tuple(int(v) for v in box), f.size))
    return box


__all__ = ["Slip", "Palette", "PALETTES", "PAPER_PAL", "PANEL_PAL", "NIGHT_PAL", "neon_ring",
           "marker_underline", "marker_arrow", "tape", "stamp", "pill", "number_reveal", "snap",
           "back", "ramp", "clamp", "a", "NEON", "NEON_WARM", "TAPE", "PAPER", "PAPER_INK",
           "INK_POS", "INK_NEG", "NIGHT"]

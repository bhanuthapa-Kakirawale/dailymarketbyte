"""Reusable chart annotations - the vocabulary that tells a viewer WHERE to look.

Every component is generic: it is handed pixel positions and already-formatted label strings,
never a stock symbol, and never computes a market value. Shapes are drawn on a supersampled
`HiRes` surface; text is queued on the `Ctx` and drawn crisp at 1x after compositing, through
the recording `Ink` so QA can check every label's bounds.
"""
from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import theme
from .chartkit import HiRes
from .typography import Ink, font, tlen


class Ctx:
    """Per-frame drawing context: the 1x content layer, a blending ImageDraw over it, the
    recording Ink, and a queue of text jobs to draw after any HiRes composite."""

    def __init__(self, layer: Image.Image, recorder=None):
        self.layer = layer
        self.d = ImageDraw.Draw(layer, "RGBA")
        self.ink = Ink(recorder)
        self.jobs: list = []

    def later(self, xy, s, f, fill, role="annotation"):
        self.jobs.append((xy, s, f, fill, role))

    def flush(self):
        for xy, s, f, fill, role in self.jobs:
            self.ink.text(self.d, xy, s, f, fill, role)
        self.jobs.clear()

    def mark(self, kind, box):
        self.ink.mark(kind, box)


def _a(color, alpha):
    return (*color[:3], max(0, min(255, int(alpha))))


def _clamp_box(x0, y0, w, h, bounds):
    bx0, by0, bx1, by1 = bounds
    x0 = max(bx0, min(x0, bx1 - w))
    y0 = max(by0, min(y0, by1 - h))
    return x0, y0, x0 + w, y0 + h


# --------------------------------------------------------------------------- markers
def event_marker(ctx: Ctx, hr: HiRes, x, y, color, t: float, p: float, kind="event") -> None:
    """Dot + halo + a slow pulsing ring: the single "it happened here" point."""
    if p <= 0:
        return
    hr.circle(x, y, 30 * p, fill=_a(color, 46 * p))
    ring = (13 + 5 * (0.5 + 0.5 * math.sin(t * 5.0))) * p
    hr.circle(x, y, ring, outline=_a(color, 255 * p), width=3.2)
    hr.circle(x, y, 7.5 * p, fill=_a(color, 255 * p))
    hr.circle(x, y, 3.0 * p, fill=_a(theme.TEXT_PRIMARY, 255 * p))
    ctx.mark(kind, (x - 30, y - 30, x + 30, y + 30))


def breakout_marker(ctx, hr, x, y, t, p, color=theme.POSITIVE):
    event_marker(ctx, hr, x, y, color, t, p, kind="breakout")
    if p > 0.3:
        cy = y - 52 - 6 * math.sin(t * 3.0)
        hr.polygon([(x - 13, cy + 12), (x + 13, cy + 12), (x, cy - 8)], _a(color, 255 * p))


def breakdown_marker(ctx, hr, x, y, t, p, color=theme.NEGATIVE):
    event_marker(ctx, hr, x, y, color, t, p, kind="breakdown")
    if p > 0.3:
        cy = y + 52 + 6 * math.sin(t * 3.0)
        hr.polygon([(x - 13, cy - 12), (x + 13, cy - 12), (x, cy + 8)], _a(color, 255 * p))


def ma_cross_marker(ctx, hr, x, y, t, p, color):
    """A crossing point: ring on the intersection plus a small diamond - reads as "the line
    was crossed here", distinct from a plain breakout dot."""
    event_marker(ctx, hr, x, y, color, t, p, kind="ma_cross")
    if p > 0.3:
        s = 9
        hr.polygon([(x, y - 22 - s), (x + s, y - 22), (x, y - 22 + s), (x - s, y - 22)],
                   _a(theme.HIGHLIGHT, 255 * p))


# --------------------------------------------------------------------------- bands / gaps
def range_band(ctx, hr, x0, x1, y_top, y_bot, p, color=theme.TEXT_SECONDARY,
               emphasize: str | None = None, edge_color=None):
    """The prior range as a shaded band with its two edges; `emphasize` thickens the edge the
    story is about ("top" for a breakout, "bottom" for a breakdown)."""
    if p <= 0:
        return
    xr = x0 + (x1 - x0) * p
    hr.rect((x0, y_top, xr, y_bot), fill=_a(color, 30 * p))
    ec = edge_color or color
    for edge, y in (("top", y_top), ("bottom", y_bot)):
        strong = emphasize == edge
        hr.dashed((x0, y), (xr, y), _a(ec if strong else color, (255 if strong else 150) * p),
                  width=3.4 if strong else 2.0)
    ctx.mark("range_band", (x0, y_top, x1, y_bot))


def gap_bracket(ctx, hr, x, y_a, y_b, color, p, kind="relative_gap"):
    """Vertical bracket between two values at one x - how far apart two lines ended."""
    if p <= 0 or abs(y_b - y_a) < 4:
        return
    top, bot = sorted((y_a, y_b))
    mid = (top + bot) / 2
    half = (bot - top) / 2 * p
    a, b = mid - half, mid + half
    hr.line([(x, a), (x, b)], _a(color, 255), 3.0)
    for yy in (a, b):
        hr.line([(x - 10, yy), (x + 10, yy)], _a(color, 255), 3.0)
    ctx.mark(kind, (x - 10, top, x + 10, bot))


# --------------------------------------------------------------------------- labels
def callout(ctx: Ctx, hr: HiRes, anchor, text: str, color, bounds, side="up-left", p=1.0,
            size=theme.T_ANNOT, offset=(36, 44), kind="callout"):
    """A label box joined to the exact point it describes. Placed on `side` of the anchor and
    clamped inside `bounds`, so it can never run off the chart or the safe area."""
    if p <= 0 or not text:
        return None
    f = font(size, True)
    w, h = tlen(text, f) + 36, size + 26
    ax, ay = anchor
    dx, dy = offset
    x0 = {"up-left": ax - dx - w, "down-left": ax - dx - w, "up-right": ax + dx,
          "down-right": ax + dx, "left": ax - dx - w, "right": ax + dx}.get(side, ax - w / 2)
    y0 = {"up-left": ay - dy - h, "up-right": ay - dy - h, "down-left": ay + dy,
          "down-right": ay + dy, "left": ay - h / 2, "right": ay - h / 2}.get(side, ay - dy - h)
    box = _clamp_box(x0, y0, w, h, bounds)
    bx = min(max(ax, box[0]), box[2])
    by = box[3] if ay > box[3] else (box[1] if ay < box[1] else ay)
    if not (box[0] <= ax <= box[2] and box[1] <= ay <= box[3]):
        hr.line([(ax, ay), (bx, by)], _a(color, 230 * p), 2.4)
    hr.rect(box, fill=(8, 12, 28, int(236 * p)), radius=16, outline=_a(color, 255 * p), width=2.4)
    if p > 0.35:
        ctx.later((box[0] + 18, box[1] + 11), text, f, theme.TEXT_PRIMARY, "annotation")
    ctx.mark(kind, box)
    return box


def end_label(ctx, hr, x, y, text, color, bounds, p=1.0, size=theme.T_LABEL):
    """A compact pill at the end of a line (a comparison label: "NIFTY", "This stock")."""
    if p <= 0.2:
        return None
    f = font(size, True)
    w, h = tlen(text, f) + 28, size + 16
    box = _clamp_box(x + 14, y - h / 2, w, h, bounds)
    hr.rect(box, fill=_a(color, 235 * p), radius=h / 2)
    ctx.later((box[0] + 14, box[1] + 7), text, f, theme.INK, "label")
    ctx.mark("comparison_label", box)
    return box


def legend(ctx: Ctx, items: list, cx: float, y: float, p: float = 1.0):
    """A centred key row under a chart: `(kind, colour, text)` with kind "line" or "band".
    Series names live here, never on top of the data they describe."""
    if p <= 0 or not items:
        return
    f = font(theme.T_SMALL, True)
    widths = [44 + tlen(txt, f) for _, _, txt in items]
    gap = 34
    x = cx - (sum(widths) + gap * (len(items) - 1)) / 2
    for (kind, color, txt), w in zip(items, widths):
        hr = HiRes((x, y, x + 36, y + f.size + 6))
        my = y + f.size / 2 + 3
        if kind == "band":
            hr.rect((x + 2, my - 9, x + 32, my + 9), fill=_a(color, 70 * p), radius=3,
                    outline=_a(color, 200 * p), width=1.4)
        else:
            hr.line([(x + 3, my), (x + 31, my)], _a(color, 255 * p), 4.0)
        hr.composite(ctx.layer)
        ctx.later((x + 42, y), txt, f, _a(theme.TEXT_SECONDARY, 255 * p), "legend")
        x += w + gap
    ctx.mark("legend", (cx - 10, y, cx + 10, y + f.size))


def check_chip(ctx: Ctx, x, y, text, p=1.0, color=theme.POSITIVE):
    """A pill with a drawn check mark (not a font glyph - fonts vary by machine)."""
    f = font(theme.T_SMALL, True)
    h = theme.T_SMALL + 20
    w = tlen(text, f) + 62
    hr = HiRes((x, y, x + w, y + h))
    hr.rect((x, y, x + w, y + h), fill=_a(theme.SURFACE_RAISED, 255 * p), radius=h / 2,
            outline=_a(color, 180 * p), width=1.6)
    cy = y + h / 2
    hr.line([(x + 17, cy), (x + 23, cy + 6), (x + 35, cy - 7)], _a(color, 255 * p), 3.4)
    hr.composite(ctx.layer)
    ctx.later((x + 46, y + 9), text, f, _a(theme.TEXT_PRIMARY, 255 * p), "signal_chip")
    return x + w


# --------------------------------------------------------------------------- focus devices
@lru_cache(maxsize=16)
def _spot_mask(w, h, cx, cy, r, feather):
    m = Image.new("L", (w, h), 255)
    ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), fill=0)
    return m.filter(ImageFilter.GaussianBlur(feather))


def spotlight(ctx: Ctx, box, center, r, p, dim=170):
    """Dim everything in `box` except a soft circle around `center` - the eye has nowhere
    else to go."""
    if p <= 0:
        return
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = x1 - x0, y1 - y0
    cx, cy = int(center[0] - x0), int(center[1] - y0)
    mask = _spot_mask(w, h, cx, cy, int(r), 14)
    alpha = mask.point(lambda v: int(v * dim / 255 * p))
    shade = Image.new("RGBA", (w, h), (4, 6, 16, 255))
    shade.putalpha(alpha)
    ctx.layer.alpha_composite(shade, (x0, y0))
    ring = HiRes((center[0] - r - 6, center[1] - r - 6, center[0] + r + 6, center[1] + r + 6))
    ring.circle(center[0], center[1], r, outline=_a(theme.HIGHLIGHT, 255 * p), width=3.0)
    ring.composite(ctx.layer)
    ctx.mark("spotlight", (center[0] - r, center[1] - r, center[0] + r, center[1] + r))


def magnifier(ctx: Ctx, lens_center, radius, draw_zoomed, p, source=None):
    """A circular lens showing a zoomed re-plot of the event region. `draw_zoomed(hr)` draws
    into a HiRes surface covering the lens square (frame coordinates); the result is masked to
    a circle and ringed. `source` (x, y, r) draws a connector from the spotlight to the lens."""
    if p <= 0:
        return
    cx, cy = lens_center
    r = radius * (0.6 + 0.4 * p)
    box = (cx - r, cy - r, cx + r, cy + r)
    if source is not None:
        sx, sy, sr = source
        con = HiRes((min(sx, cx) - r, min(sy, cy) - r, max(sx, cx) + r, max(sy, cy) + r))
        ang = math.atan2(cy - sy, cx - sx)
        con.line([(sx + sr * math.cos(ang), sy + sr * math.sin(ang)),
                  (cx - r * math.cos(ang), cy - r * math.sin(ang))],
                 _a(theme.HIGHLIGHT, 220 * p), 2.6)
        con.composite(ctx.layer)
    lens = HiRes(box)
    lens.circle(cx, cy, r, fill=(8, 13, 32, int(250 * p)))
    draw_zoomed(lens)
    mask = Image.new("L", lens.img.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, lens.img.size[0] - 1, lens.img.size[1] - 1), fill=255)
    lens.img.putalpha(ImageChops.multiply(lens.img.getchannel("A"), mask))
    lens.circle(cx, cy, r - 1.5, outline=_a(theme.HIGHLIGHT, 255 * p), width=4.0)
    lens.composite(ctx.layer)
    ctx.mark("magnifier", box)


__all__ = ["Ctx", "event_marker", "breakout_marker", "breakdown_marker", "ma_cross_marker",
           "range_band", "gap_bracket", "callout", "end_label", "legend", "check_chip",
           "spotlight", "magnifier"]

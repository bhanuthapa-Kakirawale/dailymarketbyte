"""Chart drawing primitives: a supersampled surface and value->pixel geometry.

Nothing here knows what a stock, a range or a moving average is - it maps numbers it is handed
to pixels and draws lines, fills, bars and candles. It performs no market calculation: every
series it plots was computed upstream (`radar.visual_evidence`, the MarketReport's candles).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from . import theme


class HiRes:
    """A 2x drawing surface for one rectangular region of the frame, addressed in FRAME
    coordinates. Downsampled with LANCZOS on composite - smooth, anti-aliased lines and rings
    instead of PIL's default jagged 1x strokes."""

    def __init__(self, box, ss: int = theme.SUPERSAMPLE):
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        self.x0, self.y0, self.w, self.h = x0, y0, max(1, x1 - x0), max(1, y1 - y0)
        self.ss = ss
        self.img = Image.new("RGBA", (self.w * ss, self.h * ss), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img, "RGBA")

    @property
    def box(self):
        return (self.x0, self.y0, self.x0 + self.w, self.y0 + self.h)

    def p(self, x, y):
        return ((x - self.x0) * self.ss, (y - self.y0) * self.ss)

    def _w(self, width):
        return max(1, int(round(width * self.ss)))

    def line(self, pts, fill, width=3.0, caps=True):
        if len(pts) < 2:
            return
        self.d.line([self.p(*q) for q in pts], fill=fill, width=self._w(width), joint="curve")
        if caps:
            r = width * self.ss / 2
            for q in (pts[0], pts[-1]):
                X, Y = self.p(*q)
                self.d.ellipse((X - r, Y - r, X + r, Y + r), fill=fill)

    def dashed(self, a, b, fill, width=2.0, dash=14, gap=10):
        (ax, ay), (bx, by) = a, b
        length = max(1e-6, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
        ux, uy = (bx - ax) / length, (by - ay) / length
        s = 0.0
        while s < length:
            e = min(length, s + dash)
            self.d.line([self.p(ax + ux * s, ay + uy * s), self.p(ax + ux * e, ay + uy * e)],
                        fill=fill, width=self._w(width))
            s = e + gap

    def circle(self, cx, cy, r, fill=None, outline=None, width=2.0):
        X, Y = self.p(cx, cy)
        R = r * self.ss
        self.d.ellipse((X - R, Y - R, X + R, Y + R), fill=fill, outline=outline,
                       width=self._w(width) if outline else 0)

    def rect(self, box, fill=None, radius=0.0, outline=None, width=1.5):
        a, b = self.p(box[0], box[1]), self.p(box[2], box[3])
        rr = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        if rr[2] - rr[0] < 1 or rr[3] - rr[1] < 1:
            return
        if radius:
            self.d.rounded_rectangle(rr, radius * self.ss, fill=fill, outline=outline,
                                     width=self._w(width) if outline else 0)
        else:
            self.d.rectangle(rr, fill=fill, outline=outline, width=self._w(width) if outline else 0)

    def polygon(self, pts, fill):
        if len(pts) >= 3:
            self.d.polygon([self.p(*q) for q in pts], fill=fill)

    def area_fill(self, pts, base_y, color, top_alpha=86):
        """Vertical-gradient fill between a line and `base_y` - the soft glow under a price
        line. The gradient fades to transparent at the baseline."""
        if len(pts) < 2:
            return
        poly = [pts[0][0], base_y], *pts, [pts[-1][0], base_y]
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).polygon([self.p(*q) for q in poly], fill=255)
        ys = [self.p(0, q[1])[1] for q in pts]
        top = max(0, int(min(ys)))
        bottom = int(self.p(0, base_y)[1])
        grad = np.zeros((self.img.size[1], 1), dtype=np.float32)
        if bottom > top:
            grad[top:bottom, 0] = np.linspace(top_alpha, 0, bottom - top)
        grad_img = Image.fromarray(np.repeat(grad, self.img.size[0], axis=1).astype(np.uint8), "L")
        alpha = ImageChops.multiply(mask, grad_img)
        solid = Image.new("RGBA", self.img.size, (*color[:3], 255))
        solid.putalpha(alpha)
        self.img.alpha_composite(solid)

    def composite(self, layer: Image.Image) -> None:
        small = self.img.resize((self.w, self.h), Image.LANCZOS)
        layer.alpha_composite(small, (self.x0, self.y0))


@dataclass
class ChartArea:
    """Value->pixel mapping for a chart plotted inside (x0, y0, x1, y1)."""
    x0: float
    y0: float
    x1: float
    y1: float
    vmin: float
    vmax: float
    n: int

    @classmethod
    def fit(cls, box, values, n, pad=0.12, pad_top=None, pad_bottom=None):
        """Fit the value range into `box`; `pad_top`/`pad_bottom` reserve headroom on one side
        (e.g. room above a breakout for its callout) without squashing the other."""
        vals = [v for v in values if v is not None]
        vmin, vmax = min(vals), max(vals)
        if vmax <= vmin:
            vmax = vmin + 1.0
        span = vmax - vmin
        top = pad if pad_top is None else pad_top
        bottom = pad if pad_bottom is None else pad_bottom
        return cls(*box, vmin - span * bottom, vmax + span * top, n)

    def px(self, i: float) -> float:
        if self.n <= 1:
            return (self.x0 + self.x1) / 2
        return self.x0 + (i / (self.n - 1)) * (self.x1 - self.x0)

    def py(self, v: float) -> float:
        return self.y1 - (v - self.vmin) / (self.vmax - self.vmin) * (self.y1 - self.y0)

    def pt(self, i, v):
        return (self.px(i), self.py(v))


def reveal_points(area: ChartArea, values: list, frac: float, start: int = 0) -> list:
    """The visible part of a line after `frac` of its reveal, with the last segment
    interpolated so the line grows smoothly rather than jumping point to point."""
    n = len(values)
    if n == 0 or frac <= 0:
        return []
    upto = start + max(0.0, min(1.0, frac)) * (n - 1 - start)
    k = int(upto)
    pts = [area.pt(i, values[i]) for i in range(start, k + 1) if values[i] is not None]
    if k < n - 1 and values[k] is not None and values[k + 1] is not None and upto > k:
        f = upto - k
        x = area.px(k) + (area.px(k + 1) - area.px(k)) * f
        y = area.py(values[k]) + (area.py(values[k + 1]) - area.py(values[k])) * f
        pts.append((x, y))
    return pts


def draw_grid(hr: HiRes, area: ChartArea, rows: int = 4) -> None:
    for r in range(rows + 1):
        y = area.y0 + (area.y1 - area.y0) * r / rows
        hr.line([(area.x0, y), (area.x1, y)], theme.GRID, 1.2, caps=False)


def draw_candles(hr: HiRes, area: ChartArea, opens, highs, lows, closes, frac: float) -> None:
    n = len(closes)
    shown = frac * n
    body_w = max(3.0, (area.x1 - area.x0) / max(n, 1) * 0.58)
    for i in range(n):
        g = shown - i
        if g <= 0:
            break
        grow = min(1.0, g)
        up = closes[i] >= opens[i]
        col = theme.POSITIVE if up else theme.NEGATIVE
        x = area.px(i)
        mid = (area.py(opens[i]) + area.py(closes[i])) / 2
        hi, lo = area.py(highs[i]), area.py(lows[i])
        hr.line([(x, mid + (hi - mid) * grow), (x, mid + (lo - mid) * grow)], col + (230,), 1.6,
                caps=False)
        top, bot = sorted((area.py(opens[i]), area.py(closes[i])))
        if bot - top < 2:
            top, bot = mid - 1, mid + 1
        hr.rect((x - body_w / 2, mid + (top - mid) * grow, x + body_w / 2, mid + (bot - mid) * grow),
                fill=col + (255,), radius=1.5)


__all__ = ["HiRes", "ChartArea", "reveal_points", "draw_grid", "draw_candles"]

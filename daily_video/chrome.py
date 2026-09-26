"""The shared frame chrome: background, header, section indicator, progress, footer.

Drawn identically on every frame of the video - main sections and Market Radar alike. There is
exactly one header (DAILY MARKET BYTE), one disclaimer, one progress system. Market Radar is a
section name in the section chip, never a second wordmark.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from video import Backdrop

from . import theme
from .chartkit import HiRes
from .typography import Ink, TextBox, font, tlen

BRAND_PART1, BRAND_PART2 = "DAILY MARKET ", "BYTE"
DISCLAIMER = "For information only - not investment advice"


def header_base(date_label: str, kicker: str) -> Image.Image:
    """Static part of the header: the wordmark and the session date - built once per video."""
    img = Image.new("RGBA", (theme.CANVAS_W, theme.PROGRESS_Y + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    fb = font(theme.T_BRAND)
    x, y = theme.X0, theme.HEADER_BRAND_Y
    w1 = tlen(BRAND_PART1, fb)
    for dx, dy, c1, c2 in ((2, 3, (0, 0, 0, 110), (0, 0, 0, 110)),
                           (0, 0, theme.TEXT_PRIMARY, theme.BRAND)):
        d.text((x + dx, y + dy), BRAND_PART1, font=fb, fill=c1)
        d.text((x + w1 + dx, y + dy), BRAND_PART2, font=fb, fill=c2)
    fd = font(theme.T_DATE)
    d.text((x, theme.HEADER_DATE_Y), date_label, font=fd, fill=theme.DATE)
    if kicker:
        fk = font(theme.T_DATE, False)
        d.text((x + tlen(date_label, fd) + 18, theme.HEADER_DATE_Y), "·  " + kicker, font=fk,
               fill=theme.TEXT_SECONDARY)
    return img


class Chrome:
    """`sections` is the ordered list of `(key, label, start_s, end_s)` built by the
    storyboard; `label` is what the section chip says (empty for hook/closing)."""

    def __init__(self, date_label: str, kicker: str, sections: list, total: float):
        self.base = header_base(date_label, kicker)
        self.sections = sections
        self.total = total
        self.backdrop = Backdrop()
        self.scrim = Image.new("RGBA", (theme.CANVAS_W, theme.CANVAS_H), (2, 4, 12, 64))
        self.progress_sections = [s for s in sections if s[1]]
        self._fchip = font(theme.T_CHIP)
        self._fdisc = font(theme.T_DISCLAIMER, False)

    def section_at(self, t):
        for s in self.sections:
            if s[2] <= t < s[3]:
                return s
        return self.sections[-1]

    def background(self, t: float, dim: bool) -> Image.Image:
        frame = self.backdrop.frame(t).convert("RGBA")
        if dim:
            frame.alpha_composite(self.scrim)
        return frame

    def draw_header(self, frame: Image.Image, t: float, counter: str | None = None,
                    ink: Ink | None = None) -> None:
        frame.alpha_composite(self.base)
        d = ImageDraw.Draw(frame, "RGBA")
        ink = ink or Ink()
        if ink.rec is not None:
            fb = font(theme.T_BRAND)
            w = tlen(BRAND_PART1 + BRAND_PART2, fb)
            ink.rec.texts.append(TextBox(BRAND_PART1 + BRAND_PART2, "brand",
                                         (theme.X0, theme.HEADER_BRAND_Y,
                                          int(theme.X0 + w), theme.HEADER_BRAND_Y + fb.size + 8),
                                         fb.size))
        key, label, s0, s1 = self.section_at(t)
        if label:
            e = min(1.0, (t - s0) / 0.4)
            f = self._fchip
            is_radar = key == "RADAR"
            pad_l = 58 if is_radar else 44
            w = tlen(label, f) + pad_l + 24
            x0, y0 = theme.X0 - (1 - e) * 18, theme.HEADER_CHIP_Y    # the chip slides in
            h = theme.T_CHIP + 22
            alpha = int(255 * e)
            d.rounded_rectangle((x0, y0, x0 + w, y0 + h), h / 2,
                                fill=(*theme.SURFACE_RAISED, alpha))
            if is_radar:
                self._radar_icon(frame, x0 + 30, y0 + h / 2, t, e)
            else:
                d.ellipse((x0 + 18, y0 + h / 2 - 6, x0 + 30, y0 + h / 2 + 6),
                          fill=(*theme.BRAND, alpha))
            ink.text(d, (x0 + pad_l, y0 + 10), label, f, (*theme.TEXT_PRIMARY, alpha), "chip")
            if counter:
                fc = font(theme.T_CHIP)
                ink.right(d, theme.X1, y0 + 10, counter, fc, theme.TEXT_SECONDARY, "counter")
        self._draw_progress(d, t)

    def _radar_icon(self, frame, cx, cy, t, e):
        hr = HiRes((cx - 16, cy - 16, cx + 16, cy + 16))
        col = (*theme.BRAND, int(255 * e))
        hr.circle(cx, cy, 11, outline=col, width=2.0)
        hr.circle(cx, cy, 5, outline=col, width=1.6)
        a = t * 2.4
        hr.line([(cx, cy), (cx + 11 * math.cos(a), cy + 11 * math.sin(a))], col, 2.2)
        hr.composite(frame)

    def _draw_progress(self, d, t):
        secs = self.progress_sections
        if not secs:
            return
        gap = 10
        total_w = theme.X1 - theme.X0 - gap * (len(secs) - 1)
        span = sum(s[3] - s[2] for s in secs)
        x = theme.X0
        y = theme.PROGRESS_Y
        for key, label, s0, s1 in secs:
            w = total_w * (s1 - s0) / span
            d.rounded_rectangle((x, y, x + w, y + 6), 3, fill=(40, 54, 96, 255))
            frac = max(0.0, min(1.0, (t - s0) / (s1 - s0)))
            if frac > 0:
                d.rounded_rectangle((x, y, x + max(6, w * frac), y + 6), 3, fill=theme.BRAND)
            x += w + gap

    def draw_footer(self, frame, ink: Ink | None = None) -> None:
        d = ImageDraw.Draw(frame, "RGBA")
        (ink or Ink()).centered(d, theme.CANVAS_W / 2, theme.FOOTER_Y, DISCLAIMER, self._fdisc,
                                theme.TEXT_SECONDARY, "disclaimer")


__all__ = ["Chrome", "header_base", "DISCLAIMER", "BRAND_PART1", "BRAND_PART2"]

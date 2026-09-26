"""Market Radar stock story (Phase 2): one shell, chart first.

    DAILY MARKET BYTE · MARKET RADAR                        1 / 3
    SYMBOL                                                  +6.0%
    Company
    ┌──────────────────────────────────────────────┬───────────┐
    │  candles (24 sessions), prior range shaded,   │ 20-DAY    │
    │  the event candle ringed + "Broke out here"   │ HIGH ₹... │
    │                                               │ CLOSE ₹...│
    ├──────────────────────────────────────────────┴───────────┤
    │  ONE supporting fact: volume bars aligned to the candles, │
    │  or where the close sat in the day's range                │
    └───────────────────────────────────────────────────────────┘
    One factual sentence.

Every story uses this shell; only the overlay (range band vs one average) and the supporting
strip (volume vs day range) change, driven by the story model's `event_family` and
`support.kind` - never by a symbol. Built from small components (CandlestickPanel,
RangeOverlay, AverageOverlay, EventHighlight, PriceTags, VolumePanel, DayRangePanel, Takeaway).
The model (`presentation.radar_story`) made every decision; this module only draws it.

Animation explains the chart in reading order: identity → candles build → the prior range /
average draws → the event candle is ringed and called out → the close is tagged → the
supporting strip rises → the sentence settles.

No-event stories (POST final edge-case patch) keep the same shell and components but claim no
chart event - no ring, no callout, no reference level:

    VOLUME   candles as neutral context (upper panel) + the volume histogram as the hero
             (lower panel): bars rise → the prior-20-session average draws → the session's bar
             turns amber with the detector's multiple → the close is tagged → the sentence.
    SESSION  candles as neutral context + the day-range strip.

Their colour (close tag) follows the session's own move, never an internal direction label.
"""
from __future__ import annotations

import math

from video import ease

from . import hook_kit as K
from . import theme
from .annotations import Ctx
from .chartkit import ChartArea, HiRes
from .scenes import Scene, alpha, card, phase, signed_color
from .typography import fit, font, tlen, wrap

# Taller chart, a price-tag column wide enough for "20-DAY HIGH" at the minimum font, and the
# evidence strip kept left of the Shorts action rail.
CARD = (theme.X0, 432, theme.X1, 1224)
PLOT = (104, 490, 796, 990)
TAG_X0, TAG_X1 = 834, 1010
STRIP = (104, 1034, 796, 1200)
TAKEAWAY_Y = 1254
# VOLUME (no-event) layout: price context above, the volume histogram - the story - below.
VOL_PLOT = (104, 486, 796, 760)
VOL_STRIP = (104, 800, 796, 1200)

# Reading-order beats (seconds into the scene).
T_IDENTITY, T_CARD, T_CANDLES, T_OVERLAY, T_EVENT, T_CLOSE, T_STRIP, T_FLASH, T_TAKEAWAY = \
    0.0, 0.15, 0.35, 1.35, 1.9, 2.4, 2.7, 3.4, 3.85
CANDLE_BUILD = 1.0


def _dir_color(model_data):
    fam = model_data["event_family"]
    if fam in ("VOLUME", "SESSION"):
        # no event, so no event direction: the session's own validated move decides
        return signed_color(model_data.get("change_positive"))
    if fam.endswith("DOWN"):
        return theme.NEGATIVE
    if fam.endswith("UP"):
        return theme.POSITIVE
    return theme.BRAND


# --------------------------------------------------------------------------- components
class CandlestickPanel:
    """Value->pixel geometry for the window plus the candles themselves. Headroom is reserved
    on the event's side so its callout never sits on the data."""

    def __init__(self, data, plot=PLOT):
        c = data["candles"]
        self.o, self.h, self.l, self.c = c["open"], c["high"], c["low"], c["close"]
        self.n = len(self.c)
        vals = list(self.h) + list(self.l)
        if data.get("reference_level") is not None:
            vals.append(data["reference_level"])
        if data.get("ma_series"):
            vals += [v for v in data["ma_series"] if v is not None]
        if data["event_family"] in ("VOLUME", "SESSION"):
            pad_top = pad_bottom = 0.1          # no callout, so no headroom on either side
        else:
            down = data["event_family"].endswith("DOWN")
            pad_top, pad_bottom = (0.08, 0.3) if down else (0.3, 0.08)
        self.area = ChartArea.fit(plot, vals, self.n, pad=0.06, pad_top=pad_top,
                                  pad_bottom=pad_bottom)
        self.slot = (plot[2] - plot[0]) / max(self.n - 1, 1)
        self.body = max(6.0, self.slot * 0.62)

    def x(self, i):
        return self.area.px(i)

    def y(self, v):
        return self.area.py(v)

    def draw(self, hr, t, focus=0.0):
        """Candles grow in left to right; once the event is in focus, the others step back."""
        shown = phase(t, T_CANDLES, CANDLE_BUILD) * self.n
        for i in range(self.n):
            g = min(1.0, shown - i)
            if g <= 0:
                break
            last = i == self.n - 1
            up = self.c[i] >= self.o[i]
            col = theme.POSITIVE if up else theme.NEGATIVE
            k = 1.0 if last else 1.0 - 0.45 * focus
            x = self.x(i)
            mid = (self.y(self.o[i]) + self.y(self.c[i])) / 2
            hi, lo = self.y(self.h[i]), self.y(self.l[i])
            hr.line([(x, mid + (hi - mid) * g), (x, mid + (lo - mid) * g)], alpha(col, 0.9 * k), 2.2,
                    caps=False)
            top, bot = sorted((self.y(self.o[i]), self.y(self.c[i])))
            if bot - top < 2:
                top, bot = mid - 1.2, mid + 1.2
            w = self.body * (1.12 if last else 1.0)
            hr.rect((x - w / 2, mid + (top - mid) * g, x + w / 2, mid + (bot - mid) * g),
                    fill=alpha(col, k), radius=2.5)


class RangeOverlay:
    """The prior range as a soft band; the edge the story is about is the only strong line."""

    @staticmethod
    def draw(ctx, hr, panel, band, fam, t):
        p = phase(t, T_OVERLAY, 0.5)
        if p <= 0 or not band:
            return
        x0 = panel.x(band["i0"]) - panel.slot / 2
        x1 = panel.x(band["i1"]) + panel.slot / 2
        xr = x0 + (x1 - x0) * p
        # A long-window range can extend far past the visible prices: the band is clipped to the
        # plot, and its far edge is drawn only when it is actually on the chart.
        yt_raw, yb_raw = panel.y(band["high"]), panel.y(band["low"])
        yt, yb = max(yt_raw, PLOT[1] - 12), min(yb_raw, PLOT[3] + 12)
        hr.rect((x0, yt, xr, yb), fill=(255, 255, 255, int(13 * p)))
        up = fam == "RANGE_UP"
        col = theme.POSITIVE if up else theme.NEGATIVE
        edge = yt_raw if up else yb_raw
        other = yb_raw if up else yt_raw
        if PLOT[1] - 12 <= other <= PLOT[3] + 12:
            hr.dashed((x0, other), (xr, other), (255, 255, 255, int(60 * p)), 1.6, dash=8, gap=8)
        # the story's edge runs on to the price tag column, so level and label connect
        hr.line([(x0, edge), (x0 + (TAG_X0 - 6 - x0) * p, edge)], alpha(col, 0.95 * p), 3.2,
                caps=False)
        ctx.mark("range_band", (x0, yt, x1, yb))


class AverageOverlay:
    @staticmethod
    def draw(ctx, hr, panel, ma, t):
        p = phase(t, T_OVERLAY, 0.6)
        if p <= 0 or not ma:
            return
        pts = [(panel.x(i), panel.y(v)) for i, v in enumerate(ma) if v is not None]
        k = max(2, int(len(pts) * p))
        pts = pts[:k]
        hr.line(pts, alpha(theme.BRAND, 0.25), 11.0)
        hr.line(pts, alpha(theme.BRAND, 1.0), 4.0)
        if p >= 1 and pts:
            hr.line([pts[-1], (TAG_X0 - 6, pts[-1][1])], alpha(theme.BRAND, 0.8), 2.0, caps=False)
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        ctx.mark("ma_line", (min(xs), min(ys), max(xs), max(ys)))


class EventHighlight:
    """The event candle: a soft glow column, a hand-drawn neon ring, and a short plain-English
    callout joined to it by a marker arrow."""

    @staticmethod
    def glow(hr, panel, fam, t):
        """Drawn BEFORE the candles: a soft column behind the event candle, never over it."""
        p = phase(t, T_EVENT, 0.45)
        if p <= 0:
            return
        i = panel.n - 1
        x = panel.x(i)
        hi, lo = panel.y(panel.h[i]), panel.y(panel.l[i])
        col = theme.NEGATIVE if fam.endswith("DOWN") else (theme.POSITIVE if fam.endswith("UP")
                                                          else theme.BRAND)
        pulse = 0.5 + 0.5 * math.sin(t * 4.5)
        hr.rect((x - panel.slot * 0.75, hi - 16, x + panel.slot * 0.75, lo + 16),
                fill=alpha(col, (0.16 + 0.06 * pulse) * p), radius=panel.slot * 0.6)

    @staticmethod
    def draw(ctx, hr, panel, fam, callout, t):
        p = phase(t, T_EVENT, 0.45)
        if p <= 0:
            return
        i = panel.n - 1
        x = panel.x(i)
        hi, lo = panel.y(panel.h[i]), panel.y(panel.l[i])
        col = theme.NEGATIVE if fam.endswith("DOWN") else (theme.POSITIVE if fam.endswith("UP")
                                                          else theme.BRAND)
        K.neon_ring(hr, x, (hi + lo) / 2, panel.slot * 0.8, (lo - hi) / 2 + 22, p, color=K.NEON,
                    seed=11)
        ctx.mark("event_candle", (x - panel.slot, hi - 26, x + panel.slot, lo + 26))
        if not callout:
            return
        pc = phase(t, T_EVENT + 0.2, 0.3)
        if pc <= 0:
            return
        f = font(theme.T_LABEL + 2)
        w, h = tlen(callout, f) + 36, f.size + 24
        down = fam.endswith("DOWN")
        cx0 = max(PLOT[0], x - panel.slot * 2.2 - w)
        cy0 = (lo + 50) if down else (hi - 50 - h)
        cy0 = min(max(cy0, PLOT[1] - 30), PLOT[3] - h)
        hr.rect((cx0, cy0, cx0 + w, cy0 + h), fill=(8, 12, 28, int(236 * pc)), radius=h / 2,
                outline=alpha(col, pc), width=2.4)
        ax0 = (cx0 + w, cy0 + h / 2)
        ax1 = (x - panel.slot * 0.95 - 4, (lo + 8) if down else (hi - 8))
        K.marker_arrow(hr, ax0, ax1, 26 if down else -26, ease((t - T_EVENT - 0.3) / 0.3), color=col,
                       width=3.2)
        if pc > 0.4:
            ctx.later((cx0 + 18, cy0 + 11), callout, f, alpha(theme.TEXT_PRIMARY, pc), "annotation")
        ctx.mark("callout", (cx0, cy0, cx0 + w, cy0 + h))


class PriceTags:
    """At most two exact prices, in a right-hand column at their own levels: the reference
    level and the latest close. Tags never overlap - they are pushed apart symmetrically."""
    H = 72

    @classmethod
    def layout(cls, panel, data, strip_top=STRIP[1]):
        tags = []
        if data.get("reference_display"):
            tags.append(["ref", panel.y(data["reference_level"]), data["reference_label"],
                         data["reference_display"]])
        if data.get("latest_close_display"):
            tags.append(["close", panel.y(data["latest_close"]), data["close_label"],
                         data["latest_close_display"]])
        if len(tags) == 2:
            a, b = sorted(tags, key=lambda z: z[1])
            gap = b[1] - a[1]
            if gap < cls.H + 8:
                mid = (a[1] + b[1]) / 2
                a[1], b[1] = mid - (cls.H + 8) / 2, mid + (cls.H + 8) / 2
        lo, hi = CARD[1] + 20 + cls.H / 2, strip_top - 24 - cls.H / 2
        shift = 0.0
        for tg in tags:
            if tg[1] < lo:
                shift = max(shift, lo - tg[1])
            if tg[1] > hi:
                shift = min(shift, hi - tg[1])
        for tg in tags:
            tg[1] += shift
        return tags

    @classmethod
    def draw(cls, ctx, panel, data, t, strip_top=STRIP[1], close_start=T_CLOSE):
        col = _dir_color(data)
        for kind, cy, label, value in cls.layout(panel, data, strip_top):
            start = T_OVERLAY + 0.35 if kind == "ref" else close_start
            p = phase(t, start, 0.3)
            if p <= 0:
                continue
            y0 = cy - cls.H / 2 + (1 - p) * 10
            box = (TAG_X0, y0, TAG_X1, y0 + cls.H)
            hr = HiRes((TAG_X0 - 2, y0 - 2, TAG_X1 + 2, y0 + cls.H + 2))
            if kind == "close":
                hr.rect(box, fill=alpha(col, 0.95 * p), radius=12)
                fg, fl = theme.INK, (20, 26, 48)
            else:
                hr.rect(box, fill=alpha(theme.SURFACE_RAISED, 0.95 * p), radius=12,
                        outline=alpha(col if data["event_family"].startswith("RANGE") else theme.BRAND, p),
                        width=2.0)
                fg, fl = theme.TEXT_PRIMARY, theme.TEXT_SECONDARY
            hr.composite(ctx.layer)
            flab = fit(label, TAG_X1 - TAG_X0 - 20, theme.T_SMALL, min_size=theme.MIN_FONT)
            fval = fit(value, TAG_X1 - TAG_X0 - 20, 30, min_size=theme.MIN_FONT)
            ctx.ink.text(ctx.d, (TAG_X0 + 10, y0 + 6), label, flab, alpha(fl, p), "price_label")
            ctx.ink.text(ctx.d, (TAG_X0 + 10, y0 + 8 + flab.size), value, fval, alpha(fg, p),
                         "price_value")
            ctx.mark("price_tag", box)


class VolumePanel:
    """Volume bars under the same session slots as the candles; the event session's bar turns
    amber and the detector's own multiple is written on the strip."""

    @staticmethod
    def draw(ctx, panel, sup, t):
        vols = sup["volume"]
        p = phase(t, T_STRIP, 0.6)
        if p <= 0:
            return
        top = max(vols) or 1.0
        by0, by1 = STRIP[1] + 62, STRIP[3]
        hr = HiRes((STRIP[0] - 20, STRIP[1], STRIP[2] + 20, STRIP[3] + 4))
        flash = phase(t, T_FLASH, 0.3)
        for i, v in enumerate(vols):
            g = min(1.0, p * len(vols) - i * 0.5) if p < 1 else 1.0
            if g <= 0:
                continue
            x = panel.x(i)
            h = (by1 - by0) * v / top * g
            last = i == len(vols) - 1
            col = theme.VOLUME if last and flash > 0 else theme.VOLUME_BASE
            if last and flash > 0:
                glow = 0.22 + 0.12 * math.sin(t * 5)
                hr.rect((x - panel.body, by1 - h - 12, x + panel.body, by1), fill=alpha(theme.VOLUME, glow * flash),
                        radius=6)
            hr.rect((x - panel.body / 2, by1 - h, x + panel.body / 2, by1), fill=alpha(col, 1.0),
                    radius=min(4, panel.body / 2))
        hr.composite(ctx.layer)
        pl = phase(t, T_FLASH, 0.35)
        if pl <= 0:
            return
        f = font(36)
        ctx.ink.text(ctx.d, (STRIP[0], STRIP[1] + 4 + (1 - pl) * 8), sup["label"], f,
                     alpha(theme.VOLUME, pl), "support_value")
        lx = panel.x(len(vols) - 1)
        ly = by1 - (by1 - by0) * vols[-1] / top
        ha = HiRes((STRIP[0], STRIP[1], STRIP[2] + 30, STRIP[3]))
        x_from = STRIP[0] + tlen(sup["label"], f) + 18
        K.marker_arrow(ha, (x_from, STRIP[1] + 24), (lx - 10, ly - 10), -18,
                       ease((t - T_FLASH - 0.2) / 0.3), color=theme.VOLUME, width=3.0)
        ha.composite(ctx.layer)
        ctx.mark("volume_spike", (lx - panel.body, ly, lx + panel.body, by1))


class VolumeStoryPanel:
    """The VOLUME (no-event) story's hero: the whole visible window's volume under the same
    session slots as the candles, the prior-20-session average as a dashed line, and the
    session's own bar lit amber with the detector's multiple. Nothing about price is claimed."""
    T_BARS, T_AVG, T_SPIKE, T_CLOSE_TAG = 1.2, 1.85, 2.3, 2.9

    @classmethod
    def draw(cls, ctx, panel, sup, t):
        vols = sup["volume"]
        p = phase(t, cls.T_BARS, 0.65)
        if p <= 0 or not vols:
            return
        top = max(vols) or 1.0
        by0, by1 = VOL_STRIP[1] + 74, VOL_STRIP[3]
        hr = HiRes((VOL_STRIP[0] - 30, VOL_STRIP[1], VOL_STRIP[2] + 30, VOL_STRIP[3] + 4))
        spike = phase(t, cls.T_SPIKE, 0.35)
        n = len(vols)
        for i, v in enumerate(vols):
            g = min(1.0, p * n - i * 0.5) if p < 1 else 1.0
            if g <= 0:
                continue
            x = panel.x(i)
            h = (by1 - by0) * v / top * g
            last = i == n - 1
            if last and spike > 0:
                glow = 0.22 + 0.12 * math.sin(t * 5)
                hr.rect((x - panel.body, by1 - h - 14, x + panel.body, by1),
                        fill=alpha(theme.VOLUME, glow * spike), radius=6)
            col = theme.VOLUME if last and spike > 0 else theme.VOLUME_BASE
            hr.rect((x - panel.body / 2, by1 - h, x + panel.body / 2, by1), fill=alpha(col, 1.0),
                    radius=min(4, panel.body / 2))
        ctx.mark("volume_bars", (VOL_STRIP[0], by0, VOL_STRIP[2], by1))
        avg, pa = sup.get("average"), phase(t, cls.T_AVG, 0.4)
        ya = None
        if avg and pa > 0:
            ya = by1 - (by1 - by0) * avg / top
            # the line spans only what it averages: the prior 20 sessions (+ the session's bar)
            i0 = max(0, n - 1 - sup.get("average_sessions", 20))
            xa0, xa1 = panel.x(i0) - panel.slot / 2, panel.x(n - 1) + panel.slot / 2
            hr.dashed((xa0, ya), (xa0 + (xa1 - xa0) * pa, ya), (255, 255, 255, int(150 * pa)),
                      2.2, dash=10, gap=8)
            ctx.mark("volume_average", (xa0, ya - 2, xa1, ya + 2))
        hr.composite(ctx.layer)
        if ya is not None and pa >= 1 and sup.get("average_label"):
            fs = font(theme.T_SMALL)
            lab = sup["average_label"]
            w, h = tlen(lab, fs) + 20, fs.size + 12
            bx, byy = xa0, ya - h - 6
            ctx.d.rounded_rectangle((bx, byy, bx + w, byy + h), 8, fill=(8, 12, 28, 225))
            ctx.ink.text(ctx.d, (bx + 10, byy + 5), lab, fs, theme.TEXT_SECONDARY,
                         "support_caption")
        pl = phase(t, cls.T_SPIKE, 0.35)
        if pl <= 0:
            return
        f = font(40)
        ctx.ink.text(ctx.d, (VOL_STRIP[0], VOL_STRIP[1] + 6 + (1 - pl) * 8), sup["label"], f,
                     alpha(theme.VOLUME, pl), "support_value")
        lx = panel.x(n - 1)
        ly = by1 - (by1 - by0) * vols[-1] / top
        ha = HiRes((VOL_STRIP[0], VOL_STRIP[1], VOL_STRIP[2] + 30, VOL_STRIP[3]))
        x_from = VOL_STRIP[0] + tlen(sup["label"], f) + 18
        K.marker_arrow(ha, (x_from, VOL_STRIP[1] + 28), (lx - 12, ly - 10), -18,
                       ease((t - cls.T_SPIKE - 0.2) / 0.3), color=theme.VOLUME, width=3.0)
        ha.composite(ctx.layer)
        ctx.mark("volume_spike", (lx - panel.body, ly, lx + panel.body, by1))


class DayRangePanel:
    """Price-location fallback: where the close sat inside the session's own low-high range."""

    @staticmethod
    def draw(ctx, sup, data, t):
        p = phase(t, T_STRIP, 0.5)
        if p <= 0:
            return
        loc = sup.get("location", "MID")
        col = theme.POSITIVE if loc == "HIGH" else (theme.NEGATIVE if loc == "LOW" else theme.TEXT_PRIMARY)
        f = font(34)
        ctx.ink.text(ctx.d, (STRIP[0], STRIP[1] + 4 + (1 - p) * 8), sup["label"], f, alpha(col, p),
                     "support_value")
        x0, x1 = STRIP[0] + 60, STRIP[2] - 60
        y = STRIP[1] + 92
        lo, hi, c = sup["day_low"], sup["day_high"], sup["day_close"]
        pos = (c - lo) / (hi - lo) if hi > lo else 0.5     # drawing only: value -> x
        g = phase(t, T_STRIP + 0.1, 0.5)
        hr = HiRes((STRIP[0], y - 40, STRIP[2] + 20, y + 40))
        hr.rect((x0, y - 7, x0 + (x1 - x0) * g, y + 7), fill=alpha(theme.PANEL_BORDER, 1.0), radius=7)
        for xe in (x0, x1):
            if g >= 1:
                hr.rect((xe - 2, y - 18, xe + 2, y + 18), fill=alpha(theme.TEXT_SECONDARY, 1.0), radius=2)
        pm = phase(t, T_FLASH, 0.3)
        xc = x0 + (x1 - x0) * pos
        if pm > 0:
            hr.rect((x0, y - 7, x0 + (xc - x0) * pm, y + 7), fill=alpha(col, 0.55), radius=7)
            hr.circle(xc, y, 12 * pm, fill=alpha(col, 1.0))
            K.neon_ring(hr, xc, y, 24, 22, pm, color=K.NEON, seed=13)
        hr.composite(ctx.layer)
        fs = font(theme.T_SMALL, False)
        if g >= 1:
            ctx.ink.text(ctx.d, (x0 - tlen(sup["low_label"], fs) / 2, y + 38), sup["low_label"], fs,
                         theme.TEXT_SECONDARY, "support_caption")
            ctx.ink.text(ctx.d, (x1 - tlen(sup["high_label"], fs) / 2, y + 38), sup["high_label"], fs,
                         theme.TEXT_SECONDARY, "support_caption")
        ctx.mark("day_range", (x0, y - 18, x1, y + 18))


class Takeaway:
    @staticmethod
    def draw(ctx, text, t, y=TAKEAWAY_Y):
        p = phase(t, T_TAKEAWAY, 0.45)
        if p <= 0 or not text:
            return
        max_w = theme.RIGHT_RAIL_X - theme.X0
        f = fit(text, max_w * 2, theme.T_TITLE + 2, min_size=34)
        yy = y + (1 - p) * 14
        for line in wrap(text, f, max_w)[:2]:
            ctx.ink.text(ctx.d, (theme.X0, yy), line, f, alpha(theme.TEXT_PRIMARY, p), "takeaway")
            yy += int(f.size * 1.2)


class StoryIdentity:
    @staticmethod
    def draw(ctx, tx, positive, t):
        p = phase(t, T_IDENTITY, 0.4)
        fs = fit(tx["symbol"], 620, theme.T_SYMBOL, min_size=50)
        ctx.ink.text(ctx.d, (theme.X0, 280 + (1 - p) * 14), tx["symbol"], fs,
                     alpha(theme.TEXT_PRIMARY, p), "symbol")
        if tx.get("company"):
            ctx.ink.text(ctx.d, (theme.X0 + 2, 280 + fs.size + 8), tx["company"],
                         fit(tx["company"], 700, theme.T_LABEL, bold=False, min_size=theme.MIN_FONT),
                         alpha(theme.TEXT_SECONDARY, p), "company")
        if tx.get("change"):
            col = signed_color(positive)
            f = font(theme.T_TITLE + 4)
            w, h = tlen(tx["change"], f) + 44, f.size + 26
            x0 = theme.X1 - w
            y0 = 292
            hr = HiRes((x0, y0, theme.X1, y0 + h))
            hr.rect((x0, y0, theme.X1, y0 + h), fill=alpha(col, 0.2 * p), radius=h / 2,
                    outline=alpha(col, 0.85 * p), width=2.2)
            hr.composite(ctx.layer)
            ctx.ink.text(ctx.d, (x0 + 22, y0 + 12), tx["change"], f, alpha(col, p), "change")


# --------------------------------------------------------------------------- the scene
class RadarStockScene(Scene):
    """`spec.data` is `RadarStoryModel.to_dict()`; `spec.texts` its declared strings."""

    def paint(self, ctx, t):
        tx, data = self.spec.texts, self.spec.data
        StoryIdentity.draw(ctx, tx, data["change_positive"], t)
        card(ctx, CARD, phase(t, T_CARD, 0.35), fill=theme.PANEL)
        if data["event_family"] == "TEXT" or not data.get("candles"):
            Takeaway.draw(ctx, tx["takeaway"], max(t, T_TAKEAWAY + 0.45), y=CARD[1] + 60)
            ctx.mark("text_card", CARD)
            return
        if data["event_family"] in ("VOLUME", "SESSION"):
            self._paint_no_event(ctx, t, tx, data)
            return
        panel = CandlestickPanel(data)
        hr = HiRes(CARD)
        # faint horizontal guides only - no axis labels, no dates
        for r in range(1, 4):
            yy = PLOT[1] + (PLOT[3] - PLOT[1]) * r / 4
            hr.line([(PLOT[0], yy), (PLOT[2], yy)], (255, 255, 255, 10), 1.2, caps=False)
        hr.line([(CARD[0] + 24, STRIP[1] - 16), (CARD[2] - 24, STRIP[1] - 16)],
                alpha(theme.PANEL_BORDER, phase(t, T_STRIP, 0.3)), 1.4, caps=False)
        fam = data["event_family"]
        focus = phase(t, T_EVENT, 0.5)
        if fam in ("RANGE_UP", "RANGE_DOWN"):
            RangeOverlay.draw(ctx, hr, panel, data.get("band"), fam, t)
        elif fam in ("MA_UP", "MA_DOWN"):
            AverageOverlay.draw(ctx, hr, panel, data.get("ma_series"), t)
        EventHighlight.glow(hr, panel, fam, t)
        panel.draw(hr, t, focus)
        ctx.mark("candles", PLOT)
        EventHighlight.draw(ctx, hr, panel, fam, tx.get("callout"), t)
        hr.composite(ctx.layer)
        PriceTags.draw(ctx, panel, data, t)
        sup = data["support"]
        if sup["kind"] == "VOLUME":
            VolumePanel.draw(ctx, panel, sup, t)
        elif sup["kind"] == "DAY_RANGE":
            DayRangePanel.draw(ctx, sup, data, t)
        Takeaway.draw(ctx, tx["takeaway"], t)

    @staticmethod
    def _paint_no_event(ctx, t, tx, data):
        """VOLUME / SESSION: the same shell with no chart event - the candles are context
        (they step back once the evidence arrives), and nothing is ringed or called out."""
        volume = data["event_family"] == "VOLUME"
        plot, strip_top = (VOL_PLOT, VOL_STRIP[1]) if volume else (PLOT, STRIP[1])
        panel = CandlestickPanel(data, plot)
        hr = HiRes(CARD)
        for r in range(1, 4):
            yy = plot[1] + (plot[3] - plot[1]) * r / 4
            hr.line([(plot[0], yy), (plot[2], yy)], (255, 255, 255, 10), 1.2, caps=False)
        start = VolumeStoryPanel.T_BARS if volume else T_STRIP
        hr.line([(CARD[0] + 24, strip_top - 16), (CARD[2] - 24, strip_top - 16)],
                alpha(theme.PANEL_BORDER, phase(t, start, 0.3)), 1.4, caps=False)
        panel.draw(hr, t, phase(t, start, 0.5))
        ctx.mark("candles", plot)
        hr.composite(ctx.layer)
        PriceTags.draw(ctx, panel, data, t, strip_top=strip_top,
                       close_start=VolumeStoryPanel.T_CLOSE_TAG if volume else T_CLOSE)
        sup = data["support"]
        if volume:
            VolumeStoryPanel.draw(ctx, panel, sup, t)
        elif sup["kind"] == "DAY_RANGE":
            DayRangePanel.draw(ctx, sup, data, t)
        Takeaway.draw(ctx, tx["takeaway"], t)


__all__ = ["RadarStockScene", "CandlestickPanel", "RangeOverlay", "AverageOverlay",
           "EventHighlight", "PriceTags", "VolumePanel", "VolumeStoryPanel", "DayRangePanel",
           "Takeaway", "StoryIdentity", "CARD", "PLOT", "STRIP", "VOL_PLOT", "VOL_STRIP"]

"""Main-section scenes. Each draws ONLY its own content layer; header, progress, background and
disclaimer are drawn once per frame by the composer, identically for every scene.

Every scene follows the same three-level grammar: Level 1 headline (what happened) at the top,
Level 2 visual proof in the middle, Level 3 context below - revealed progressively, so the eye
always has one thing to look at.
"""
from __future__ import annotations

import math

from PIL import Image

from video import ease, heatmap_cell_colour

from . import theme
from .annotations import Ctx, callout, event_marker, gap_bracket, legend
from .chartkit import ChartArea, HiRes, draw_candles, draw_grid, reveal_points
from .typography import fit, font, tlen, wrap


def phase(t: float, start: float, dur: float) -> float:
    return ease((t - start) / dur) if dur > 0 else (1.0 if t >= start else 0.0)


def alpha(color, p):
    return (*color[:3], max(0, min(255, int(255 * p))))


def signed_color(positive):
    if positive is None:
        return theme.TEXT_PRIMARY
    return theme.POSITIVE if positive else theme.NEGATIVE


def new_layer():
    return Image.new("RGBA", (theme.CANVAS_W, theme.CANVAS_H), (0, 0, 0, 0))


def headline(ctx: Ctx, text: str, y: float, p: float, size=theme.T_HEADLINE, color=None,
             max_w=theme.CONTENT_W, max_lines=2, x=theme.X0, role="headline") -> float:
    """Level-1 message: slides up 18px and fades in. Returns the y below it."""
    if not text:
        return y
    f = fit(text, max_w * max_lines, size, min_size=theme.MIN_FONT + 10)
    lines = wrap(text, f, max_w)[:max_lines]
    dy = (1 - p) * 18
    for line in lines:
        ctx.ink.text(ctx.d, (x, y + dy), line, f, alpha(color or theme.TEXT_PRIMARY, p), role)
        y += int(f.size * 1.18)
    return y


def subline(ctx, text, y, p, size=theme.T_BODY - 4, color=theme.TEXT_SECONDARY, x=theme.X0,
            max_w=theme.CONTENT_W):
    if not text:
        return y
    f = fit(text, max_w, size, bold=False, min_size=theme.MIN_FONT)
    ctx.ink.text(ctx.d, (x, y + (1 - p) * 10), text, f, alpha(color, p), "subline")
    return y + int(f.size * 1.35)


def card(ctx, box, p=1.0, fill=theme.SURFACE, accent=None, radius=theme.RADIUS_CARD,
         border=True):
    hr = HiRes(box)
    hr.rect(box, fill=alpha(fill, 0.96 * p), radius=radius,
            outline=alpha(theme.PANEL_BORDER, 0.9 * p) if border else None, width=1.6)
    if accent is not None:
        hr.rect((box[0], box[1] + 18, box[0] + 7, box[3] - 18), fill=alpha(accent, p), radius=3.5)
    hr.composite(ctx.layer)


def chip(ctx, x, y, text, bg, fg=theme.INK, size=theme.T_CHIP, p=1.0, role="chip"):
    f = font(size, True)
    w, h = tlen(text, f) + 30, size + 18
    hr = HiRes((x, y, x + w, y + h))
    hr.rect((x, y, x + w, y + h), fill=alpha(bg, p), radius=h / 2)
    hr.composite(ctx.layer)
    ctx.ink.text(ctx.d, (x + 15, y + 8), text, f, alpha(fg, p), role)
    return x + w


def triangle(hr, cx, cy, s, up, color):
    if up:
        hr.polygon([(cx - s, cy + s * 0.8), (cx + s, cy + s * 0.8), (cx, cy - s * 0.9)], color)
    else:
        hr.polygon([(cx - s, cy - s * 0.8), (cx + s, cy - s * 0.8), (cx, cy + s * 0.9)], color)


class Scene:
    def __init__(self, spec):
        self.spec = spec
        self.duration = spec.duration

    def draw(self, t: float, recorder=None) -> Image.Image:
        layer = new_layer()
        ctx = Ctx(layer, recorder)
        self.paint(ctx, t)
        ctx.flush()
        prov = (getattr(self.spec, "texts", None) or {}).get("provenance")
        if isinstance(prov, dict):
            # SOURCE / DATA AS OF: one shared component, on the scene's own layer
            from .provenance_bar import draw_provenance
            draw_provenance(ctx, prov, phase(t, 0.25, 0.35))
        return layer

    def paint(self, ctx, t):
        raise NotImplementedError


# --------------------------------------------------------------------------- HOOK
class HookScene(Scene):
    def paint(self, ctx, t):
        s = self.spec
        tx = s.texts
        cx = theme.CANVAS_W / 2
        k = phase(t, 0.0, 0.4)
        f = font(theme.T_LABEL)
        ctx.ink.centered(ctx.d, cx, 372, tx["kicker"], f, alpha(theme.BRAND, k), "kicker")

        pv = phase(t, 0.15, 0.55)
        col = signed_color(s.data.get("positive"))
        glow = HiRes((cx - 330, 290, cx + 330, 950))
        for r, a in ((300, 0.045), (230, 0.05), (160, 0.06)):
            glow.circle(cx, 620, r * (0.75 + 0.25 * pv), fill=alpha(col, a * pv))
        glow.composite(ctx.layer)
        fl = font(theme.T_TITLE)
        ctx.ink.centered(ctx.d, cx, 462, tx["label"], fl, alpha(theme.TEXT_SECONDARY, pv), "label")
        size = int(theme.T_DISPLAY * (0.86 + 0.14 * pv))
        fv = fit(tx["value"], 960, size, min_size=90)
        ctx.ink.centered(ctx.d, cx, 522 + (1 - pv) * 20, tx["value"], fv, alpha(col, pv), "hero")
        fs = font(theme.T_BODY, False)
        ctx.ink.centered(ctx.d, cx, 716, s.subline, fs, alpha(theme.TEXT_PRIMARY, pv), "subline")

        pa = phase(t, 0.9, 0.5)
        hr = HiRes((cx - 110, 798, cx + 110, 806))
        hr.rect((cx - 100 * pa, 800, cx + 100 * pa, 804), fill=alpha(theme.BRAND, pa), radius=2)
        hr.composite(ctx.layer)
        self._agenda(ctx, tx["agenda"].split(" • "), t)

        if tx.get("teaser"):
            pt = phase(t, 2.2, 0.6)
            if pt > 0:
                box = (90, 1090 + (1 - pt) * 30, theme.RIGHT_RAIL_X, 1224 + (1 - pt) * 30)
                card(ctx, box, pt, fill=theme.SURFACE_RAISED, accent=theme.BRAND)
                self._radar_glyph(ctx, box[0] + 70, (box[1] + box[3]) / 2, t, pt)
                ft = fit(tx["teaser"], box[2] - box[0] - 150, theme.T_BODY, min_size=28)
                lines = wrap(tx["teaser"], ft, box[2] - box[0] - 150)[:2]
                y = (box[1] + box[3]) / 2 - len(lines) * ft.size * 0.62
                for line in lines:
                    ctx.ink.text(ctx.d, (box[0] + 130, y), line, ft, alpha(theme.TEXT_PRIMARY, pt),
                                 "teaser")
                    y += int(ft.size * 1.22)

    def _agenda(self, ctx, names, t):
        f = font(theme.T_LABEL)
        widths = [tlen(n, f) + 36 for n in names]
        gap = 14
        rows, cur, w = [], [], 0
        for n, wd in zip(names, widths):
            if cur and w + wd + gap > 900:
                rows.append(cur)
                cur, w = [], 0
            cur.append((n, wd))
            w += wd + gap
        if cur:
            rows.append(cur)
        y = 850
        idx = 0
        for row in rows:
            total = sum(wd for _, wd in row) + gap * (len(row) - 1)
            x = (theme.CANVAS_W - total) / 2
            for n, wd in row:
                p = phase(t, 1.0 + idx * 0.12, 0.35)
                if p > 0:
                    h = theme.T_LABEL + 22
                    hr = HiRes((x, y, x + wd, y + h))
                    hr.rect((x, y, x + wd, y + h), fill=alpha(theme.SURFACE, p), radius=h / 2,
                            outline=alpha(theme.PANEL_BORDER, p), width=1.5)
                    hr.composite(ctx.layer)
                    ctx.ink.text(ctx.d, (x + 18, y + 10), n, f, alpha(theme.TEXT_PRIMARY, p), "agenda")
                x += wd + gap
                idx += 1
            y += theme.T_LABEL + 38

    def _radar_glyph(self, ctx, cx, cy, t, p):
        hr = HiRes((cx - 44, cy - 44, cx + 44, cy + 44))
        for r in (38, 24, 10):
            hr.circle(cx, cy, r, outline=alpha(theme.BRAND, 0.6 * p), width=2.0)
        a = t * 2.2
        hr.line([(cx, cy), (cx + 38 * math.cos(a), cy + 38 * math.sin(a))], alpha(theme.BRAND, p), 3.0)
        hr.circle(cx + 20, cy - 14, 5, fill=alpha(theme.POSITIVE, p))
        hr.composite(ctx.layer)


# --------------------------------------------------------------------------- MARKET PULSE
class PulseScene(Scene):
    def paint(self, ctx, t):
        s, tx, dt_ = self.spec, self.spec.texts, self.spec.data
        ph = phase(t, 0.0, 0.5)
        headline(ctx, s.headline, 300, ph)
        pv = phase(t, 0.2, 0.5)
        col = signed_color(dt_["positive"])
        fv = font(theme.T_HERO)
        ctx.ink.text(ctx.d, (theme.X0, 390 + (1 - pv) * 16), tx["value"], fv,
                     alpha(theme.TEXT_PRIMARY, pv), "hero")
        fc = font(theme.T_TITLE)
        ctx.ink.text(ctx.d, (theme.X0 + 4, 520), tx["change"], fc, alpha(col, pv), "value")

        if "open" in dt_:
            self._range(ctx, t, (theme.X0, 620, theme.X1, 1060))
        tiles = tx.get("tiles") or []
        if tiles:
            y0, y1 = 1100, 1290
            gap = 24
            right = theme.RIGHT_RAIL_X
            w = (right - theme.X0 - gap * (len(tiles) - 1)) / len(tiles)
            for i, tile in enumerate(tiles):
                p = phase(t, 3.2 + i * 0.2, 0.45)
                if p <= 0:
                    continue
                x0 = theme.X0 + i * (w + gap)
                box = (x0, y0 + (1 - p) * 24, x0 + w, y1 + (1 - p) * 24)
                card(ctx, box, p, accent=signed_color(tile["positive"]))
                fl = font(theme.T_LABEL)
                ctx.ink.text(ctx.d, (box[0] + 34, box[1] + 30), tile["label"], fl,
                             alpha(theme.TEXT_SECONDARY, p), "label")
                fv2 = font(64)
                ctx.ink.text(ctx.d, (box[0] + 34, box[1] + 82), tile["value"], fv2,
                             alpha(signed_color(tile["positive"]), p), "value")

    def _range(self, ctx, t, box):
        tx, dt_ = self.spec.texts, self.spec.data
        pc = phase(t, 0.5, 0.4)
        card(ctx, box, pc, fill=theme.PANEL)
        fl = font(theme.T_LABEL)
        ctx.ink.text(ctx.d, (box[0] + 40, box[1] + 34), tx["range_title"], fl,
                     alpha(theme.TEXT_SECONDARY, pc), "label")
        lo, hi = dt_["low"], dt_["high"]
        span = max(hi - lo, 1e-9)
        bx0, bx1 = box[0] + 70, box[2] - 70
        by = box[1] + 250
        xv = lambda v: bx0 + (v - lo) / span * (bx1 - bx0)
        hr = HiRes(box)
        pt = phase(t, 0.7, 0.7)
        hr.rect((bx0, by - 9, bx0 + (bx1 - bx0) * pt, by + 9), fill=(40, 54, 96, 255), radius=9)
        for x in (bx0, bx1):
            if pt > 0.9:
                hr.rect((x - 2, by - 22, x + 2, by + 22), fill=alpha(theme.TEXT_SECONDARY, pt), radius=2)
        xo, xc = xv(dt_["open"]), xv(dt_["close"])
        po = phase(t, 1.4, 0.4)
        move = phase(t, 1.8, 1.0)
        up = dt_["close"] >= dt_["open"]
        mcol = theme.POSITIVE if up else theme.NEGATIVE
        if move > 0:
            xe = xo + (xc - xo) * move
            a, b = sorted((xo, xe))
            hr.rect((a, by - 9, b, by + 9), fill=alpha(mcol, 1.0), radius=9)
        if po > 0:
            hr.circle(xo, by, 13 * po, fill=theme.PANEL + (255,), outline=alpha(theme.TEXT_PRIMARY, po),
                      width=3.5)
        hr.composite(ctx.layer)
        pk = phase(t, 2.7, 0.4)
        if pk > 0:
            hr2 = HiRes((box[0], by - 50, box[2], by + 50))
            event_marker(ctx, hr2, xc, by, mcol, t, pk, kind="close_marker")
            hr2.composite(ctx.layer)
            fcl = font(theme.T_TITLE)
            w = tlen(tx["close_label"], fcl)
            lx = min(max(xc - w / 2, box[0] + 30), box[2] - 30 - w)
            ctx.ink.text(ctx.d, (lx, by - 100 + (1 - pk) * 10), tx["close_label"], fcl,
                         alpha(theme.TEXT_PRIMARY, pk), "annotation")
        if po > 0:
            fo = font(theme.T_LABEL)
            w = tlen(tx["open_label"], fo)
            lx = min(max(xo - w / 2, box[0] + 30), box[2] - 30 - w)
            ctx.ink.text(ctx.d, (lx, by + 32), tx["open_label"], fo, alpha(theme.TEXT_PRIMARY, po),
                         "annotation")
        if pt > 0.9:
            fe = font(theme.T_SMALL, False)
            ctx.ink.text(ctx.d, (bx0 - 10, by + 86), tx["low_label"], fe, theme.TEXT_SECONDARY, "label")
            ctx.ink.right(ctx.d, bx1 + 10, by + 86, tx["high_label"], fe, theme.TEXT_SECONDARY, "label")


# --------------------------------------------------------------------------- NIFTY chart
class NiftyChartScene(Scene):
    def paint(self, ctx, t):
        s, tx, dt_ = self.spec, self.spec.texts, self.spec.data
        y = headline(ctx, s.headline, 300, phase(t, 0.0, 0.5))
        subline(ctx, tx["sub"], y + 4, phase(t, 0.2, 0.5), size=theme.T_LABEL + 2)
        box = (theme.X0, 470, theme.X1, 1290)
        card(ctx, box, phase(t, 0.3, 0.4), fill=theme.PANEL)
        area_box = (box[0] + 40, box[1] + 70, box[2] - 120, box[3] - 90)
        closes = dt_["close"]
        n = len(closes)
        vals = dt_["high"] + dt_["low"] + (dt_["ema20"] or [])
        area = ChartArea.fit(area_box, vals, n, pad=0.06)
        hr = HiRes(box)
        draw_grid(hr, area)
        draw_candles(hr, area, dt_["open"], dt_["high"], dt_["low"], closes, phase(t, 0.6, 2.4))
        pe = phase(t, 2.8, 1.2)
        if dt_["ema20"] and pe > 0:
            pts = reveal_points(area, dt_["ema20"], pe)
            hr.line(pts, alpha(theme.BRAND, 0.35), 10.0)
            hr.line(pts, alpha(theme.BRAND, 1.0), 4.0)
        xl, yc = area.px(n - 1), area.py(closes[-1])
        pm = phase(t, 4.6, 0.6)
        event_marker(ctx, hr, xl, yc, signed_color(dt_["positive"]), t, pm, kind="close_marker")
        if dt_["ema20"]:
            ye = area.py(dt_["ema20"][-1])
            gap_bracket(ctx, hr, xl + 34, yc, ye, theme.NEGATIVE if yc > ye else theme.POSITIVE,
                        phase(t, 5.2, 0.6), kind="ema_gap")
        callout(ctx, hr, (xl, yc), tx["close_label"], signed_color(dt_["positive"]),
                (box[0] + 20, box[1] + 20, box[2] - 20, box[3] - 60), side="down-left", p=pm,
                offset=(40, 60))
        hr.composite(ctx.layer)
        fd = font(theme.T_SMALL, False)
        py = box[3] - 62
        ctx.ink.text(ctx.d, (area.x0, py), tx["first_date"], fd, theme.TEXT_MUTED, "axis")
        ctx.ink.right(ctx.d, area.x1, py, tx["last_date"], fd, theme.TEXT_MUTED, "axis")
        if tx.get("ema_label"):
            legend(ctx, [("line", theme.BRAND, tx["ema_label"])], (area.x0 + area.x1) / 2, py,
                   phase(t, 4.0, 0.5))


# --------------------------------------------------------------------------- diverging bars
def diverging_rows(ctx, t, rows, top, bottom, start=0.5, x0=theme.X0, x1=theme.X1,
                   key="numeric"):
    """Rows of `{name, value, tag, positive, numeric}` as bars growing from a shared zero line:
    right = positive, left = negative. Used by SECTORS and FLOWS alike."""
    n = len(rows)
    if n == 0:
        return
    gap = 22
    h = min(420, (bottom - top - gap * (n - 1)) / n)
    maxabs = max(abs(r.get(key) or 0) for r in rows) or 1.0
    zx = (x0 + x1) / 2
    reach = (x1 - x0) / 2 - 50
    big = h >= 240
    for i, r in enumerate(rows):
        p = phase(t, start + i * 0.25, 0.5)
        if p <= 0:
            continue
        y0 = top + i * (h + gap)
        box = (x0, y0 + (1 - p) * 20, x1, y0 + h + (1 - p) * 20)
        col = signed_color(r.get("positive"))
        card(ctx, box, p)
        tx = box[0] + 36
        ty = box[1] + (30 if big else 18)
        if r.get("tag"):
            tx = chip(ctx, tx, ty, r["tag"], col, p=p, size=theme.T_SMALL) + 16
        fn = fit(r["name"], (x1 - x0) * 0.5, 50 if big else 38, min_size=28)
        ctx.ink.text(ctx.d, (tx, ty - (6 if r.get("tag") else 0)), r["name"], fn,
                     alpha(theme.TEXT_PRIMARY, p), "name")
        fv = font(84 if big else 48)
        ctx.ink.right(ctx.d, box[2] - 36, box[1] + (24 if big else 10), r["value"], fv,
                      alpha(col, p), "value")
        bar_y = box[3] - (h * 0.30 if big else h * 0.34)
        bh = 40 if big else 22
        grow = phase(t, start + 0.3 + i * 0.25, 0.8)
        frac = abs(r.get(key) or 0) / maxabs
        length = max(6.0, reach * frac) * grow
        hr = HiRes((x0, bar_y - bh, x1, bar_y + bh))
        hr.rect((x0 + 36, bar_y - 2, x1 - 36, bar_y + 2), fill=alpha(theme.PANEL_BORDER, p))
        if (r.get(key) or 0) >= 0:
            hr.rect((zx, bar_y - bh / 2, zx + length, bar_y + bh / 2), fill=alpha(col, p), radius=bh / 3)
        else:
            hr.rect((zx - length, bar_y - bh / 2, zx, bar_y + bh / 2), fill=alpha(col, p), radius=bh / 3)
        hr.rect((zx - 2, bar_y - bh / 2 - 12, zx + 2, bar_y + bh / 2 + 12),
                fill=alpha(theme.TEXT_SECONDARY, p))
        hr.composite(ctx.layer)
        ctx.mark("bar", (min(zx, zx - length), bar_y - bh / 2, max(zx, zx + length), bar_y + bh / 2))


class SectorsScene(Scene):
    """Every sector in the report as a heatmap: tiles ranked strongest -> weakest, tinted by
    the size of the move (the product's own `video.heatmap_cell_colour` scale), with the
    leader and the laggard ringed. Sign is always in the text too, never colour alone."""
    TOP, BOTTOM, GAP = 480, 1380, 20

    def paint(self, ctx, t):
        s = self.spec
        y = headline(ctx, s.headline, 300, phase(t, 0.0, 0.5))
        subline(ctx, s.subline, y + 4, phase(t, 0.2, 0.5))
        rows = s.texts["rows"]
        for i, (box, r) in enumerate(zip(self.layout(len(rows)), rows)):
            self._tile(ctx, t, i, box, r)

    @classmethod
    def layout(cls, n):
        """Tile boxes in rank order. 1 column for 1, 2 columns up to 6, 3 beyond; when the
        count leaves one tile over, the LEADER takes the whole first row."""
        if n == 0:
            return []
        cols = 1 if n == 1 else (2 if n <= 6 else 3)
        span_first = n > 1 and n % cols == 1
        rest = n - (1 if span_first else 0)
        rows = (1 if span_first else 0) + -(-rest // cols)
        h = min(420, (cls.BOTTOM - cls.TOP - cls.GAP * (rows - 1)) / rows)
        w = (theme.CONTENT_W - cls.GAP * (cols - 1)) / cols
        boxes, y, idx = [], cls.TOP, 0
        if span_first:
            boxes.append((theme.X0, y, theme.X1, y + h))
            y += h + cls.GAP
            idx = 1
        while idx < n:
            for c in range(cols):
                if idx >= n:
                    break
                x0 = theme.X0 + c * (w + cls.GAP)
                boxes.append((x0, y, x0 + w, y + h))
                idx += 1
            y += h + cls.GAP
        return boxes

    def _tile(self, ctx, t, i, box, r):
        p = phase(t, 0.5 + i * 0.12, 0.45)
        if p <= 0:
            return
        x0, y0, x1, y1 = box
        k = 0.94 + 0.06 * p
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        bx = (cx - (x1 - x0) * k / 2, cy - (y1 - y0) * k / 2, cx + (x1 - x0) * k / 2,
              cy + (y1 - y0) * k / 2)
        col = signed_color(r["positive"])
        hr = HiRes((x0 - 8, y0 - 8, x1 + 8, y1 + 8))
        hr.rect(bx, fill=alpha(heatmap_cell_colour(r["numeric"]), p), radius=24,
                outline=alpha(theme.PANEL_BORDER, 0.8 * p), width=1.4)
        if r.get("tag"):
            pr = phase(t, 1.8, 0.5)
            if pr > 0:
                hr.rect((bx[0] - 4, bx[1] - 4, bx[2] + 4, bx[3] + 4), radius=28,
                        outline=alpha(col, pr), width=3 + 1.5 * math.sin(t * 4))
        hr.composite(ctx.layer)
        h, pad = y1 - y0, 32
        # Text never reaches under the Shorts action rail, whichever column the tile is in.
        right = bx[2] - pad
        if bx[3] > theme.RIGHT_RAIL_Y:
            right = min(right, theme.RIGHT_RAIL_X + 20)
        ty = bx[1] + pad - 6
        if r.get("tag"):
            chip(ctx, bx[0] + pad, ty, r["tag"], col, p=p, size=theme.T_SMALL)
            ty += theme.T_SMALL + 32
        fn = fit(r["name"], right - bx[0] - pad, min(58, int(h * 0.2)), min_size=26)
        ctx.ink.text(ctx.d, (bx[0] + pad, ty), r["name"], fn, alpha(theme.TEXT_PRIMARY, p), "name")
        fv = fit(r["value"], right - bx[0] - pad - 50, min(100, int(h * 0.3)), min_size=30)
        vy = bx[3] - pad - fv.size - 4
        tri = HiRes((bx[0] + pad, vy, bx[0] + pad + 44, vy + fv.size + 10))
        triangle(tri, bx[0] + pad + 18, vy + fv.size * 0.62, fv.size * 0.24, r["positive"],
                 alpha(col, p))
        tri.composite(ctx.layer)
        ctx.ink.text(ctx.d, (bx[0] + pad + 46, vy), r["value"], fv, alpha(theme.TEXT_PRIMARY, p),
                     "value")
        ctx.mark("heat_tile", bx)


class FlowsScene(Scene):
    def paint(self, ctx, t):
        s = self.spec
        y = headline(ctx, s.headline, 300, phase(t, 0.0, 0.5))
        subline(ctx, s.subline, y + 4, phase(t, 0.2, 0.5))
        diverging_rows(ctx, t, s.texts["bars"], 480, 1200)


# --------------------------------------------------------------------------- MOVERS
class MoversScene(Scene):
    COLS = ((theme.X0, 500), (525, 960))

    def paint(self, ctx, t):
        s, tx = self.spec, self.spec.texts
        y = headline(ctx, s.headline, 300, phase(t, 0.0, 0.5))
        subline(ctx, s.subline, y + 4, phase(t, 0.2, 0.5))
        allv = [abs(r["numeric"] or 0) for r in tx["gainers"] + tx["losers"]] or [1.0]
        maxabs = max(allv) or 1.0
        for ci, (key, title, up) in enumerate((("gainers", tx["gainers_title"], True),
                                               ("losers", tx["losers_title"], False))):
            x0, x1 = self.COLS[ci]
            col = theme.POSITIVE if up else theme.NEGATIVE
            p = phase(t, 0.4 + ci * 0.15, 0.4)
            hr = HiRes((x0, 470, x0 + 40, 510))
            triangle(hr, x0 + 14, 490, 12, up, alpha(col, p))
            hr.composite(ctx.layer)
            ctx.ink.text(ctx.d, (x0 + 38, 474), title, font(theme.T_LABEL + 2), alpha(col, p), "label")
            ry = 530
            for i, r in enumerate(tx[key]):
                hero = i == 0
                h = 150 if hero else 104
                pr = phase(t, 0.7 + ci * 0.15 + i * 0.14, 0.45)
                if pr > 0:
                    self._row(ctx, r, (x0, ry, x1, ry + h), col, hero, pr,
                              abs(r["numeric"] or 0) / maxabs, phase(t, 1.0 + i * 0.14, 0.8))
                ry += h + 14

    def _row(self, ctx, r, box, col, hero, p, frac, grow):
        dx = -(1 - p) * 40
        box = (box[0] + dx, box[1], box[2] + dx, box[3])
        card(ctx, box, p, fill=theme.SURFACE_RAISED if hero else theme.SURFACE,
             accent=col if hero else None, radius=22)
        rk = str(r["rank"])
        fr = font(26)
        cx, cy = box[0] + (48 if hero else 42), (box[1] + box[3]) / 2 - (12 if hero else 8)
        hr = HiRes((cx - 22, cy - 22, cx + 22, cy + 22))
        hr.circle(cx, cy, 20, fill=alpha(col if hero else theme.PANEL_BORDER, p))
        hr.composite(ctx.layer)
        ctx.ink.text(ctx.d, (cx - tlen(rk, fr) / 2, cy - 15), rk, fr,
                     alpha(theme.INK if hero else theme.TEXT_PRIMARY, p), "rank")
        fv = font(52 if hero else 36)
        vw = tlen(r["value"], fv)
        ctx.ink.text(ctx.d, (box[2] - 24 - vw, cy - fv.size * 0.6), r["value"], fv, alpha(col, p), "value")
        nx = cx + 36
        fn = fit(r["name"], box[2] - 40 - vw - nx, 44 if hero else 32, min_size=24)
        ctx.ink.text(ctx.d, (nx, cy - fn.size * 0.6), r["name"], fn, alpha(theme.TEXT_PRIMARY, p), "name")
        by = box[3] - (22 if hero else 16)
        bw = (box[2] - box[0] - 70) * frac * grow
        hr2 = HiRes((box[0], by - 6, box[2], by + 6))
        hr2.rect((box[0] + 30, by - 3, box[2] - 30, by + 3), fill=alpha(theme.PANEL_BORDER, p), radius=3)
        if bw > 0:
            hr2.rect((box[0] + 30, by - 3, box[0] + 30 + bw, by + 3), fill=alpha(col, p), radius=3)
        hr2.composite(ctx.layer)


# --------------------------------------------------------------------------- LOOK AHEAD
class AheadScene(Scene):
    def paint(self, ctx, t):
        s, tx = self.spec, self.spec.texts
        y = headline(ctx, s.headline, 300, phase(t, 0.0, 0.5))
        subline(ctx, s.subline, y + 4, phase(t, 0.2, 0.5))
        tiles = tx.get("tiles") or []
        cols = 2 if len(tiles) > 1 else 1
        gap = 22
        w = (theme.CONTENT_W - gap * (cols - 1)) / cols
        th = 200
        for i, tile in enumerate(tiles[:4]):
            r, c = divmod(i, cols)
            p = phase(t, 0.5 + i * 0.15, 0.45)
            if p <= 0:
                continue
            x0 = theme.X0 + c * (w + gap)
            y0 = 480 + r * (th + gap) + (1 - p) * 20
            col = signed_color(tile["positive"])
            card(ctx, (x0, y0, x0 + w, y0 + th), p, accent=col)
            ctx.ink.text(ctx.d, (x0 + 36, y0 + 30), tile["name"], font(theme.T_LABEL),
                         alpha(theme.TEXT_SECONDARY, p), "label")
            hr = HiRes((x0 + 30, y0 + 100, x0 + 80, y0 + 160))
            if tile["positive"] is not None:
                triangle(hr, x0 + 52, y0 + 130, 15, tile["positive"], alpha(col, p))
            hr.composite(ctx.layer)
            ctx.ink.text(ctx.d, (x0 + 84, y0 + 96), tile["value"], font(62), alpha(col, p), "value")
        watch = tx.get("watch") or []
        if watch:
            p = phase(t, 1.6, 0.5)
            if p > 0:
                rows = math.ceil(min(len(tiles), 4) / cols) if tiles else 0
                y0 = 480 + rows * (th + gap) + 30
                box = (theme.X0, y0 + (1 - p) * 20, theme.RIGHT_RAIL_X, y0 + 230 + (1 - p) * 20)
                card(ctx, box, p, fill=theme.SURFACE_RAISED, accent=theme.BRAND)
                ctx.ink.text(ctx.d, (box[0] + 36, box[1] + 26), tx["watch_title"], font(theme.T_SMALL),
                             alpha(theme.TEXT_SECONDARY, p), "label")
                item = watch[0]
                x = box[0] + 36
                if item.get("tag"):
                    x = chip(ctx, x, box[1] + 70, item["tag"], theme.BRAND, p=p, size=theme.T_SMALL) + 18
                fw = fit(item["text"], (box[2] - x - 36) * 2, theme.T_BODY, min_size=28)
                ly = box[1] + 66
                for line in wrap(item["text"], fw, box[2] - x - 36)[:2]:
                    ctx.ink.text(ctx.d, (x, ly), line, fw, alpha(theme.TEXT_PRIMARY, p), "watch")
                    ly += int(fw.size * 1.25)


# --------------------------------------------------------------------------- CLOSING
class ClosingScene(Scene):
    def paint(self, ctx, t):
        tx = self.spec.texts
        cx = theme.CANVAS_W / 2
        p = phase(t, 0.0, 0.6)
        f1 = font(92)
        from .chrome import BRAND_PART1, BRAND_PART2
        ctx.ink.centered(ctx.d, cx, 560 + (1 - p) * 20, BRAND_PART1.strip(), f1,
                         alpha(theme.TEXT_PRIMARY, p), "lockup")
        ctx.ink.centered(ctx.d, cx, 668 + (1 - p) * 20, BRAND_PART2, font(120),
                         alpha(theme.BRAND, p), "lockup")
        pc = phase(t, 0.5, 0.5)
        if tx.get("cadence"):
            ctx.ink.centered(ctx.d, cx, 830, tx["cadence"], font(theme.T_BODY, False),
                             alpha(theme.TEXT_SECONDARY, pc), "subline")
        pb = phase(t, 0.9, 0.5)
        if pb > 0 and tx.get("cta"):
            f = font(theme.T_TITLE)
            w = tlen(tx["cta"], f) + 90
            pulse = 1 + 0.03 * math.sin(t * 5)
            bw, bh = w * pulse, 92 * pulse
            box = (cx - bw / 2, 920, cx + bw / 2, 920 + bh)
            hr = HiRes(box)
            hr.rect(box, fill=alpha((230, 33, 23), pb), radius=bh / 2)
            hr.composite(ctx.layer)
            ctx.ink.centered(ctx.d, cx, 920 + bh / 2 - f.size * 0.6, tx["cta"], f,
                             alpha((255, 255, 255), pb), "cta")
        pn = phase(t, 1.3, 0.5)
        if tx.get("note"):
            fn = font(theme.T_LABEL, False)
            y = 1120
            for line in wrap(tx["note"], fn, 880)[:2]:
                ctx.ink.centered(ctx.d, cx, y, line, fn, alpha(theme.TEXT_SECONDARY, pn), "note")
                y += 38


__all__ = ["Scene", "HookScene", "PulseScene", "NiftyChartScene", "SectorsScene", "FlowsScene",
           "MoversScene", "AheadScene", "ClosingScene", "phase", "headline", "subline", "card",
           "chip", "alpha", "signed_color", "triangle", "diverging_rows"]

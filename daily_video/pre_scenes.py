"""PRE-MARKET scenes (V1): the morning sibling of the POST middle section.

    PreOvernightScene   one dominant overnight cue (night card, as in the OVERNIGHT hook hero),
                        at most two supporting cues, and a timestamped GIFT Nifty strip
    PreVixScene         India VIX: the previous session's close vs the session before
    PreEventScene       one verified scheduled event: taped paper calendar + event card
    PreStockWatchScene  1-2 previous-session Radar stories carried forward
    PreWatchScene       WATCH AT THE OPEN: 2-3 numbered attention cues

The previous-session setup reuses POST's MarketPulseScene / MarketStructureScene, sector
context SectorBoardScene, flows FlowsScene and the close QuickCloseScene - one design system.
Every string drawn is declared in the scene's `texts`; every number was validated and
formatted upstream (`presentation.pre_plan`). Nothing here decides whether a scene appears.
"""
from __future__ import annotations

import math

from video import ease

from . import hook_kit as K
from . import theme
from .chartkit import HiRes
from .hook_scene import _mini_calendar, _moon
from .scenes import Scene, alpha, card, chip, headline, phase, signed_color, subline, triangle
from .typography import fit, font, tlen, wrap

TEXT_RIGHT_LOW = theme.RIGHT_RAIL_X - 12     # text right edge anywhere near the action rail


def _underline(ctx, box, p, color, width=5.0):
    if p <= 0 or not box:
        return
    hr = HiRes((box[0] - 16, box[3] - 4, box[2] + 16, box[3] + 30))
    K.marker_underline(hr, box[0] - 4, box[2] + 4, box[3] + 12, p, color=color, width=width)
    hr.composite(ctx.layer)


def _top(ctx, t, tx):
    y = headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
    return subline(ctx, tx.get("subline", ""), y + 4, phase(t, 0.2, 0.4))


# --------------------------------------------------------------------------- overnight
class PreOvernightScene(Scene):
    LEAD = (theme.X0, 488, theme.X1, 810)
    ROW = (838, 1010)
    GIFT = (theme.X0, 1040, theme.RIGHT_RAIL_X, 1188)

    def paint(self, ctx, t):
        tx, data = self.spec.texts, self.spec.data
        _top(ctx, t, {"headline": tx["headline"], "subline": tx.get("takeaway", "")})
        cues = data.get("cues") or []
        if cues:
            self._lead(ctx, t, cues[0], tx["cues"][0])
            if len(cues) > 1:
                self._row(ctx, t, cues[1:], tx["cues"][1:])
        if data.get("gift"):
            box = self.GIFT if cues else (theme.X0, 488, theme.RIGHT_RAIL_X, 700)
            self._gift(ctx, t, tx["gift"], data["gift"], box, 1.3 if cues else 0.4)

    def _lead(self, ctx, t, c, ct):
        k = min(K.snap(t, 0.35, 0.32), 1.0)
        if k <= 0:
            return
        x0, y0, x1, y1 = self.LEAD
        dy = (1 - k) * 24
        hr = HiRes((x0 - 6, y0 - 6, x1 + 6, y1 + 30))
        hr.rect((x0, y0 + dy, x1, y1 + dy), fill=alpha(K.NIGHT, 0.97 * k), radius=theme.RADIUS_CARD,
                outline=alpha((52, 66, 120), k), width=1.8)
        for i in range(14):      # a few fixed stars - the night card of the OVERNIGHT hook hero
            sx = x0 + 60 + (i * 157) % (x1 - x0 - 120)
            sy = y0 + 24 + (i * 83) % 150 + dy
            hr.circle(sx, sy, 1.5 + (i % 3) * 0.6,
                      fill=alpha((200, 215, 255), k * (0.30 + 0.2 * math.sin(t * 2 + i))))
        hr.composite(ctx.layer)
        _moon(ctx, x0 + 58, y0 + 58 + dy, 18, K.NIGHT, k)
        ctx.ink.text(ctx.d, (x0 + 92, y0 + 42 + dy), ct["when"], font(theme.T_LABEL),
                     alpha(K.NEON_WARM, k), "cue_note")
        col = signed_color(c["positive"])
        fn = fit(ct["name"], x1 - x0 - 88, 58, min_size=32)
        ctx.ink.text(ctx.d, (x0 + 44, y0 + 98 + dy), ct["name"], fn, alpha(theme.TEXT_PRIMARY, k), "cue_name")
        fv = fit(ct["value"], x1 - x0 - 88, 130, min_size=60)
        vb = K.number_reveal(ctx, x0 + 40, y0 + 168 + dy, ct["value"], fv, col,
                             phase(t, 0.5, 0.35), "cue_value")
        _underline(ctx, vb, phase(t, 0.9, 0.4), col, width=6.0)
        ctx.mark("cue_card", self.LEAD)

    def _row(self, ctx, t, cues, texts):
        n = len(cues)
        gap = 22
        w = (theme.X1 - theme.X0 - gap * (n - 1)) / n
        for i, (c, ct) in enumerate(zip(cues, texts)):
            p = min(K.snap(t, 0.85 + i * 0.18, 0.3), 1.0)
            if p <= 0:
                continue
            x0 = theme.X0 + i * (w + gap)
            box = (x0, self.ROW[0] + (1 - p) * 20, x0 + w, self.ROW[1] + (1 - p) * 20)
            col = signed_color(c["positive"])
            card(ctx, box, p, fill=theme.PANEL, accent=col)
            ctx.ink.text(ctx.d, (box[0] + 32, box[1] + 22), ct["when"],
                         fit(ct["when"], w - 64, theme.T_SMALL, min_size=theme.MIN_FONT),
                         alpha(K.NEON_WARM, p), "cue_note")
            ctx.ink.text(ctx.d, (box[0] + 32, box[1] + 60), ct["name"],
                         fit(ct["name"], w - 64, 34, min_size=theme.MIN_FONT),
                         alpha(theme.TEXT_SECONDARY, p), "cue_name")
            h2 = HiRes((box[0] + 26, box[1] + 106, box[0] + 70, box[1] + 156))
            triangle(h2, box[0] + 46, box[1] + 132, 12, c["positive"], alpha(col, p))
            h2.composite(ctx.layer)
            ctx.ink.text(ctx.d, (box[0] + 74, box[1] + 104), ct["value"],
                         fit(ct["value"], w - 110, 52, min_size=30), alpha(col, p), "cue_value")
            ctx.mark("cue_card", box)

    def _gift(self, ctx, t, gt, g, box, start):
        p = min(K.snap(t, start, 0.32), 1.0)
        if p <= 0:
            return
        x0, y0, x1, y1 = box
        dx = (1 - p) * 60
        hr = HiRes((x0 - 4, y0 - 4, x1 + 64, y1 + 4))
        hr.rect((x0 + dx, y0, x1 + dx, y1), fill=alpha(K.PAPER, 0.98 * p), radius=20,
                outline=alpha(K.PAPER_EDGE, p), width=1.5)
        K.tape(hr, x0 + 90 + dx, y0 + 2, 110, 26, -4, p)
        hr.composite(ctx.layer)
        col = K.PAPER_PAL.signed(g["positive"])
        ctx.ink.text(ctx.d, (x0 + 34 + dx, y0 + 30), gt["name"], font(34), alpha(K.PAPER_INK, p), "cue_name")
        ctx.ink.text(ctx.d, (x0 + 34 + dx, y0 + 80), gt["time"], font(theme.T_SMALL),
                     alpha(K.PAPER_MUTED, p), "cue_note")
        ctx.ink.text(ctx.d, (x0 + 34 + dx, y0 + 112), gt["reference"], font(theme.T_SMALL, False),
                     alpha(K.PAPER_MUTED, p), "cue_note")
        fv = fit(gt["value"], 300, 80, min_size=40)
        ctx.ink.right(ctx.d, x1 - 36 + dx, y0 + (y1 - y0 - fv.size) / 2 - 6, gt["value"], fv,
                      alpha(col, p), "cue_value")
        ctx.mark("gift_strip", box)


# --------------------------------------------------------------------------- VIX
class PreVixScene(Scene):
    CARD = (theme.X0, 480, theme.X1, 1000)

    def paint(self, ctx, t):
        tx, data = self.spec.texts, self.spec.data
        _top(ctx, t, {"headline": tx["headline"], "subline": tx["note"]})
        k = min(K.snap(t, 0.35, 0.32), 1.0)
        if k <= 0:
            return
        x0, y0, x1, y1 = self.CARD
        card(ctx, self.CARD, k, fill=theme.PANEL, accent=theme.VOLUME)
        ctx.ink.text(ctx.d, (x0 + 44, y0 + 40), tx["label"], font(theme.T_LABEL),
                     alpha(theme.TEXT_SECONDARY, k), "label")
        fv = font(150)
        K.number_reveal(ctx, x0 + 40, y0 + 90, tx["value"], fv, theme.TEXT_PRIMARY,
                        phase(t, 0.5, 0.35), "value")
        pc = phase(t, 0.8, 0.3)
        cb = None
        if pc > 0:
            cb = ctx.ink.text(ctx.d, (x0 + 44, y0 + 290), tx["change"], font(84),
                              alpha(theme.VOLUME, pc), "value")
            ctx.ink.text(ctx.d, (x0 + 44, y0 + 400), tx["change_label"], font(theme.T_LABEL, False),
                         alpha(theme.TEXT_SECONDARY, pc), "label")
        _underline(ctx, cb, phase(t, 1.1, 0.4), theme.VOLUME, width=6.0)
        # the two readings to scale, from a shared zero baseline - the change you can see
        vals = [data["previous"], data["latest"]]
        top = max(vals) * 1.12 or 1.0
        bx0, base, htop = x1 - 330, y1 - 90, y0 + 110
        hr = HiRes((bx0 - 20, htop - 60, x1, base + 60))
        hr.line([(bx0 - 10, base), (x1 - 40, base)], alpha(theme.PANEL_BORDER, k), 2.0, caps=False)
        for i, (v, col) in enumerate(zip(vals, (theme.VOLUME_BASE, theme.VOLUME))):
            g = phase(t, 0.7 + 0.25 * i, 0.6)
            h = (base - htop) * v / top * g
            bx = bx0 + i * 150
            hr.rect((bx, base - h, bx + 110, base), fill=alpha(col, k), radius=10)
        hr.composite(ctx.layer)
        fl = font(theme.T_LABEL)
        for i, (lab, val) in enumerate(((tx["previous_label"], tx["previous_value"]),
                                        (tx["session_label"], tx["value"]))):
            bx = bx0 + i * 150 + 55
            ctx.ink.centered(ctx.d, bx, base + 14, lab, fl, alpha(theme.TEXT_SECONDARY, k), "label")
            if phase(t, 0.9 + 0.25 * i, 0.3) > 0:
                v = vals[i]
                ctx.ink.centered(ctx.d, bx, base - (base - htop) * v / top - 44, val, font(30),
                                 alpha(theme.TEXT_PRIMARY, k), "bar_value")
        ctx.mark("vix_bars", (bx0, htop, x1 - 40, base))


# --------------------------------------------------------------------------- event
class PreEventScene(Scene):
    CAL = (theme.X0, 480, theme.X0 + 300, 840)
    CARD = (theme.X0 + 330, 480, theme.X1, 900)

    def paint(self, ctx, t):
        tx = self.spec.texts
        _top(ctx, t, {"headline": tx["headline"], "subline": ""})
        # calendar page: the approved EVENT_CALENDAR paper slip
        k = min(K.snap(t, 0.3, 0.35), 1.0)
        if k > 0:
            cal = K.Slip(270, 330, style="paper")
            _mini_calendar(cal, (cal.x0 + 16, cal.y0 + 16, cal.x1 - 16, cal.y1 - 16), tx["day"],
                           tx["month"], tx["weekday"])
            chr_ = cal.hr()
            K.tape(chr_, cal.x0 + 135, cal.y0 + 2, 110, 28, -6, 1.0)
            chr_.composite(cal.img)
            cal.place(ctx, self.CAL[0] + 150, self.CAL[1] + 180 - (1 - k) * 50, -3.0 + 5 * (1 - k),
                      1.0, min(1.0, k * 1.5), mark="event_calendar")
        p = min(K.snap(t, 0.55, 0.32), 1.0)
        if p <= 0:
            return
        x0, y0, x1, y1 = self.CARD
        card(ctx, self.CARD, p, fill=theme.PANEL, accent=theme.BRAND)
        chip(ctx, x0 + 40, y0 + 40, tx["tag"], theme.BRAND, p=p, size=theme.T_SMALL)
        ft = fit(tx["title"], (x1 - x0 - 80) * 3, 54, min_size=32)
        y = y0 + 112
        for line in wrap(tx["title"], ft, x1 - x0 - 80)[:3]:
            ctx.ink.text(ctx.d, (x0 + 40, y), line, ft, alpha(theme.TEXT_PRIMARY, p), "event_title")
            y += int(ft.size * 1.18)
        fw = fit(tx["time"], x1 - x0 - 100, 40, min_size=theme.MIN_FONT)
        pw = phase(t, 0.95, 0.3)
        if pw > 0:
            wb = ctx.ink.text(ctx.d, (x0 + 40, y + 28), tx["time"], fw, alpha(K.NEON_WARM, pw), "event_when")
            hr = HiRes((x0, wb[1] - 40, x1, wb[3] + 40))
            K.neon_ring(hr, (wb[0] + wb[2]) / 2, (wb[1] + wb[3]) / 2, (wb[2] - wb[0]) / 2 + 26,
                        (wb[3] - wb[1]) / 2 + 20, phase(t, 1.15, 0.45), color=K.NEON_WARM, seed=9)
            hr.composite(ctx.layer)
        ctx.mark("event_card", self.CARD)
        ps = phase(t, 1.3, 0.3)
        if ps > 0:
            f = font(theme.T_SMALL, False)
            ctx.ink.text(ctx.d, (theme.X0, 940), tx["source"], f, alpha(theme.TEXT_SECONDARY, ps), "source")
            if tx.get("status"):
                ctx.ink.text(ctx.d, (theme.X0, 980), tx["status"], f, alpha(theme.TEXT_MUTED, ps), "source")


# --------------------------------------------------------------------------- stock watch
class PreStockWatchScene(Scene):
    TOP = 486

    def paint(self, ctx, t):
        tx, items = self.spec.texts, self.spec.data["items"]
        _top(ctx, t, {"headline": tx["headline"], "subline": tx["subline"]})
        n = len(items)
        gap = 26
        h = min(400 if n == 1 else 330, (1190 - self.TOP - gap * (n - 1)) / max(n, 1))
        tall = h >= 380
        for i, (it, st) in enumerate(zip(items, tx["items"])):
            k = min(K.snap(t, 0.4 + i * 0.3, 0.32), 1.0)
            if k <= 0:
                continue
            y0 = self.TOP + i * (h + gap) + (1 - k) * 22
            box = (theme.X0, y0, theme.X1, y0 + h)
            col = signed_color(it["positive"])
            card(ctx, box, k, fill=theme.PANEL, accent=col)
            right = TEXT_RIGHT_LOW if box[3] > theme.RIGHT_RAIL_Y else theme.X1 - 40
            ctx.ink.text(ctx.d, (box[0] + 40, box[1] + 28), st["symbol"],
                         fit(st["symbol"], 520, 60, min_size=34), alpha(theme.TEXT_PRIMARY, k), "symbol")
            fc = font(54)
            ctx.ink.right(ctx.d, right, box[1] + 30, st["change"], fc, alpha(col, k), "value")
            if st.get("day"):
                ctx.ink.right(ctx.d, right, box[1] + 96, st["day"], font(theme.T_SMALL),
                              alpha(theme.TEXT_SECONDARY, k), "day")
            closes = it.get("closes") or []
            if len(closes) >= 2:
                self._spark(ctx, t, closes, (box[0] + 40, box[1] + 134, right - 30,
                                             box[1] + (250 if tall else 190)), col, k, i)
            fl = fit(st["line"], (right - box[0] - 80) * 2, 34 if tall else 32, bold=False,
                     min_size=theme.MIN_FONT)
            yy = box[1] + ((280 if tall else 212) if closes else 118)
            for line in wrap(st["line"], fl, right - box[0] - 40)[:2]:
                ctx.ink.text(ctx.d, (box[0] + 40, yy), line, fl, alpha(theme.TEXT_PRIMARY, k), "line")
                yy += int(fl.size * 1.25)
            ctx.mark("stock_card", box)

    def _spark(self, ctx, t, closes, box, col, k, i):
        x0, y0, x1, y1 = box
        lo, hi = min(closes), max(closes)
        span = (hi - lo) or 1.0
        pts = [(x0 + (x1 - x0) * j / (len(closes) - 1), y1 - (c - lo) / span * (y1 - y0))
               for j, c in enumerate(closes)]
        frac = phase(t, 0.6 + 0.3 * i, 0.6)
        m = max(2, int(len(pts) * frac))
        hr = HiRes((x0 - 30, y0 - 30, x1 + 30, y1 + 30))
        hr.line(pts[:m], alpha(theme.BENCHMARK, k), 3.0)
        if frac >= 1:
            hr.circle(pts[-1][0], pts[-1][1], 9, fill=alpha(col, k))
            K.neon_ring(hr, pts[-1][0], pts[-1][1], 24, 20, phase(t, 1.2 + 0.3 * i, 0.4),
                        color=K.NEON, seed=11 + i)
        hr.composite(ctx.layer)
        ctx.mark("spark_line", box)


# --------------------------------------------------------------------------- watch at the open
class PreWatchScene(Scene):
    TOP = 480

    def paint(self, ctx, t):
        tx = self.spec.texts
        _top(ctx, t, {"headline": tx["headline"], "subline": tx["subline"]})
        items = tx["items"]
        n = len(items)
        gap = 24
        h = min(226, (1190 - self.TOP - gap * (n - 1)) / max(n, 1))
        for i, it in enumerate(items):
            k = min(K.snap(t, 0.45 + i * 0.4, 0.32), 1.0)
            if k <= 0:
                continue
            y0 = self.TOP + i * (h + gap)
            dx = (1 - k) * 40
            box = (theme.X0 + dx, y0, theme.X1 + dx, y0 + h)
            pos = self.spec.data["positive"][i]
            col = theme.BRAND if pos is None else signed_color(pos)
            card(ctx, box, k, fill=theme.PANEL, accent=col)
            right = TEXT_RIGHT_LOW if box[3] > theme.RIGHT_RAIL_Y else theme.X1 - 36
            cx, cy = box[0] + 64, box[1] + h / 2
            hr = HiRes((cx - 34, cy - 34, cx + 34, cy + 34))
            hr.circle(cx, cy, 30, fill=alpha(theme.SURFACE_RAISED, k),
                      outline=alpha(theme.BRAND, k), width=2.4)
            hr.composite(ctx.layer)
            fnum = font(34)
            num = str(i + 1)
            ctx.ink.text(ctx.d, (cx - tlen(num, fnum) / 2, cy - 21), num, fnum, alpha(theme.BRAND, k), "rank")
            tx0 = box[0] + 124
            chip(ctx, tx0, box[1] + 24, it["tag"], col if pos is not None else theme.BRAND, p=k,
                 size=theme.T_SMALL)
            ft = fit(it["title"], right - tx0, 46, min_size=30)
            ctx.ink.text(ctx.d, (tx0, box[1] + 80), it["title"], ft, alpha(theme.TEXT_PRIMARY, k), "title")
            fn = fit(it["note"], right - tx0, 30, bold=False, min_size=theme.MIN_FONT)
            ctx.ink.text(ctx.d, (tx0, box[1] + 88 + ft.size + 10), it["note"], fn,
                         alpha(theme.TEXT_SECONDARY, k), "note")
            ctx.mark("watch_card", box)


__all__ = ["PreOvernightScene", "PreVixScene", "PreEventScene", "PreStockWatchScene",
           "PreWatchScene"]

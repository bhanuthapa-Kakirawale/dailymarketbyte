"""POST middle-section scenes (Phase 3): clean financial storytelling, one idea per screen.

    MarketPulseScene     how Nifty finished: ONE primary fact (close + change) and ONE support
                         (where it closed in the day's range, drawn as the day's path)
    MarketStructureScene the optional Nifty chart story - the Radar chart shell (same grammar),
                         marked as MARKET by a brand-coloured edge instead of a story counter
    SectorBoardScene     leader dominant, laggard clearly visible, every sector in a ranked
                         heat strip underneath for context (all sectors, never trimmed)
    MoversDuelScene      optional: at most two single-stock moves, side by side
    GlobalContextScene   optional and rare in POST: same-day global moves, never "overnight"
    SpecialEventScene    optional and rare: one high-impact scheduled event
    QuickCloseScene      2.6 s sign-off: brand, one light CTA, the signals-not-advice line

Every string comes from the storyboard's declared `texts`; every number from the post plan's
validated models. Nothing here decides whether a section appears - `presentation.post_plan`
did that. Components (cards, neon underline, markers) are reusable by a future PRE plan.
"""
from __future__ import annotations

import math

from video import ease, heatmap_cell_colour

from . import hook_kit as K
from . import theme
from .annotations import event_marker
from .chartkit import HiRes
from .radar_story_scene import CARD as RADAR_CARD, RadarStockScene
from .scenes import Scene, alpha, card, chip, headline, phase, signed_color, subline, triangle
from .typography import fit, font, tlen, wrap


def _underline(ctx, box, p, color, width=5.0):
    if p <= 0 or not box:
        return
    hr = HiRes((box[0] - 16, box[3] - 4, box[2] + 16, box[3] + 30))
    K.marker_underline(hr, box[0] - 4, box[2] + 4, box[3] + 12, p, color=color, width=width)
    hr.composite(ctx.layer)


# --------------------------------------------------------------------------- market pulse
class MarketPulseScene(Scene):
    HERO = (theme.X0, 392, theme.X1, 846)
    SUPPORT = (theme.X0, 878, theme.X1, 1250)   # card may reach the rail; its text stays left of it

    def paint(self, ctx, t):
        tx, dt_ = self.spec.texts, self.spec.data
        headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        col = signed_color(dt_["positive"])
        # hero: the close and the change - the one primary fact
        pc = phase(t, 0.15, 0.35)
        card(ctx, self.HERO, pc, fill=theme.PANEL)
        x0, y0 = self.HERO[0] + 44, self.HERO[1]
        ctx.ink.text(ctx.d, (x0, y0 + 34), tx["label"], font(theme.T_LABEL), alpha(theme.TEXT_SECONDARY, pc),
                     "label")
        fv = fit(tx["value"], self.HERO[2] - self.HERO[0] - 88, 172, min_size=90)
        K.number_reveal(ctx, x0 - 4, y0 + 86, tx["value"], fv, theme.TEXT_PRIMARY,
                        phase(t, 0.3, 0.35), "hero")
        fc = font(90)
        pch = phase(t, 0.55, 0.3)
        cb = None
        if pch > 0:
            cb = ctx.ink.text(ctx.d, (x0, y0 + 300), tx["change"], fc, alpha(col, pch), "value")
            if tx.get("points"):
                fp = font(50, False)
                ctx.ink.text(ctx.d, (cb[2] + 34, y0 + 330), tx["points"], fp, alpha(col, 0.85 * pch),
                             "value")
        _underline(ctx, cb, phase(t, 0.85, 0.4), col, width=6.0)
        ctx.mark("close_marker", self.HERO)
        # support: the day's path from open to close inside its low-high range
        sup = dt_.get("support") or {}
        if sup.get("kind") != "SESSION_RANGE":
            return
        ps = phase(t, 1.15, 0.35)
        card(ctx, self.SUPPORT, ps, fill=theme.PANEL)
        loc = sup["location"]
        scol = (theme.POSITIVE if loc in ("HIGH", "MID_UP") else
                theme.NEGATIVE if loc in ("LOW", "MID_DOWN") else theme.TEXT_PRIMARY)
        f = font(46)
        ctx.ink.text(ctx.d, (self.SUPPORT[0] + 44, self.SUPPORT[1] + 40 + (1 - ps) * 8), tx["support"],
                     f, alpha(scol, ps), "support")
        self._range(ctx, t, sup, tx, scol)

    def _range(self, ctx, t, sup, tx, col):
        x0, x1 = self.SUPPORT[0] + 90, theme.RIGHT_RAIL_X - 60
        y = self.SUPPORT[1] + 236
        lo, hi = sup["low"], sup["high"]
        xv = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)       # drawing only: value -> x
        pt = phase(t, 1.4, 0.4)
        hr = HiRes((self.SUPPORT[0], y - 60, self.SUPPORT[2], y + 60))
        hr.rect((x0, y - 10, x0 + (x1 - x0) * pt, y + 10), fill=alpha(theme.PANEL_BORDER, 1.0), radius=10)
        if pt >= 1:
            for xe in (x0, x1):
                hr.rect((xe - 2, y - 20, xe + 2, y + 20), fill=alpha(theme.TEXT_SECONDARY, 1.0), radius=2)
        xo, xc = xv(sup["open"]), xv(sup["close"])
        po = phase(t, 1.75, 0.25)
        pp = phase(t, 1.95, 0.55)
        if pp > 0:
            xe = xo + (xc - xo) * pp
            a_, b_ = sorted((xo, xe))
            hr.rect((a_, y - 10, b_, y + 10), fill=alpha(col, 0.9), radius=10)
        if po > 0:
            hr.circle(xo, y, 13 * po, fill=theme.PANEL + (255,), outline=alpha(theme.TEXT_PRIMARY, po),
                      width=3.4)
        pm = phase(t, 2.45, 0.35)
        if pm > 0:
            event_marker(ctx, hr, xc, y, col, t, pm, kind="close_marker")
            K.neon_ring(hr, xc, y, 30, 28, pm, color=K.NEON, seed=21)
        hr.composite(ctx.layer)
        ctx.mark("session_range", (x0, y - 20, x1, y + 20))
        fs = font(theme.T_LABEL, False)
        if pt >= 1:
            ctx.ink.text(ctx.d, (x0 - tlen(tx["low_label"], fs) / 2, y + 38), tx["low_label"], fs,
                         theme.TEXT_SECONDARY, "label")
            ctx.ink.text(ctx.d, (x1 - tlen(tx["high_label"], fs) / 2, y + 38), tx["high_label"], fs,
                         theme.TEXT_SECONDARY, "label")
        fl = font(theme.T_LABEL)
        # the left-most marker's label runs left, the right-most runs right: never collide
        left_is_open = xo <= xc
        for label, x, p, is_left in ((tx["open_label"], xo, po, left_is_open),
                                     (tx["close_label"], xc, pm, not left_is_open)):
            if p <= 0:
                continue
            w = tlen(label, fl)
            lx = (x - w + 10) if is_left else (x - 10)
            lx = min(max(lx, self.SUPPORT[0] + 20), self.SUPPORT[2] - 20 - w)
            ctx.ink.text(ctx.d, (lx, y - 72), label, fl,
                         alpha(col if label == tx["close_label"] else theme.TEXT_SECONDARY, p), "label")


# --------------------------------------------------------------------------- Nifty chart story
class MarketStructureScene(RadarStockScene):
    """The Nifty chart story, composed from the Radar chart components (same grammar) but with
    no evidence strip: the day's range is already the Market Pulse's supporting fact. The card
    therefore ends under the plot and the sentence sits directly beneath it - found on the real
    24 Sep 2026 session, where the full Radar card left its lower third empty. A brand edge
    (not a story counter) marks it as MARKET rather than RADAR."""
    CARD = (RADAR_CARD[0], RADAR_CARD[1], RADAR_CARD[2], 1016)
    TAKEAWAY_Y = 1048

    def paint(self, ctx, t):
        from .radar_story_scene import (PLOT, T_CARD, T_EVENT, AverageOverlay, CandlestickPanel,
                                        EventHighlight, PriceTags, RangeOverlay, StoryIdentity,
                                        Takeaway)
        tx, data = self.spec.texts, self.spec.data
        StoryIdentity.draw(ctx, tx, data["change_positive"], t)
        card(ctx, self.CARD, phase(t, T_CARD, 0.35), fill=theme.PANEL)
        panel = CandlestickPanel(data)
        hr = HiRes(self.CARD)
        for r in range(1, 4):
            yy = PLOT[1] + (PLOT[3] - PLOT[1]) * r / 4
            hr.line([(PLOT[0], yy), (PLOT[2], yy)], (255, 255, 255, 10), 1.2, caps=False)
        fam = data["event_family"]
        if fam in ("RANGE_UP", "RANGE_DOWN"):
            RangeOverlay.draw(ctx, hr, panel, data.get("band"), fam, t)
        elif fam in ("MA_UP", "MA_DOWN"):
            AverageOverlay.draw(ctx, hr, panel, data.get("ma_series"), t)
        EventHighlight.glow(hr, panel, fam, t)
        panel.draw(hr, t, phase(t, T_EVENT, 0.5))
        ctx.mark("candles", PLOT)
        EventHighlight.draw(ctx, hr, panel, fam, tx.get("callout"), t)
        hr.composite(ctx.layer)
        PriceTags.draw(ctx, panel, data, t)
        Takeaway.draw(ctx, tx["takeaway"], t, y=self.TAKEAWAY_Y)
        p = phase(t, 0.15, 0.35)
        he = HiRes((self.CARD[0], self.CARD[1] - 2, self.CARD[2], self.CARD[1] + 8))
        he.rect((self.CARD[0] + 24, self.CARD[1], self.CARD[2] - 24, self.CARD[1] + 5),
                fill=alpha(theme.BRAND, p), radius=3)
        he.composite(ctx.layer)


# --------------------------------------------------------------------------- sectors
class SectorBoardScene(Scene):
    LEAD = (theme.X0, 436, 604, 880)
    LAG = (628, 536, theme.X1, 880)
    STRIP_Y = 920
    STRIP = (theme.X0, 962, theme.RIGHT_RAIL_X, 1250)

    def paint(self, ctx, t):
        tx, rows = self.spec.texts, self.spec.data["rows"]
        y = headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        subline(ctx, tx["tone"], y + 4, phase(t, 0.2, 0.4))
        n = len(rows)
        if n == 1:
            self._hero(ctx, t, (theme.X0, 436, theme.X1, 880), rows[0], tx.get("leader_tag", ""), 0.4,
                       big=True)
        else:
            self._hero(ctx, t, self.LEAD, rows[0], tx["leader_tag"], 0.4, big=True)
            self._hero(ctx, t, self.LAG, rows[-1], tx["laggard_tag"], 0.62, big=False)
        self._strip(ctx, t, rows, tx)

    def _hero(self, ctx, t, box, r, tag, start, big):
        k = K.snap(t, start, 0.32)
        if k <= 0:
            return
        col = signed_color(r["positive"])
        x0, y0, x1, y1 = box
        s = min(k, 1.0)
        dy = (1 - s) * 24
        hr = HiRes((x0 - 8, y0 - 8, x1 + 8, y1 + 8))
        hr.rect((x0, y0 + dy, x1, y1 + dy), fill=alpha(heatmap_cell_colour(r["numeric"]), s), radius=26,
                outline=alpha(col, 0.9 * s), width=2.6 if big else 2.0)
        hr.composite(ctx.layer)
        pad = 34
        if tag:
            chip(ctx, x0 + pad, y0 + pad + dy, tag, col, p=s, size=theme.T_SMALL)
        fn = fit(r["name"], x1 - x0 - 2 * pad, 72 if big else 50, min_size=28)
        ctx.ink.text(ctx.d, (x0 + pad, y0 + pad + 58 + dy), r["name"], fn, alpha(theme.TEXT_PRIMARY, s),
                     "name")
        fv = fit(r["value"], x1 - x0 - 2 * pad - 60, 136 if big else 84, min_size=40)
        vy = y1 - pad - fv.size - 8 + dy
        tri = HiRes((x0 + pad, vy, x0 + pad + 56, vy + fv.size + 10))
        triangle(tri, x0 + pad + 22, vy + fv.size * 0.6, fv.size * 0.22, r["positive"], alpha(col, s))
        tri.composite(ctx.layer)
        vb = ctx.ink.text(ctx.d, (x0 + pad + 58, vy), r["value"], fv, alpha(theme.TEXT_PRIMARY, s), "value")
        if big:
            _underline(ctx, vb, phase(t, start + 0.45, 0.4), col, width=6.0)
        ctx.mark("heat_tile", box)

    @classmethod
    def strip_layout(cls, n):
        cols = 3 if n <= 3 else 4
        rows = -(-n // cols)
        gap = 12
        h = min(140, (cls.STRIP[3] - cls.STRIP[1] - gap * (rows - 1)) / max(rows, 1))
        w = (cls.STRIP[2] - cls.STRIP[0] - gap * (cols - 1)) / cols
        return [(cls.STRIP[0] + (i % cols) * (w + gap), cls.STRIP[1] + (i // cols) * (h + gap),
                 cls.STRIP[0] + (i % cols) * (w + gap) + w, cls.STRIP[1] + (i // cols) * (h + gap) + h)
                for i in range(n)]

    def _strip(self, ctx, t, rows, tx):
        n = len(rows)
        if n < 2:
            return
        pt = phase(t, 1.0, 0.3)
        ctx.ink.text(ctx.d, (theme.X0, self.STRIP_Y), tx["strip_title"], font(theme.T_SMALL),
                     alpha(theme.TEXT_SECONDARY, pt), "label")
        for i, (box, r) in enumerate(zip(self.strip_layout(n), rows)):
            p = phase(t, 1.1 + i * 0.05, 0.3)
            if p <= 0:
                continue
            col = signed_color(r["positive"])
            edge = i == 0 or i == n - 1
            hr = HiRes((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4))
            hr.rect(box, fill=alpha(heatmap_cell_colour(r["numeric"]), p), radius=14,
                    outline=alpha(col if edge else theme.PANEL_BORDER, p), width=2.2 if edge else 1.2)
            hr.composite(ctx.layer)
            h = box[3] - box[1]
            big = h >= 120
            fn = fit(r["name"], box[2] - box[0] - 28, 32 if big else 26, min_size=theme.MIN_FONT)
            fv = fit(r["value"], box[2] - box[0] - 28, 44 if big else (32 if h >= 90 else 28),
                     min_size=theme.MIN_FONT)
            ctx.ink.text(ctx.d, (box[0] + 16, box[1] + 12), r["name"], fn, alpha(theme.TEXT_PRIMARY, p),
                         "tile_name")
            ctx.ink.text(ctx.d, (box[0] + 16, box[3] - fv.size - 16), r["value"], fv, alpha(col, p),
                         "tile_value")
            ctx.mark("heat_tile", box)


# --------------------------------------------------------------------------- optional scenes
class MoversDuelScene(Scene):
    def paint(self, ctx, t):
        tx, cards = self.spec.texts, self.spec.data["cards"]
        y = headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        subline(ctx, tx.get("subline", ""), y + 4, phase(t, 0.2, 0.4))
        n = len(cards)
        gap = 28
        w = (theme.X1 - theme.X0 - gap * (n - 1)) / max(n, 1)
        for i, c in enumerate(cards):
            k = min(K.snap(t, 0.4 + i * 0.2, 0.32), 1.0)
            if k <= 0:
                continue
            x0 = theme.X0 + i * (w + gap)
            box = (x0, 480 + (1 - k) * 24, x0 + w, 1000 + (1 - k) * 24)
            col = signed_color(c["positive"])
            card(ctx, box, k, fill=theme.PANEL, accent=col)
            chip(ctx, box[0] + 34, box[1] + 34, c["tag"], col, p=k, size=theme.T_SMALL)
            fn = fit(c["name"], w - 68, 60, min_size=32)
            ctx.ink.text(ctx.d, (box[0] + 34, box[1] + 110), c["name"], fn, alpha(theme.TEXT_PRIMARY, k), "name")
            fv = fit(c["value"], w - 68, 120, min_size=48)
            vb = ctx.ink.text(ctx.d, (box[0] + 34, box[1] + 220), c["value"], fv, alpha(col, k), "value")
            _underline(ctx, vb, phase(t, 0.9 + i * 0.2, 0.4), col)


class GlobalContextScene(Scene):
    def paint(self, ctx, t):
        tx, dt_ = self.spec.texts, self.spec.data
        y = headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        subline(ctx, tx.get("note", ""), y + 4, phase(t, 0.2, 0.4))
        lead = dt_["lead"]
        k = min(K.snap(t, 0.4, 0.3), 1.0)
        col = signed_color(lead["positive"])
        box = (theme.X0, 480, theme.X1, 800)
        if k > 0:
            card(ctx, box, k, fill=theme.PANEL, accent=col)
            ctx.ink.text(ctx.d, (box[0] + 40, box[1] + 40), lead["name"], font(56), alpha(theme.TEXT_PRIMARY, k),
                         "name")
            vb = ctx.ink.text(ctx.d, (box[0] + 40, box[1] + 140), lead["value"], font(124), alpha(col, k), "value")
            _underline(ctx, vb, phase(t, 0.8, 0.4), col)
        others = dt_.get("others") or []
        if others:
            gap = 18
            w = (theme.RIGHT_RAIL_X - theme.X0 - gap * (len(others) - 1)) / len(others)
            for i, o in enumerate(others):
                p = phase(t, 1.0 + i * 0.1, 0.3)
                if p <= 0:
                    continue
                x0 = theme.X0 + i * (w + gap)
                ob = (x0, 830, x0 + w, 1000)
                oc = signed_color(o["positive"])
                card(ctx, ob, p, fill=theme.PANEL)
                ctx.ink.text(ctx.d, (ob[0] + 24, ob[1] + 24), o["name"], fit(o["name"], w - 48, 28, min_size=24),
                             alpha(theme.TEXT_SECONDARY, p), "name")
                ctx.ink.text(ctx.d, (ob[0] + 24, ob[1] + 84), o["value"], fit(o["value"], w - 48, 52, min_size=28),
                             alpha(oc, p), "value")


class SpecialEventScene(Scene):
    def paint(self, ctx, t):
        tx = self.spec.texts
        headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        k = min(K.snap(t, 0.35, 0.3), 1.0)
        if k <= 0:
            return
        box = (theme.X0, 440, theme.RIGHT_RAIL_X, 820)
        card(ctx, box, k, fill=theme.PANEL, accent=theme.BRAND)
        chip(ctx, box[0] + 40, box[1] + 40, tx["tag"], theme.BRAND, p=k, size=theme.T_SMALL)
        f = fit(tx["title"], (box[2] - box[0] - 80) * 3, 52, min_size=30)
        yy = box[1] + 110
        for line in wrap(tx["title"], f, box[2] - box[0] - 80):
            ctx.ink.text(ctx.d, (box[0] + 40, yy), line, f, alpha(theme.TEXT_PRIMARY, k), "title")
            yy += int(f.size * 1.2)


class QuickCloseScene(Scene):
    """2.6 s: the brand, one light CTA, the signals-not-advice line - nothing longer than the
    market conclusion that came before it."""

    def paint(self, ctx, t):
        from .chrome import BRAND_PART1, BRAND_PART2
        tx = self.spec.texts
        cx = theme.CANVAS_W / 2
        p = phase(t, 0.0, 0.3)
        ctx.ink.centered(ctx.d, cx, 600 + (1 - p) * 16, BRAND_PART1.strip(), font(88),
                         alpha(theme.TEXT_PRIMARY, p), "lockup")
        ctx.ink.centered(ctx.d, cx, 704 + (1 - p) * 16, BRAND_PART2, font(116), alpha(theme.BRAND, p),
                         "lockup")
        pb = phase(t, 0.3, 0.3)
        if pb > 0 and tx.get("cta"):
            f = font(theme.T_BODY)
            w, h = tlen(tx["cta"], f) + 70, 76
            box = (cx - w / 2, 876, cx + w / 2, 876 + h)
            hr = HiRes(box)
            hr.rect(box, fill=alpha((230, 33, 23), pb), radius=h / 2)
            hr.composite(ctx.layer)
            ctx.ink.centered(ctx.d, cx, 876 + h / 2 - f.size * 0.6, tx["cta"], f, alpha((255, 255, 255), pb),
                             "cta")
        pn = phase(t, 0.5, 0.3)
        if tx.get("note") and pn > 0:
            fn = font(theme.T_LABEL, False)
            y = 1010
            for line in wrap(tx["note"], fn, 860)[:2]:
                ctx.ink.centered(ctx.d, cx, y, line, fn, alpha(theme.TEXT_SECONDARY, pn), "note")
                y += 38


__all__ = ["MarketPulseScene", "MarketStructureScene", "SectorBoardScene", "MoversDuelScene",
           "GlobalContextScene", "SpecialEventScene", "QuickCloseScene"]

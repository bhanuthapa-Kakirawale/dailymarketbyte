"""Public market-intelligence scenes (PUBLIC_UNREGISTERED): one idea per screen, the Daily
Market Byte design system, SOURCE / DATA AS OF on every one (the shared provenance bar).

    StructureScene        UNDER THE SURFACE: one Market Structure count over a NAMED universe
                          ("18 / 200" + "NIFTY 200 STOCKS ...") with its sector distribution or
                          advance/decline split - never a stock name
    ExchangeWatchScene    EXCHANGE WATCH: 1-3 official exchange events, each card = the
                          security, the exchange's status, one fixed factual line
    IPOWatchScene         IPO WATCH: one IPO, its dated event and up to 5 official fact rows
    PrimaryMarketScene    PRIMARY MARKET board: 2-4 IPOs with today's dated event

Every string is declared in the storyboard's `texts`; nothing here decides what appears.
"""
from __future__ import annotations

from . import hook_kit as K
from . import theme
from .chartkit import HiRes
from .pre_scenes import TEXT_RIGHT_LOW, _top
from .scenes import Scene, alpha, card, chip, headline, phase, signed_color, subline
from .typography import fit, font, tlen, wrap

CONTENT_BOTTOM = 1356          # the provenance band starts below this


def _bar(ctx, x0, y0, x1, y1, frac, color, p):
    hr = HiRes((x0, y0, x1, y1))
    hr.rect((x0, y0, x1, y1), fill=alpha(theme.VOLUME_BASE, 0.55 * p), radius=(y1 - y0) / 2)
    if frac > 0:
        hr.rect((x0, y0, x0 + max(y1 - y0, (x1 - x0) * frac), y1), fill=alpha(color, p),
                radius=(y1 - y0) / 2)
    hr.composite(ctx.layer)


# --------------------------------------------------------------------------- market structure
class StructureScene(Scene):
    HERO = (theme.X0, 452, theme.X1, 760)

    def paint(self, ctx, t):
        tx, data = self.spec.texts, self.spec.data
        headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        k = min(K.snap(t, 0.25, 0.3), 1.0)
        if k <= 0:
            return
        box = self.HERO
        card(ctx, box, k, fill=theme.PANEL, accent=theme.VOLUME if data["kind"] != "BREADTH"
             else theme.BRAND)
        x0 = box[0] + 44
        fl = fit(tx["hero_label"], box[2] - x0 - 36, theme.T_LABEL, min_size=theme.MIN_FONT)
        ctx.ink.text(ctx.d, (x0, box[1] + 30), tx["hero_label"], fl,
                     alpha(theme.TEXT_SECONDARY, k), "label")
        fv = fit(tx["hero_value"], box[2] - x0 - 36, 150, min_size=80)
        K.number_reveal(ctx, x0 - 4, box[1] + 76, tx["hero_value"], fv, theme.TEXT_PRIMARY,
                        phase(t, 0.4, 0.35), "hero")
        fd = fit(tx["definition"], box[2] - x0 - 36, 30, bold=False, min_size=theme.MIN_FONT)
        ctx.ink.text(ctx.d, (x0, box[3] - 62), tx["definition"], fd,
                     alpha(theme.TEXT_SECONDARY, phase(t, 0.6, 0.3)), "definition")
        ctx.mark("structure_hero", box)
        y = box[3] + 30
        if tx.get("rows"):
            y = self._rows(ctx, t, tx["rows"], data["rows_n"], y)
        if tx.get("split"):
            y = self._split(ctx, t, tx["split"], data.get("split_n") or [], data["kind"], y)
        if tx.get("takeaway"):
            p = phase(t, 1.3, 0.4)
            f = fit(tx["takeaway"], TEXT_RIGHT_LOW - theme.X0, 40, min_size=30)
            ctx.ink.text(ctx.d, (theme.X0, min(y + 14, CONTENT_BOTTOM - f.size - 8)),
                         tx["takeaway"], f, alpha(theme.HIGHLIGHT, p), "takeaway")

    def _rows(self, ctx, t, rows, ns, y):
        total = max(sum(ns), 1)
        top = max(ns) if ns else 1
        n = len(rows)
        row_h = min(64, (1150 - y) / max(n, 1))
        f = font(34)
        fv = font(36)
        for i, (r, v) in enumerate(zip(rows, ns)):
            p = min(K.snap(t, 0.7 + 0.12 * i, 0.3), 1.0)
            if p <= 0:
                continue
            yy = y + i * row_h
            name = r["name"]
            ctx.ink.text(ctx.d, (theme.X0, yy + 8), name, fit(name, 300, 34, min_size=26),
                         alpha(theme.TEXT_PRIMARY, p), "sector")
            _bar(ctx, theme.X0 + 320, yy + 18, TEXT_RIGHT_LOW - 90, yy + 40,
                 (v / top) * p, theme.VOLUME if name != "Others" else theme.BENCHMARK, p)
            ctx.ink.right(ctx.d, TEXT_RIGHT_LOW, yy + 6, r["value"], fv, alpha(theme.TEXT_PRIMARY, p),
                          "count")
            ctx.mark("sector_bar", (theme.X0 + 320, yy + 18, TEXT_RIGHT_LOW - 90, yy + 40))
        return y + n * row_h

    def _split(self, ctx, t, split, ns, kind, y):
        p = phase(t, 0.8, 0.4)
        if kind == "BREADTH" and ns:
            total = max(sum(ns), 1)
            x, x1, h = theme.X0, TEXT_RIGHT_LOW, 34
            cols = [theme.POSITIVE, theme.NEGATIVE, theme.BENCHMARK]
            hr = HiRes((x, y, x1, y + h))
            cur = x
            for v, c in zip(ns, cols):
                w = (x1 - x) * v / total * p
                if w > 0:
                    hr.rect((cur, y, cur + w, y + h), fill=alpha(c, 0.95 * p), radius=6)
                    cur += w
            hr.composite(ctx.layer)
            ctx.mark("breadth_bar", (x, y, x1, y + h))
            y += h + 22
        f = font(30)
        x = theme.X0
        for s in split:
            text = f"{s['label']}  {s['value']}"
            w = tlen(text, f)
            if x + w > TEXT_RIGHT_LOW:
                x = theme.X0
                y += 46
            ctx.ink.text(ctx.d, (x, y), text, f, alpha(theme.TEXT_PRIMARY, p), "split")
            x += w + 44
        return y + 50


# --------------------------------------------------------------------------- exchange watch
class ExchangeWatchScene(Scene):
    TOP = 440

    def paint(self, ctx, t):
        tx = self.spec.texts
        _top(ctx, t, {"headline": tx["headline"], "subline": tx.get("subline", "")})
        cards = tx["cards"]
        n = len(cards)
        gap = 22
        h = min(280, (CONTENT_BOTTOM - self.TOP - gap * (n - 1)) / max(n, 1))
        for i, c in enumerate(cards):
            k = min(K.snap(t, 0.4 + 0.35 * i, 0.32), 1.0)
            if k <= 0:
                continue
            y0 = self.TOP + i * (h + gap)
            box = (theme.X0 + (1 - k) * 40, y0, TEXT_RIGHT_LOW + (1 - k) * 40, y0 + h)
            card(ctx, box, k, fill=theme.PANEL, accent=theme.HIGHLIGHT)
            x0 = box[0] + 40
            xe = chip(ctx, x0, box[1] + 26, c["tag"], theme.HIGHLIGHT, p=k, size=theme.T_SMALL)
            if c.get("status"):
                fs = fit(c["status"], box[2] - xe - 40, theme.T_SMALL, min_size=theme.MIN_FONT)
                ctx.ink.text(ctx.d, (xe + 16, box[1] + 34), c["status"], fs,
                             alpha(theme.TEXT_SECONDARY, k), "status")
            fn = fit(c["name"], box[2] - x0 - 36, 52, min_size=34)
            ctx.ink.text(ctx.d, (x0, box[1] + 84), c["name"], fn, alpha(theme.TEXT_PRIMARY, k), "name")
            y = box[1] + 84 + fn.size + 10
            if c.get("company") and h >= 240:
                fc = fit(c["company"], box[2] - x0 - 36, 28, bold=False, min_size=theme.MIN_FONT)
                ctx.ink.text(ctx.d, (x0, y), c["company"], fc, alpha(theme.TEXT_SECONDARY, k), "company")
                y += fc.size + 10
            line = c["line"] + (f" {c['change']}." if c.get("change") else "")
            fl = fit(line, (box[2] - x0 - 36) * 2, 30, bold=False, min_size=theme.MIN_FONT)
            for ln in wrap(line, fl, box[2] - x0 - 36)[:2]:
                if y + fl.size > box[3] - 10:
                    break
                ctx.ink.text(ctx.d, (x0, y), ln, fl, alpha(theme.TEXT_PRIMARY, k), "line")
                y += int(fl.size * 1.2)
            ctx.mark("exchange_card", box)


# --------------------------------------------------------------------------- IPO watch
class IPOWatchScene(Scene):
    BOX = (theme.X0, 440, TEXT_RIGHT_LOW, 1330)

    def paint(self, ctx, t):
        tx = self.spec.texts
        headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        k = min(K.snap(t, 0.3, 0.3), 1.0)
        if k <= 0:
            return
        box = self.BOX
        card(ctx, box, k, fill=theme.PANEL, accent=theme.BRAND)
        x0 = box[0] + 40
        xe = chip(ctx, x0, box[1] + 30, tx["chip"], theme.HIGHLIGHT, p=k, size=theme.T_SMALL)
        chip(ctx, xe + 14, box[1] + 30, tx["board"], theme.SURFACE_RAISED, fg=theme.TEXT_PRIMARY,
             p=k, size=theme.T_SMALL)
        fn = fit(tx["name"], (box[2] - x0 - 36) * 2, 52, min_size=34)
        y = box[1] + 96
        for ln in wrap(tx["name"], fn, box[2] - x0 - 36)[:2]:
            ctx.ink.text(ctx.d, (x0, y), ln, fn, alpha(theme.TEXT_PRIMARY, k), "name")
            y += int(fn.size * 1.15)
        y += 18
        fl, fv = font(theme.T_LABEL), font(40)
        for i, r in enumerate(tx["rows"]):
            p = min(K.snap(t, 0.6 + 0.15 * i, 0.3), 1.0)
            if p <= 0 or y + 110 > box[3]:
                continue
            ctx.ink.text(ctx.d, (x0, y), r["label"], fl, alpha(theme.TEXT_SECONDARY, p), "label")
            fvv = fit(r["value"], box[2] - x0 - 36, 40, min_size=28)
            ctx.ink.text(ctx.d, (x0, y + 36), r["value"], fvv, alpha(theme.TEXT_PRIMARY, p), "value")
            y += 110
        ctx.mark("ipo_card", box)


class PrimaryMarketScene(Scene):
    TOP = 450

    def paint(self, ctx, t):
        tx = self.spec.texts
        headline(ctx, tx["headline"], 296, phase(t, 0.0, 0.4))
        rows = tx["rows"]
        h = min(190, (CONTENT_BOTTOM - self.TOP) / max(len(rows), 1) - 18)
        for i, r in enumerate(rows):
            k = min(K.snap(t, 0.35 + 0.3 * i, 0.3), 1.0)
            if k <= 0:
                continue
            y0 = self.TOP + i * (h + 18)
            box = (theme.X0, y0, TEXT_RIGHT_LOW, y0 + h)
            card(ctx, box, k, fill=theme.PANEL, accent=theme.BRAND)
            x0 = box[0] + 40
            xe = chip(ctx, x0, box[1] + 24, r["chip"], theme.HIGHLIGHT, p=k, size=theme.T_SMALL)
            chip(ctx, xe + 14, box[1] + 24, r["board"], theme.SURFACE_RAISED,
                 fg=theme.TEXT_PRIMARY, p=k, size=theme.T_SMALL)
            fn = fit(r["name"], box[2] - x0 - 36, 42, min_size=28)
            ctx.ink.text(ctx.d, (x0, box[1] + 84), r["name"], fn, alpha(theme.TEXT_PRIMARY, k), "name")
            ctx.mark("ipo_row", box)


__all__ = ["StructureScene", "ExchangeWatchScene", "IPOWatchScene", "PrimaryMarketScene"]

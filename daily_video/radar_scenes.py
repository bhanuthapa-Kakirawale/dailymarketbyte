"""Market Radar scenes, drawn in the same design system as every other Daily Market Byte scene.

`RadarStoryScene` picks a storytelling MECHANISM from the storyboard's `mode` (derived from the
story's own visual type / direction / signal count / event type - never its symbol):

    CONVERGENCE  price breaks its range + amber volume spike + gap to Nifty, then "3 signals"
    BREAKOUT     shaded range, marker above the top edge, then the Nifty comparison
    BREAKDOWN    shaded range with a thick lower edge, "below range" zone, marker under it
    QUIET        "only -1.9%" first, then a dimmed chart, spotlight + magnifier on the cross
    MIXED        split screen - price trend vs relative strength - with a VS badge
    TEXT         validated text card when no local chart evidence exists (never a fake chart)

All numbers/series come from `RadarVisualEvidence` (built upstream from local OHLCV) and the
storyboard's formatted strings. This module draws; it computes nothing.
"""
from __future__ import annotations

import math

from . import theme
from .animations import (animate_breakdown, animate_breakout, animate_chart_reveal, animate_ma_cross,
                         animate_ma_line, animate_range_highlight, animate_relative_comparison,
                         animate_spotlight, animate_volume_spike)
from .annotations import check_chip, legend, ma_cross_marker, magnifier
from .chartkit import ChartArea, HiRes, draw_grid, reveal_points
from .scenes import Scene, alpha, card, chip, headline, phase, signed_color, triangle
from .storyboard import event_direction, event_window
from .typography import fit, font, tlen, wrap


class RadarIntroScene(Scene):
    """Section opener: a radar sweep that lights one blip per flagged stock."""

    def paint(self, ctx, t):
        s, tx = self.spec, self.spec.texts
        y = headline(ctx, s.headline, 300, phase(t, 0.0, 0.5))
        ctx.ink.text(ctx.d, (theme.X0, y + 6), s.subline, font(theme.T_BODY, False),
                     alpha(theme.TEXT_SECONDARY, phase(t, 0.2, 0.5)), "subline")
        cx, cy, R = theme.CANVAS_W / 2, 790, 300
        p = phase(t, 0.2, 0.6)
        hr = HiRes((cx - R - 20, cy - R - 20, cx + R + 20, cy + R + 20))
        hr.circle(cx, cy, R * p, fill=(10, 18, 42, int(200 * p)))
        for r in (R, R * 0.66, R * 0.33):
            hr.circle(cx, cy, r * p, outline=alpha(theme.BRAND, 0.35 * p), width=2.0)
        hr.line([(cx - R * p, cy), (cx + R * p, cy)], alpha(theme.BRAND, 0.18 * p), 1.5, caps=False)
        hr.line([(cx, cy - R * p), (cx, cy + R * p)], alpha(theme.BRAND, 0.18 * p), 1.5, caps=False)
        ang = -math.pi / 2 + t * 2.4
        for k in range(18):
            a = ang - k * 0.05
            hr.polygon([(cx, cy), (cx + R * math.cos(a), cy + R * math.sin(a)),
                        (cx + R * math.cos(a - 0.05), cy + R * math.sin(a - 0.05))],
                       alpha(theme.BRAND, 0.22 * p * (1 - k / 18)))
        hr.line([(cx, cy), (cx + R * math.cos(ang), cy + R * math.sin(ang))], alpha(theme.BRAND, p), 3.0)
        names = tx.get("names") or []
        blips = []
        for i, name in enumerate(names):
            a = -math.pi / 2 + 0.55 + i * (2 * math.pi / max(len(names), 1))
            rr = R * (0.52 if i % 2 else 0.8)
            bx, by = cx + rr * math.cos(a), cy + rr * math.sin(a)
            appear = phase(t, 0.7 + i * 0.32, 0.3)
            if appear > 0:
                hr.circle(bx, by, 18 * appear, fill=alpha(theme.POSITIVE, 0.25 * appear))
                hr.circle(bx, by, 8, fill=alpha(theme.POSITIVE, appear))
                blips.append((bx, by, name, appear))
        hr.composite(ctx.layer)
        fl = font(theme.T_LABEL)
        for bx, by, name, ap in blips:
            w = tlen(name, fl)
            lx = bx + 16 if bx < cx else bx - 16 - w
            ctx.ink.text(ctx.d, (lx, by - 16), name, fl, alpha(theme.TEXT_PRIMARY, ap), "blip")
        pc = phase(t, 2.2, 0.5)
        ctx.ink.centered(ctx.d, cx, 1140, tx["count"], font(theme.T_TITLE),
                         alpha(theme.TEXT_PRIMARY, pc), "count")


class RadarStoryScene(Scene):
    def paint(self, ctx, t):
        mode = self.spec.data["mode"]
        self._title(ctx, t, headline_text=False if mode == "QUIET" else None)
        getattr(self, f"_{mode.lower()}")(ctx, t)

    # ------------------------------------------------------------------ shared blocks
    def _title(self, ctx, t, headline_text=None):
        s, tx = self.spec, self.spec.texts
        p = phase(t, 0.0, 0.45)
        fs = fit(tx["symbol"], 640, theme.T_SYMBOL, min_size=50)
        ctx.ink.text(ctx.d, (theme.X0, 286 + (1 - p) * 14), tx["symbol"], fs,
                     alpha(theme.TEXT_PRIMARY, p), "symbol")
        col = signed_color(s.data["change_positive"])
        if tx.get("change"):
            f = font(theme.T_TITLE)
            w, h = tlen(tx["change"], f) + 40, theme.T_TITLE + 24
            x0 = theme.X1 - w
            hr = HiRes((x0, 304, theme.X1, 304 + h))
            hr.rect((x0, 304, theme.X1, 304 + h), fill=alpha(col, 0.2 * p), radius=h / 2,
                    outline=alpha(col, 0.8 * p), width=2)
            hr.composite(ctx.layer)
            ctx.ink.text(ctx.d, (x0 + 20, 314), tx["change"], f, alpha(col, p), "change")
        ctx.ink.text(ctx.d, (theme.X0 + 2, 386), tx.get("company", ""), font(theme.T_LABEL, False),
                     alpha(theme.TEXT_SECONDARY, p), "company")
        if headline_text is not False:
            headline(ctx, headline_text or s.headline, 434, phase(t, 0.3, 0.5), max_lines=1)

    def _price_area(self, box, ev, band=None, ma=None, down=False):
        """Headroom on the side the event happened, so its callout never has to sit on top of
        the edge label or the data."""
        vals = list(ev.close_series)
        if band:
            vals += [band[0], band[1]]
        if ma:
            vals += [v for v in ma if v is not None]
        return ChartArea.fit(box, vals, len(ev.close_series), pad=0.08,
                             pad_top=0.08 if down else 0.42, pad_bottom=0.42 if down else 0.08)

    def _key_row(self, ctx, area, y, ev, items, p):
        """Axis dates at both ends, the chart legend centred between them."""
        fd = font(theme.T_SMALL, False)
        ctx.ink.text(ctx.d, (area.x0, y), ev.window_dates[0].strftime("%d %b"), fd,
                     theme.TEXT_MUTED, "axis")
        ctx.ink.right(ctx.d, area.x1, y, ev.window_dates[-1].strftime("%d %b"), fd,
                      theme.TEXT_MUTED, "axis")
        legend(ctx, items, (area.x0 + area.x1) / 2, y, p)

    def _event_chart(self, ctx, t, card_box, chart_box, beats, dim=1.0, row_y=None):
        """Price line + the primary event's overlay (range band or moving average) + the
        event marker and callout. `beats` = (line_start, overlay_start, marker_start)."""
        s, tx, ev = self.spec, self.spec.texts, self.spec.data["evidence"]
        event = s.data["event"]
        down = event is not None and event_direction(event) < 0
        col = theme.NEGATIVE if down else theme.POSITIVE
        n = len(ev.close_series)
        w = event_window(event) if event else None
        is_range = bool(event) and "RANGE" in event and event != "RANGE_COMPRESSION"
        is_ma = bool(event) and "SMA" in event
        band = ma = None
        if is_range:
            hi = ev.range_50_high if w == 50 else ev.range_20_high
            lo = ev.range_50_low if w == 50 else ev.range_20_low
            if hi is not None and lo is not None:
                band = (hi, lo)
        if is_ma:
            ma = ev.sma50_series if w == 50 else ev.sma20_series
        area = self._price_area(chart_box, ev, band, ma, down)
        card(ctx, card_box, phase(t, 0.4, 0.4), fill=theme.PANEL)
        hr = HiRes(card_box)
        draw_grid(hr, area)
        l0, o0, m0 = beats
        if band and down:
            pz = phase(t, o0 + 0.3, 0.6)
            if pz > 0:
                hr.rect((area.x0, area.py(band[1]), area.x1, area.y1),
                        fill=alpha(theme.NEGATIVE, 0.07 * pz))
        keys = [("line", theme.TEXT_PRIMARY, "Close")]
        if band:
            i0 = max(0, n - 1 - w)
            animate_range_highlight(ctx, hr, area, i0, n - 2, band[1], band[0], phase(t, o0, 0.8),
                                    "bottom" if down else "top", col, tx.get("edge_label"))
            keys.append(("band", theme.TEXT_SECONDARY, tx.get("band_label") or "Prior range"))
        if ma:
            animate_ma_line(ctx, hr, area, ma, phase(t, o0, 1.0), None)
            keys.append(("line", theme.BRAND, tx.get("ma_label") or "Average"))
        animate_chart_reveal(hr, area, ev.close_series, phase(t, l0, 1.8), dim=dim)
        # Callouts stay inside the plot itself - never down into the dates/legend row.
        bounds = (card_box[0] + 16, card_box[1] + 16, card_box[2] - 16,
                  (chart_box[3] + 6) if row_y is not None else card_box[3] - 16)
        pm = phase(t, m0, 0.6)
        if is_range:
            (animate_breakdown if down else animate_breakout)(
                ctx, hr, area, n - 1, ev.close_series[-1], t, pm, tx.get("callout"), bounds)
        elif is_ma:
            animate_ma_cross(ctx, hr, area, n - 1, ev.close_series[-1], t, pm, tx.get("callout"),
                             bounds, col)
        hr.composite(ctx.layer)
        if row_y is not None:
            self._key_row(ctx, area, row_y, ev, keys, phase(t, o0 + 0.4, 0.5))
        return area

    def _takeaway(self, ctx, t, y, start, chips=None, max_w=None):
        s = self.spec
        p = phase(t, start, 0.5)
        if p <= 0 or not s.takeaway:
            return
        max_w = max_w or (theme.RIGHT_RAIL_X - theme.X0)
        f = fit(s.takeaway, max_w * 2, theme.T_TITLE, min_size=30)
        yy = y + (1 - p) * 14
        for line in wrap(s.takeaway, f, max_w)[:2]:
            ctx.ink.text(ctx.d, (theme.X0, yy), line, f, alpha(theme.TEXT_PRIMARY, p), "takeaway")
            yy += int(f.size * 1.2)
        if chips:
            x = theme.X0
            for i, c in enumerate(chips):
                pc = phase(t, start + 0.25 + i * 0.18, 0.35)
                if pc > 0:
                    x = check_chip(ctx, x, yy + 12, c, pc) + 14

    def _relative(self, ctx, t, box, start, compact=False):
        tx, ev, dt_ = self.spec.texts, self.spec.data["evidence"], self.spec.data
        if not ev.relative_stock_normalized or dt_.get("rel20") is None:
            return False
        p = phase(t, start, 1.2)
        if p <= 0:
            return True
        animate_relative_comparison(ctx, box, ev.relative_stock_normalized,
                                    ev.relative_benchmark_normalized, t, p,
                                    phase(t, start + 1.2, 0.5), tx["relative_names"],
                                    tx["relative_label"], tx["relative_window"],
                                    dt_["rel20"] > 0, compact=compact)
        return True

    # ------------------------------------------------------------------ modes
    def _chart_with_volume(self, ctx, t, beats, vol_start):
        """Price chart on top, key row, then the volume strip - one card, one timeline."""
        ev, tx = self.spec.data["evidence"], self.spec.texts
        has_vol = bool(tx.get("volume_label"))
        cb = (theme.X0, 520, theme.X1, 1090)
        chart_bottom = 832 if has_vol else 1000
        self._event_chart(ctx, t, cb, (cb[0] + 40, 596, cb[2] - 64, chart_bottom), beats,
                          row_y=chart_bottom + 18)
        if has_vol:
            pv = phase(t, vol_start, 0.8)
            if pv > 0:
                animate_volume_spike(ctx, (cb[0] + 40, 896, cb[2] - 64, 1060), ev.volume_series, t,
                                     pv, phase(t, vol_start + 0.6, 0.5), tx["volume_label"])

    def _convergence(self, ctx, t):
        self._chart_with_volume(ctx, t, (0.7, 2.3, 3.1), 3.9)
        self._relative(ctx, t, (theme.X0, 1108, theme.RIGHT_RAIL_X, 1290), 5.2)
        self._takeaway(ctx, t, 1314, 6.9, chips=self.spec.texts.get("chips"))

    def _breakout(self, ctx, t):
        has_vol = bool(self.spec.texts.get("volume_label"))
        self._chart_with_volume(ctx, t, (0.7, 2.2, 3.2), 4.0)
        self._relative(ctx, t, (theme.X0, 1108, theme.RIGHT_RAIL_X, 1290),
                       5.0 if has_vol else 4.4)
        self._takeaway(ctx, t, 1318, self.duration - 1.5)

    def _breakdown(self, ctx, t):
        self._breakout(ctx, t)

    def _quiet(self, ctx, t):
        s, tx, ev = self.spec, self.spec.texts, self.spec.data["evidence"]
        pq = phase(t, 0.3, 0.5)
        ctx.ink.text(ctx.d, (theme.X0, 432), tx["quiet_line"], font(theme.T_HEADLINE),
                     alpha(theme.TEXT_PRIMARY, pq), "headline")
        swap = phase(t, 4.3, 0.4)
        pt = phase(t, 1.2, 0.4) * (1 - swap)
        if pt > 0:
            ctx.ink.text(ctx.d, (theme.X0, 504), tx["quiet_turn"], font(theme.T_TITLE),
                         alpha(theme.HIGHLIGHT, pt), "turn")
        if swap > 0:
            ctx.ink.text(ctx.d, (theme.X0, 504 + (1 - swap) * 10), s.headline, font(theme.T_TITLE),
                         alpha(theme.HIGHLIGHT, swap), "headline2")
        cb = (theme.X0, 580, theme.X1, 1090)
        dim = 1.0 - 0.45 * phase(t, 3.0, 0.6)
        area = self._event_chart(ctx, t, cb, (cb[0] + 40, 640, cb[2] - 64, 1010), (2.0, 2.6, 4.1),
                                 dim=dim, row_y=1030)
        n = len(ev.close_series)
        px, py = area.pt(n - 1, ev.close_series[-1])
        ps = phase(t, 3.2, 0.6)
        animate_spotlight(ctx, cb, (px - 30, py), 92, ps)
        pl = phase(t, 3.5, 0.6)
        event = s.data["event"]
        w = event_window(event) if event else 20
        ma = ev.sma50_series if w == 50 else ev.sma20_series
        lens_c = (cb[0] + 250, cb[1] + 190)

        def zoomed(hr):
            k = 8
            closes = ev.close_series[-k:]
            mas = [v for v in ma[-k:]] if ma else []
            inner = (lens_c[0] - 110, lens_c[1] - 80, lens_c[0] + 100, lens_c[1] + 80)
            za = ChartArea.fit(inner, closes + [v for v in mas if v is not None], k, pad=0.18)
            if mas:
                hr.line(reveal_points(za, mas, 1.0), alpha(theme.BRAND, 1.0), 4.0)
            hr.line(reveal_points(za, closes, 1.0), alpha(theme.TEXT_PRIMARY, 1.0), 4.4)
            ex, ey = za.pt(k - 1, closes[-1])
            hr.circle(ex, ey, 9, fill=alpha(theme.NEGATIVE if event_direction(event or "") < 0
                                            else theme.POSITIVE, 1.0))

        if pl > 0:
            magnifier(ctx, lens_c, 128, zoomed, pl, source=(px - 30, py, 92))
            fz = font(theme.T_SMALL, True)
            ctx.ink.centered(ctx.d, lens_c[0], lens_c[1] + 140, "Zoomed: last 8 sessions", fz,
                             alpha(theme.HIGHLIGHT, pl), "lens_label")
        self._relative(ctx, t, (theme.X0, 1108, theme.RIGHT_RAIL_X, 1290), 5.0)
        self._takeaway(ctx, t, 1316, 6.5)

    def _mixed(self, ctx, t):
        s, tx, ev = self.spec, self.spec.texts, self.spec.data["evidence"]
        event = s.data["event"]
        down = event is not None and event_direction(event) < 0
        lcol = theme.NEGATIVE if down else theme.POSITIVE
        ahead = (s.data.get("rel20") or 0) > 0
        rcol = theme.POSITIVE if ahead else theme.NEGATIVE
        L = (theme.X0, 530, 522, 1170)
        R = (558, 530, theme.X1, 1170)
        for box, title, verdict, col, up, start in (
                (L, tx["left_title"], tx["left_verdict"], lcol, not down, 0.6),
                (R, tx["right_title"], tx["right_verdict"], rcol, ahead, 2.6)):
            p = phase(t, start - 0.2, 0.4)
            card(ctx, box, p, fill=theme.PANEL)
            ctx.ink.text(ctx.d, (box[0] + 30, box[1] + 28), title, font(theme.T_LABEL),
                         alpha(theme.TEXT_SECONDARY, p), "panel_title")
            pv = phase(t, start + 1.6, 0.4)
            if pv > 0:
                hr = HiRes((box[0] + 26, box[1] + 70, box[0] + 70, box[1] + 116))
                triangle(hr, box[0] + 46, box[1] + 94, 15, up, alpha(col, pv))
                hr.composite(ctx.layer)
                ctx.ink.text(ctx.d, (box[0] + 76, box[1] + 70), verdict, font(theme.T_TITLE),
                             alpha(col, pv), "verdict")
        # left: price vs its average, with the cross marked
        w = event_window(event) if event else 50
        ma = ev.sma50_series if w == 50 else ev.sma20_series
        lb = (L[0] + 28, L[1] + 150, L[2] - 40, L[3] - 170)
        area = ChartArea.fit(lb, list(ev.close_series) + [v for v in ma if v is not None],
                             len(ev.close_series), pad=0.1)
        hr = HiRes(L)
        draw_grid(hr, area, rows=3)
        animate_ma_line(ctx, hr, area, ma, phase(t, 0.8, 1.2), None)
        animate_chart_reveal(hr, area, ev.close_series, phase(t, 0.7, 1.4), width=3.6)
        pm = phase(t, 2.2, 0.5)
        n = len(ev.close_series)
        x, y = area.pt(n - 1, ev.close_series[-1])
        ma_cross_marker(ctx, hr, x, y, t, pm, lcol)
        hr.composite(ctx.layer)
        fe = font(theme.T_SMALL, False)
        yy = L[3] - 150
        for line in wrap(tx.get("event_line", ""), fe, L[2] - L[0] - 60)[:2]:
            ctx.ink.text(ctx.d, (L[0] + 30, yy), line, fe, alpha(theme.TEXT_PRIMARY, pm), "event_line")
            yy += fe.size + 8
        legend(ctx, [("line", theme.TEXT_PRIMARY, "Close"),
                     ("line", theme.BRAND, tx.get("ma_label") or "Average")],
               (L[0] + L[2]) / 2, L[3] - 52, phase(t, 1.2, 0.5))
        # right: stock vs Nifty
        self._relative(ctx, t, (R[0] + 10, R[1] + 130, R[2] - 10, R[3] - 50), 2.8, compact=True)
        if self.spec.data.get("rel20") is not None:
            legend(ctx, [("line", rcol, tx["relative_names"][0]),
                         ("line", theme.BENCHMARK, tx["relative_names"][1])],
                   (R[0] + R[2]) / 2, R[3] - 52, phase(t, 3.0, 0.5))
        # VS badge sits between the two verdicts: "Weaker  VS  Stronger"
        pv = phase(t, 0.5, 0.4)
        cx, cy = (L[2] + R[0]) / 2, L[1] + 93
        hr = HiRes((cx - 40, cy - 40, cx + 40, cy + 40))
        hr.circle(cx, cy, 32 + 2 * math.sin(t * 4), fill=alpha(theme.SURFACE_RAISED, pv),
                  outline=alpha(theme.MIXED, pv), width=3)
        hr.composite(ctx.layer)
        fv = font(theme.T_LABEL)
        ctx.ink.centered(ctx.d, cx, cy - 15, "VS", fv, alpha(theme.MIXED, pv), "vs")
        ctx.mark("mixed_split", (L[0], L[1], R[2], R[3]))
        self._takeaway(ctx, t, 1206, 5.0)

    def _text(self, ctx, t):
        s = self.spec
        box = (theme.X0, 540, theme.RIGHT_RAIL_X, 1000)
        card(ctx, box, phase(t, 0.4, 0.4))
        self._takeaway(ctx, t, 1040, 1.2)


__all__ = ["RadarIntroScene", "RadarStoryScene"]

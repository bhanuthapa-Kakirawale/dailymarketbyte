"""Generic animation templates. Each takes already-validated series/levels and a progress value
(0..1) and draws one storytelling beat. None of them knows which stock it is drawing - a scene
picks templates by EVENT TYPE, so tomorrow's stocks get the same treatment automatically.
"""
from __future__ import annotations

from . import theme
from .annotations import (Ctx, breakdown_marker, breakout_marker, callout, end_label, gap_bracket,
                          ma_cross_marker, range_band, spotlight)
from .chartkit import ChartArea, HiRes, draw_grid, reveal_points
from .typography import fit, font, tlen


def _a(c, p):
    return (*c[:3], max(0, min(255, int(255 * p))))


def animate_chart_reveal(hr: HiRes, area: ChartArea, values: list, p: float,
                         line=theme.TEXT_PRIMARY, fill=theme.BRAND, width=4.2, dim=1.0) -> list:
    """Line draws left to right with a soft gradient under it."""
    pts = reveal_points(area, values, p)
    if len(pts) >= 2:
        hr.area_fill(pts, area.y1, fill, top_alpha=int(64 * dim))
        hr.line(pts, _a(line, 0.18 * dim), width * 2.6)
        hr.line(pts, _a(line, dim), width)
    return pts


def animate_range_highlight(ctx: Ctx, hr: HiRes, area: ChartArea, i0: int, i1: int, lo: float,
                            hi: float, p: float, emphasize: str, color, edge_label: str | None):
    """The prior range as a shaded band; the edge the story is about drawn thick in the
    story's colour, with its level labelled right on that edge (outside the band, where the
    prior price line never was). The band's name goes in the chart legend, not on the data."""
    x0, x1 = area.px(i0), area.px(i1)
    y_top, y_bot = area.py(hi), area.py(lo)
    range_band(ctx, hr, x0, x1, y_top, y_bot, p, emphasize=emphasize, edge_color=color)
    if p > 0.6 and edge_label:
        fe = font(theme.T_LABEL, True)
        ey = y_top - fe.size - 12 if emphasize == "top" else y_bot + 10
        ctx.later((x0 + 16, ey), edge_label, fe, _a(color, p), "edge_label")


def animate_breakout(ctx, hr, area, i, v, t, p, label, bounds, color=theme.POSITIVE):
    """Marker on the session that closed above the range; the callout sits up and to the
    left in the headroom the chart reserves above a breakout - clear of the edge label."""
    x, y = area.pt(i, v)
    breakout_marker(ctx, hr, x, y, t, p, color)
    callout(ctx, hr, (x, y), label, color, bounds, side="up-left", p=p, offset=(40, 66))


def animate_breakdown(ctx, hr, area, i, v, t, p, label, bounds, color=theme.NEGATIVE):
    x, y = area.pt(i, v)
    breakdown_marker(ctx, hr, x, y, t, p, color)
    callout(ctx, hr, (x, y), label, color, bounds, side="down-left", p=p, offset=(40, 66))


def animate_ma_line(ctx, hr, area, ma: list, p: float, label: str | None):
    """A moving average is always a cyan line across the whole product (Nifty's 20-day
    average uses the same colour), labelled once near where it starts."""
    pts = reveal_points(area, ma, p)
    if len(pts) >= 2:
        hr.line(pts, _a(theme.BRAND, 0.28), 10.0)
        hr.line(pts, _a(theme.BRAND, 1.0), 3.6)
    if label and p > 0.5:
        first = next((k for k, v in enumerate(ma) if v is not None), None)
        if first is not None:
            f = font(theme.T_SMALL, True)
            x, y = area.pt(first, ma[first])
            ctx.later((x + 6, y - f.size - 14), label, f, _a(theme.BRAND, p), "ma_label")


def animate_ma_cross(ctx, hr, area, i, v, t, p, label, bounds, color):
    x, y = area.pt(i, v)
    ma_cross_marker(ctx, hr, x, y, t, p, color)
    side = "down-left" if color == theme.NEGATIVE else "up-left"
    callout(ctx, hr, (x, y), label, color, bounds, side=side, p=p, offset=(46, 70))


def animate_volume_spike(ctx: Ctx, box, volumes: list, t: float, p_bars: float, p_spike: float,
                         label: str | None):
    """Bars grow in; the current session's bar turns amber and a label row above the strip
    points at it - the label never covers the 'normal' bars it is being compared with."""
    n = len(volumes)
    vals = [v or 0.0 for v in volumes]
    top = max(vals) or 1.0
    x0, ly0, x1, y1 = box
    f = font(theme.T_ANNOT, True)
    y0 = ly0 + f.size + 18
    hr = HiRes((x0, ly0, x1, y1 + 4))
    slot = (x1 - x0) / max(n, 1)
    bw = slot * 0.62
    shown = p_bars * n
    for k, v in enumerate(vals):
        g = min(1.0, shown - k)
        if g <= 0:
            break
        h = (y1 - y0) * v / top * g
        last = k == n - 1
        cx = x0 + slot * (k + 0.5)
        col = _a(theme.VOLUME, 1.0) if (last and p_spike > 0) else _a(theme.VOLUME_BASE, 1.0)
        if last and p_spike > 0:
            hr.rect((cx - bw, y1 - h - 10, cx + bw, y1), fill=_a(theme.VOLUME, 0.22 * p_spike), radius=6)
        hr.rect((cx - bw / 2, y1 - h, cx + bw / 2, y1), fill=col, radius=min(4, bw / 2))
    if p_spike > 0 and n and label:
        lx = x0 + slot * (n - 0.5)
        ly = y1 - (y1 - y0) * vals[-1] / top
        ctx.mark("volume_spike", (lx - bw, ly, lx + bw, y1))
        tw = tlen(label, f)
        tx = lx - 34 - tw
        ty = ly0 + 2
        hr.line([(tx + tw + 8, ty + f.size / 2 + 4), (lx, ty + f.size / 2 + 4), (lx, ly - 8)],
                _a(theme.VOLUME, p_spike), 2.6)
        hr.polygon([(lx - 7, ly - 16), (lx + 7, ly - 16), (lx, ly - 5)], _a(theme.VOLUME, p_spike))
        ctx.later((tx, ty), label, f, _a(theme.VOLUME, p_spike), "volume_label")
        ctx.mark("callout", (tx, ty, tx + tw, ty + f.size + 6))
    hr.composite(ctx.layer)


def animate_relative_comparison(ctx: Ctx, box, stock: list, bench: list, t: float, p: float,
                                p_gap: float, names: list, label: str, window: str, ahead: bool,
                                compact: bool = False):
    """Stock vs Nifty, both starting at the same point: the lines separate, and the gap at
    the end is bracketed. The gap label is the story's own validated relative figure."""
    x0, y0, x1, y1 = box
    col = theme.POSITIVE if ahead else theme.NEGATIVE
    hr = HiRes(box)
    if not compact:   # compact mode sits inside a panel that already has its own card
        hr.rect(box, fill=_a(theme.PANEL, 0.96 * min(1, p * 3)), radius=theme.RADIUS_CARD,
                outline=_a(theme.PANEL_BORDER, 0.9 * min(1, p * 3)), width=1.6)
    if compact:
        chart = (x0 + 30, y0 + 26, x1 - 60, y1 - 110)
    else:
        chart = (x0 + 340, y0 + 34, x1 - 56, y1 - 34)
    area = ChartArea.fit(chart, list(stock) + list(bench), len(stock), pad=0.14)
    hr.dashed((area.x0, area.py(100.0)), (area.x1, area.py(100.0)), (255, 255, 255, 40), 1.6)
    pb = reveal_points(area, bench, p)
    ps = reveal_points(area, stock, p)
    hr.line(pb, _a(theme.BENCHMARK, 1.0), 3.4)
    hr.line(ps, _a(col, 0.25), 10)
    hr.line(ps, _a(col, 1.0), 4.2)
    if p >= 1.0:
        for pts, c in ((pb, theme.BENCHMARK), (ps, col)):
            hr.circle(pts[-1][0], pts[-1][1], 7, fill=_a(c, 1.0))
        gap_bracket(ctx, hr, area.x1 + 26, ps[-1][1], pb[-1][1], col, p_gap)
    hr.composite(ctx.layer)

    pl = min(1.0, p * 2)
    fl = fit(label, 290 if not compact else x1 - x0 - 60, theme.T_TITLE, min_size=28)
    fs = font(theme.T_SMALL, False)
    fn = font(theme.T_SMALL, True)
    if compact:
        ly = y1 - 96
        ctx.ink.text(ctx.d, (x0 + 30, ly), label, fl, _a(col, pl), "relative_label")
        ctx.ink.text(ctx.d, (x0 + 30, ly + fl.size + 10), window, fs, _a(theme.TEXT_SECONDARY, pl),
                     "window")
        return
    ctx.ink.text(ctx.d, (x0 + 34, y0 + 30), label, fl, _a(col, pl), "relative_label")
    ctx.ink.text(ctx.d, (x0 + 34, y0 + 36 + fl.size), window, fs, _a(theme.TEXT_SECONDARY, pl),
                 "window")
    ly = y0 + 36 + fl.size + 44
    for name, c in ((names[0], col), (names[1], theme.BENCHMARK)):
        leg = HiRes((x0 + 34, ly + 8, x0 + 74, ly + 22))
        leg.line([(x0 + 36, ly + 15), (x0 + 70, ly + 15)], _a(c, pl), 4.0)
        leg.composite(ctx.layer)
        ctx.ink.text(ctx.d, (x0 + 84, ly), name, fn, _a(theme.TEXT_PRIMARY, pl), "legend")
        ly += fn.size + 14


def animate_spotlight(ctx, box, center, r, p):
    spotlight(ctx, box, center, r, p)


__all__ = ["animate_chart_reveal", "animate_range_highlight", "animate_breakout",
           "animate_breakdown", "animate_ma_line", "animate_ma_cross", "animate_volume_spike",
           "animate_relative_comparison", "animate_spotlight", "end_label", "draw_grid"]

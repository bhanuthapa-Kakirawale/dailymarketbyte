"""The Dynamic Hook, Phase 1A visual language: an editorial collage that builds, then settles.

    0.00 ── beat 1 ── 0.85 ── beat 2 ── 1.70 ── beat 3 ── 2.55 ────── settled hook ────── 4.60
    base card          a paper slip snaps       a second slip snaps     the collage clears; the hero
    (neon ring on      in from its side         in from the other       builds in <0.8s, then holds
    the first figure)  (up = gains, right)      side (down = falls)     still enough to freeze

Frame 0 already shows the brand, the curiosity line (its first figure neon-underlined as it
draws) and the first beat's figure. Beats ACCUMULATE instead of cutting: each new fact is
pinned on top of the last, so by the settle the viewer has seen the whole argument assembled.

Driven only by `BeatKind` / `HeroVisual` and the plan's payloads - never by a symbol. All
strings come from the storyboard's declared `texts`; all numbers from validated payloads. The
toolkit it draws with lives in `hook_kit.py`.
"""
from __future__ import annotations

import math
import re

from PIL import Image

from video import ease, heatmap_cell_colour

from . import hook_kit as K
from . import theme
from .animations import animate_chart_reveal, animate_range_highlight
from .annotations import Ctx, check_chip, event_marker
from .chartkit import ChartArea, HiRes, draw_candles, draw_grid, reveal_points
from .scenes import Scene, alpha, new_layer, phase, signed_color, triangle
from .typography import fit, font, tlen, wrap

# --------------------------------------------------------------------------- layout
EYEBROW_Y = 290
CURIOSITY_Y = 332
STAGE = (theme.X0, 514, theme.X1, 1168)
PIPS_Y = 1186
SUMMARY_Y = 1204
CURIOSITY_SIZE, CURIOSITY_MIN = 64, 44
SLIP_W, SLIP_H = 600, 340
# Two slots for the stacked slips: a rising fact is pinned upper-right, a falling one
# lower-left - the collage's geometry already says "up here, down there".
SLOT_UP = {"cx": theme.X0 + 40 + 300 + 300, "cy": STAGE[1] + 16 + SLIP_H / 2, "angle": 3.0,
           "side": 1}
SLOT_DOWN = {"cx": theme.X0 + 20 + SLIP_W / 2, "cy": STAGE[3] - 20 - SLIP_H / 2, "angle": -3.0,
             "side": -1}
_NUM = re.compile(r"^[+\-−]?(?:Rs\.?)?\d[\d,]*(?:\.\d+)?(?:%|×)?[.,:;]?$")


def _fs(h, frac, lo=theme.MIN_FONT, hi=400):
    return int(max(lo, min(hi, h * frac)))


# --------------------------------------------------------------------------- rich text
def _word_color(word: str):
    if _NUM.match(word):
        if word.startswith("+"):
            return theme.POSITIVE
        if word[0] in "-−":
            return theme.NEGATIVE
        return theme.HIGHLIGHT
    return theme.TEXT_PRIMARY


def _sentences(text: str) -> list:
    return [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]


def rich_lines(text: str, max_w: float, size: int, min_size: int, max_lines: int = 2):
    """(font, lines) at the largest size where `text` fits `max_lines`; a two-sentence line
    breaks at the sentence when both halves fit - the turn gets its own line."""
    sents = _sentences(text)
    s = size
    while s >= min_size:
        f = font(s)
        if len(sents) == 2 and max_lines >= 2 and all(tlen(x, f) <= max_w for x in sents):
            return f, sents
        lines = wrap(text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        s -= 2
    f = font(min_size)
    return f, wrap(text, f, max_w)[:max_lines]


def balanced_lines(text: str, f, max_w: float) -> list:
    """One line if it fits, else the two-line split with the most even widths."""
    if tlen(text, f) <= max_w:
        return [text]
    words = text.split(" ")
    best = None
    for i in range(1, len(words)):
        l1, l2 = " ".join(words[:i]), " ".join(words[i:])
        w1, w2 = tlen(l1, f), tlen(l2, f)
        if w1 <= max_w and w2 <= max_w and (best is None or max(w1, w2) < best[0]):
            best = (max(w1, w2), [l1, l2])
    return best[1] if best else wrap(text, f, max_w)[:2]


def draw_rich(ctx, x, y, text, max_w, size, min_size, p=1.0, max_lines=2, role="curiosity",
              turn_color=None):
    """Word-by-word text; numbers carry their sign colour, the second sentence (the turn) can
    take `turn_color`. Returns `(y_below, [(word, box)])`."""
    f, lines = rich_lines(text, max_w, size, min_size, max_lines)
    space = tlen(" ", f)
    lh = int(f.size * 1.14)
    sents = _sentences(text)
    boxes = []
    for li, line in enumerate(lines):
        xx = x
        is_turn = turn_color is not None and len(sents) == 2 and li == 1 and line == sents[1]
        for w in line.split(" "):
            col = _word_color(w)
            if is_turn and col == theme.TEXT_PRIMARY:
                col = turn_color
            box = ctx.ink.text(ctx.d, (xx, y + (1 - p) * 14), w, f, alpha(col, p), role)
            boxes.append((w, box))
            xx += tlen(w, f) + space
        y += lh
    return y, boxes


# --------------------------------------------------------------------------- mini visuals
def _mini_line(s, box, series, p, color, t, marker=True, fill=True):
    area = ChartArea.fit(box, series, len(series), pad=0.1)
    hr = s.hr()
    pts = reveal_points(area, series, p)
    if len(pts) >= 2:
        if fill:
            hr.area_fill(pts, area.y1, color, top_alpha=58)
        hr.line(pts, K.a(s.pal.ink, 0.95), 3.6)
        if marker and p > 0.9:
            x, y = pts[-1]
            ring = 12 + 4 * (0.5 + 0.5 * math.sin(t * 5))
            hr.circle(x, y, 26, fill=K.a(color, 0.18))
            hr.circle(x, y, ring, outline=K.a(color, 1.0), width=2.8)
            hr.circle(x, y, 7, fill=K.a(color, 1.0))
    hr.composite(s.img)
    return pts


def _mini_event_chart(s, box, pl, p, t):
    """Price line + prior range band (or average) + the event marker, in the slip's ink."""
    series, band, ma = pl["series"], pl.get("band"), pl.get("ma")
    down = (pl.get("direction") or 0) < 0
    col = s.pal.neg if down else s.pal.pos
    n = len(series)
    vals = list(series) + (list(band) if band else []) + [v for v in (ma or []) if v is not None]
    area = ChartArea.fit(box, vals, n, pad=0.08, pad_top=0.08 if down else 0.3,
                         pad_bottom=0.3 if down else 0.08)
    hr = s.hr()
    if band:
        w = 50 if "50" in (pl.get("edge_label") or "") else 20
        x0, x1 = area.px(max(0, n - 1 - w)), area.px(n - 2)
        yt, yb = area.py(band[1]), area.py(band[0])
        pb = K.clamp(p * 1.6)
        hr.rect((x0, yt, x0 + (x1 - x0) * pb, yb), fill=K.a(s.pal.ink, 0.07))
        edge = yb if down else yt
        hr.dashed((x0, edge), (x0 + (x1 - x0) * pb, edge), K.a(col, 1.0), 3.0)
    if ma:
        mp = reveal_points(area, ma, p)
        if len(mp) >= 2:
            hr.line(mp, K.a((40, 130, 210), 0.9), 3.0)
    pts = reveal_points(area, series, p)
    if len(pts) >= 2:
        hr.area_fill(pts, area.y1, col, top_alpha=40)
        hr.line(pts, K.a(s.pal.ink, 0.95), 3.4)
    if p > 0.85 and pts:
        x, y = pts[-1]
        k = K.clamp((p - 0.85) / 0.15)
        hr.circle(x, y, 28 * k, fill=K.a(col, 0.2))
        hr.circle(x, y, (12 + 4 * math.sin(t * 5)) * k, outline=K.a(col, 1.0), width=3.0)
        hr.circle(x, y, 7 * k, fill=K.a(col, 1.0))
        tri_y = y + 34 if down else y - 34
        triangle(hr, x, tri_y, 10 * k, not down, K.a(col, 1.0))
    hr.composite(s.img)
    return area


def _mini_volume(s, box, volumes, p, spike, t, flash=True):
    vals = [v or 0.0 for v in volumes[-30:]]
    n = len(vals)
    top = max(vals) or 1.0
    x0, y0, x1, y1 = box
    hr = s.hr()
    slot = (x1 - x0) / max(n, 1)
    bw = slot * 0.62
    base = (170, 176, 196) if s.style == "paper" else theme.VOLUME_BASE
    for k, v in enumerate(vals):
        g = K.clamp(p * n - k)
        if g <= 0:
            break
        h = (y1 - y0) * v / top * g
        cx = x0 + slot * (k + 0.5)
        last = k == n - 1
        col = theme.VOLUME if last and spike > 0 else base
        if last and spike > 0 and flash:
            glow = 0.3 + 0.25 * math.sin(t * 6)
            hr.rect((cx - bw * 1.3, y1 - h - 14, cx + bw * 1.3, y1), fill=K.a(theme.VOLUME, glow * spike),
                    radius=6)
        hr.rect((cx - bw / 2, y1 - h, cx + bw / 2, y1), fill=K.a(col, 1.0), radius=min(4, bw / 2))
    hr.composite(s.img)
    return (x0 + slot * (n - 0.5), y1 - (y1 - y0) * vals[-1] / top) if vals else None


def _moon(s_or_ctx, cx, cy, r, bg, p=1.0):
    hr = HiRes((cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4))
    hr.circle(cx, cy, r, fill=K.a(K.NEON_WARM, 0.95 * p))
    hr.circle(cx + r * 0.45, cy - r * 0.3, r * 0.86, fill=K.a(bg, p))
    hr.composite(s_or_ctx.img if hasattr(s_or_ctx, "img") else s_or_ctx.layer)


def _mini_calendar(s, box, day, month, weekday=""):
    x0, y0, x1, y1 = box
    hr = s.hr()
    hr.rect(box, fill=K.a((252, 251, 247), 1.0), radius=16, outline=K.a(K.PAPER_INK, 0.25), width=1.4)
    strip = y0 + (y1 - y0) * 0.26
    hr.rect((x0, y0, x1, strip), fill=K.a(K.INK_NEG, 1.0), radius=16)
    hr.rect((x0, strip - 16, x1, strip), fill=K.a(K.INK_NEG, 1.0))
    for rx in (x0 + (x1 - x0) * 0.28, x1 - (x1 - x0) * 0.28):
        hr.circle(rx, y0 + 6, 7, fill=K.a(K.PAPER_INK, 0.8))
    hr.composite(s.img)
    cx = (x0 + x1) / 2
    fm = font(_fs(y1 - y0, 0.13))
    s.text((cx - tlen(month, fm) / 2, y0 + (strip - y0 - fm.size) / 2 - 2), month, fm,
           (255, 255, 255), "cal_month")
    fd = font(_fs(y1 - y0, 0.42))
    s.text((cx - tlen(day, fd) / 2, strip + 2), day, fd, K.PAPER_INK, "cal_day")
    if weekday:
        fw = font(_fs(y1 - y0, 0.09))
        s.text((cx - tlen(weekday, fw) / 2, y1 - fw.size - 16), weekday, fw, K.PAPER_MUTED,
               "cal_weekday")


def _mini_radar(s, cx, cy, r, t, names, p):
    hr = s.hr((cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6))
    hr.circle(cx, cy, r, fill=K.a((10, 18, 42), 0.95))
    for rr in (r, r * 0.62, r * 0.3):
        hr.circle(cx, cy, rr, outline=K.a(K.NEON, 0.4), width=1.8)
    ang = -math.pi / 2 + t * 6.0
    for k in range(16):
        aa = ang - k * 0.05
        hr.polygon([(cx, cy), (cx + r * math.cos(aa), cy + r * math.sin(aa)),
                    (cx + r * math.cos(aa - 0.05), cy + r * math.sin(aa - 0.05))],
                   K.a(K.NEON, 0.26 * (1 - k / 16)))
    for i, _ in enumerate(names):
        aa = -math.pi / 2 + 0.55 + i * 2 * math.pi / max(len(names), 1)
        rr = r * (0.55 if i % 2 else 0.8)
        ap = K.clamp(p * len(names) - i)
        if ap > 0:
            bx, by = cx + rr * math.cos(aa), cy + rr * math.sin(aa)
            hr.circle(bx, by, 14 * ap, fill=K.a(theme.POSITIVE, 0.3))
            hr.circle(bx, by, 6, fill=K.a(theme.POSITIVE, ap))
    hr.composite(s.img)


# --------------------------------------------------------------------------- card content
def _header(s, pad, label, pal, size):
    f = font(size)
    s.text((s.x0 + pad, s.y0 + pad - 4), label, f, pal.muted, "card_label")
    return s.y0 + pad - 4 + size


def content(s, kind, pl, tl, t, base=False):
    """Draw one beat's content inside Slip `s`. `base` = the full-stage first card (bigger
    figure, neon ring); otherwise the compact slip. Returns the local box of the key figure
    (for a stamp or ring), or None."""
    W, H, pal = s.w, s.h, s.pal
    pad = int(max(26, min(46, W * 0.05)))
    X0, Y0, X1, Y1 = s.box
    key = None
    if kind == "LINE":
        col = pal.signed(pl.get("positive"))
        yl = _header(s, pad, pl["label"], pal, _fs(H, 0.075 if not base else 0.06, hi=40))
        if pl.get("change") and pl.get("value"):
            fsub = font(_fs(H, 0.055, hi=30), False)
            s.text((X0 + pad, yl + 8), pl["value"], fsub, pal.muted, "card_sub")
        fig = pl.get("change") or pl.get("value", "")
        if base:
            fv = font(_fs(H, 0.25, hi=176))
            fx = X0 + (W - tlen(fig, fv)) / 2
            fy = Y0 + H * 0.17
        else:
            fv = font(_fs(H, 0.24, hi=120))
            fx = X1 - pad - tlen(fig, fv)
            fy = Y0 + pad - 10
        key = K.number_reveal(s.ctx, fx, fy, fig, fv, col, K.clamp(tl / 0.22), "card_value")
        chart = (X0 + pad, Y0 + H * (0.58 if base else 0.5), X1 - pad, Y1 - pad)
        _mini_line(s, chart, pl["series"], ease(tl / 0.45), col, t)
    elif kind == "BREAKOUT":
        col = pal.signed(pl.get("positive"))
        fsym = font(_fs(H, 0.13 if not base else 0.1, hi=84))
        s.text((X0 + pad, Y0 + pad - 8), pl["symbol"], fsym, pal.ink, "symbol")
        if pl.get("event_label"):
            fe = font(_fs(H, 0.06, hi=30), False)
            s.text((X0 + pad, Y0 + pad + fsym.size + 2), pl["event_label"], fe, pal.muted,
                   "card_sub")
        fv = font(_fs(H, 0.16 if not base else 0.13, hi=100))
        key = K.number_reveal(s.ctx, X1 - pad - tlen(pl["change"], fv), Y0 + pad - 8, pl["change"],
                              fv, col, K.clamp(tl / 0.22), "card_value")
        cb = (X0 + pad, Y0 + H * 0.42, X1 - pad - 10, Y1 - pad)
        _mini_event_chart(s, cb, pl, ease(tl / 0.4), t)
    elif kind == "VOLUME":
        fsym = font(_fs(H, 0.11, hi=70))
        s.text((X0 + pad, Y0 + pad - 8), pl["symbol"], fsym, pal.ink, "symbol")
        s.text((X1 - pad - tlen(pl.get("title", ""), font(_fs(H, 0.06, hi=28))), Y0 + pad),
               pl.get("title", ""), font(_fs(H, 0.06, hi=28)), pal.muted, "card_label")
        lab = pl["label"]
        big, _, rest = lab.partition(" ")
        fb = font(_fs(H, 0.2, hi=120))
        key = K.number_reveal(s.ctx, X0 + pad, Y0 + pad + fsym.size + 4, big, fb, theme.VOLUME,
                              K.clamp(tl / 0.22), "card_value")
        if rest:
            fr = font(_fs(H, 0.07, hi=36))
            s.text((X0 + pad + tlen(big, fb) + 16, Y0 + pad + fsym.size + 4 + fb.size - fr.size - 6),
                   rest, fr, pal.ink, "card_sub")
        _mini_volume(s, (X0 + pad, Y0 + H * 0.56, X1 - pad, Y1 - pad), pl["volumes"],
                     ease(tl / 0.3), K.clamp((tl - 0.25) / 0.15), t)
    elif kind == "MOVER_BAR":
        col = pal.signed(pl.get("positive"))
        K.pill(None, s.ctx, X0 + pad, Y0 + pad - 4, pl["tag"], col,
               (255, 255, 255) if s.style == "paper" else theme.INK, role="card_tag")
        fn = fit(pl["name"], W - 2 * pad, _fs(H, 0.15 if not base else 0.12, hi=96), min_size=36)
        s.text((X0 + pad, Y0 + pad + 44), pl["name"], fn, pal.ink, "card_name")
        fv = font(_fs(H, 0.26 if not base else 0.22, hi=150))
        key = K.number_reveal(s.ctx, X0 + pad - 4, Y0 + pad + 50 + fn.size, pl["value"], fv, col,
                              K.clamp(tl / 0.22), "card_value")
        by = Y1 - pad - 30
        g = ease(tl / 0.4)
        hr = s.hr()
        hr.rect((X0 + pad, by - 6, X1 - pad, by + 6), fill=K.a(pal.border, 0.9), radius=6)
        hr.rect((X0 + pad, by - 11, X0 + pad + (W - 2 * pad) * g, by + 11), fill=K.a(col, 1.0),
                radius=11)
        hr.composite(s.img)
        if pl.get("context"):
            s.text((X0 + pad, by + 16), pl["context"], font(_fs(H, 0.055, hi=26), False), pal.muted,
                   "card_sub")
    elif kind in ("SECTOR_TILE", "SECTOR_PAIR"):
        sides = [pl] if kind == "SECTOR_TILE" else [pl["left"], pl["right"]]
        cw = (W - pad * (len(sides) + 1)) / len(sides)
        hr = s.hr()
        for i, r in enumerate(sides):
            x = X0 + pad + i * (cw + pad)
            hr.rect((x, Y0 + pad, x + 12, Y1 - pad), fill=K.a(heatmap_cell_colour(r["numeric"]) if
                                                               s.style != "paper" else pal.signed(r["positive"]), 1.0),
                    radius=6)
        hr.composite(s.img)
        for i, r in enumerate(sides):
            x = X0 + pad + i * (cw + pad) + 30
            col = pal.signed(r["positive"])
            if r.get("tag"):
                K.pill(None, s.ctx, x, Y0 + pad, r["tag"], col,
                       (255, 255, 255) if s.style == "paper" else theme.INK, role="card_tag")
            fn = fit(r["name"], cw - 40, _fs(H, 0.14, hi=80), min_size=30)
            s.text((x, Y0 + pad + 52), r["name"], fn, pal.ink, "card_name")
            fv = fit(r["value"], cw - 40, _fs(H, 0.24 if len(sides) == 1 else 0.2, hi=140), min_size=40)
            vb = K.number_reveal(s.ctx, x, Y1 - pad - fv.size - 12, r["value"], fv, col,
                                 K.clamp((tl - 0.08 * i) / 0.22), "card_value")
            key = key or vb
        if kind == "SECTOR_PAIR":
            hr = s.hr()
            mx = X0 + W / 2
            hr.line([(mx, Y0 + pad), (mx + 3, Y0 + pad + (H - 2 * pad) * ease(tl / 0.3))],
                    K.a(K.NEON_WARM, 0.9), 3.0)
            hr.composite(s.img)
            vs = pl.get("vs_label", "")
            if vs:
                fvs = font(26)
                cy = Y0 + H / 2
                hr = s.hr()
                hr.circle(mx, cy, 30, fill=K.a(pal.fill, 1.0), outline=K.a(K.NEON_WARM, 1.0), width=3)
                hr.composite(s.img)
                s.text((mx - tlen(vs, fvs) / 2, cy - 16), vs, fvs, K.NEON_WARM if s.style != "paper"
                       else (170, 120, 0), "vs")
    elif kind == "RADAR":
        r = min(H * 0.36, W * 0.22)
        _mini_radar(s, X0 + pad + r, Y0 + H / 2, r, t, pl.get("names") or [], ease(tl / 0.5))
        fc = fit(pl.get("count_label", ""), W - 2 * r - 3 * pad, _fs(H, 0.14, hi=80), min_size=30)
        key = K.number_reveal(s.ctx, X0 + 2 * pad + 2 * r, Y0 + H / 2 - fc.size / 2,
                              pl.get("count_label", ""), fc, pal.ink, K.clamp(tl / 0.25), "card_value")
    elif kind == "FLOWS":
        rows = pl["rows"][:2]
        rh = (H - 2 * pad) / 2
        for i, r in enumerate(rows):
            y = Y0 + pad + i * rh
            col = pal.signed(r["positive"])
            s.text((X0 + pad, y), r["name"], font(_fs(H, 0.1, hi=56)), pal.ink, "card_name")
            fv = font(_fs(H, 0.13, hi=80))
            K.number_reveal(s.ctx, X1 - pad - tlen(r["value"], fv), y - 6, r["value"], fv, col,
                            K.clamp((tl - 0.1 * i) / 0.22), "card_value")
    elif kind == "CUE":
        col = pal.signed(pl.get("positive"))
        y = Y0 + pad
        if pl.get("note"):
            nx = X0 + pad
            if pl["note"] == "OVERNIGHT":        # the night motif marks overnight cues only
                _moon(s, X0 + pad + 14, y + 14, 14, pal.fill)
                nx += 40
            s.text((nx, y), pl["note"], font(_fs(H, 0.065, hi=30)), K.NEON_WARM
                   if s.style != "paper" else (150, 110, 0), "card_label")
            y += 50
        fn = fit(pl["name"], W - 2 * pad, _fs(H, 0.14, hi=90), min_size=30)
        s.text((X0 + pad, y), pl["name"], fn, pal.ink, "card_name")
        fv = font(_fs(H, 0.28 if base else 0.24, hi=170))
        vy = (y + fn.size + 40) if base else (Y1 - pad - fv.size - 12)
        key = K.number_reveal(s.ctx, X0 + pad, vy, pl["value"], fv, col, K.clamp(tl / 0.22),
                              "card_value")
    elif kind == "EVENT":
        cw = min(W * 0.34, H * 0.8)
        _mini_calendar(s, (X0 + pad, Y0 + pad, X0 + pad + cw, Y1 - pad), pl["day"], pl["month"],
                       pl.get("weekday", ""))
        x = X0 + 2 * pad + cw
        K.pill(None, s.ctx, x, Y0 + pad, pl["tag"], K.NEON if s.style != "paper" else K.PAPER_INK,
               theme.INK if s.style != "paper" else (255, 255, 255), role="card_tag")
        # A title is never cut: shrink until every line fits above the time (a truncated
        # headline can say something the original did not).
        avail = (Y1 - pad - (_fs(H, 0.08, hi=40) + 24 if pl.get("when") else 0)) - (Y0 + pad + 56)
        size = _fs(H, 0.1, hi=52)
        while True:
            ft = font(size)
            lines = wrap(pl["title"], ft, X1 - pad - x)
            if len(lines) * int(ft.size * 1.18) <= avail or size <= theme.MIN_FONT:
                break
            size -= 2
        y = Y0 + pad + 56
        for line in lines:
            s.text((x, y), line, ft, pal.ink, "event_title")
            y += int(ft.size * 1.18)
        if pl.get("when"):
            fw = font(_fs(H, 0.08, hi=40))
            key = (x, y + 10, x + tlen(pl["when"], fw), y + 10 + fw.size)
            s.text((x, y + 10), pl["when"], fw, K.NEON_WARM if s.style != "paper" else K.INK_NEG,
                   "event_when")
    elif kind == "METRIC":
        col = pal.signed(pl.get("positive"))
        s.text((X0 + pad, Y0 + pad), pl["label"], font(_fs(H, 0.075, hi=36)), pal.muted, "card_label")
        fv = fit(pl["value"], W - 2 * pad, _fs(H, 0.3 if base else 0.26, hi=170), min_size=48)
        key = K.number_reveal(s.ctx, X0 + pad - 4, Y0 + H * 0.3, pl["value"], fv, col,
                              K.clamp(tl / 0.22), "card_value")
        if pl.get("note"):
            s.text((X0 + pad, Y1 - pad - 30), pl["note"], font(_fs(H, 0.07, hi=32), False), pal.muted,
                   "card_sub")
    return key


def _polarity(beat):
    pl = beat["payload"]
    if pl.get("positive") is not None:
        return bool(pl["positive"])
    if pl.get("direction"):
        return pl["direction"] > 0
    return None


def slip_slots(beats):
    """Which slot each stacked beat (index >= 1) takes: rising facts upper-right, falling
    facts lower-left; ties alternate so two slips never share a slot."""
    out, used = {}, set()
    for i, b in enumerate(beats[1:], start=1):
        pol = _polarity(b)
        want = "up" if pol is not False else "down"
        if pol is None:
            want = "up" if "up" not in used else "down"
        if want in used:
            want = "down" if want == "up" else "up"
        used.add(want)
        out[i] = SLOT_UP if want == "up" else SLOT_DOWN
    return out


# --------------------------------------------------------------------------- beat painters
def paint_beat(ctx, i, beat, tb, t, slot=None, dim=1.0, record=True):
    """Beat `i`: the first is the full-stage base card; later beats are paper slips that snap
    into their slot. Returns the frame box of the key figure (for a stamp)."""
    kind, pl = beat["kind"], beat["payload"]
    if i == 0 or slot is None:
        W, H = STAGE[2] - STAGE[0], STAGE[3] - STAGE[1]
        s = K.Slip(W, H, style="night" if kind == "CUE" else "panel")
        key = content(s, kind, pl, tb, t, base=True)
        cx, cy = (STAGE[0] + STAGE[2]) / 2, (STAGE[1] + STAGE[3]) / 2
        hr = s.hr()
        if key and kind in ("LINE", "CUE", "MOVER_BAR", "METRIC", "SECTOR_TILE", "BREAKOUT"):
            kx0, ky0, kx1, ky1 = key
            K.neon_ring(hr, (kx0 + kx1) / 2, (ky0 + ky1) / 2, (kx1 - kx0) / 2 + 34,
                        (ky1 - ky0) / 2 + 26, ease((tb - 0.18) / 0.45), color=K.NEON, seed=3)
        elif key and kind == "EVENT":
            kx0, ky0, kx1, ky1 = key
            K.neon_ring(hr, (kx0 + kx1) / 2, (ky0 + ky1) / 2, (kx1 - kx0) / 2 + 26,
                        (ky1 - ky0) / 2 + 18, ease((tb - 0.18) / 0.45), color=K.NEON_WARM, seed=4)
        hr.composite(s.img)
        scale = 1.0 - 0.03 * (1 - dim) / 0.55 if dim < 1 else 1.0
        s.place(ctx, cx, cy, 0.0, scale, dim, shadow=0.3 * dim, record=record)
        return _to_frame(key, s, cx, cy, scale)
    sp = K.snap(tb, 0.0, 0.32)
    if sp <= 0:
        return None
    s = K.Slip(SLIP_W, SLIP_H, style="paper")
    key = content(s, kind, pl, tb, t, base=False)
    hr = s.hr()
    K.tape(hr, s.x0 + SLIP_W / 2, s.y0 + 4, 150, 34, -4 * slot["side"], 1.0)
    hr.composite(s.img)
    side = slot["side"]
    cx = slot["cx"] + side * 640 * (1 - sp)
    ang = slot["angle"] + side * 9 * (1 - sp)
    s.place(ctx, cx, slot["cy"], ang, 1.0, min(1.0, tb / 0.08) * dim, shadow=0.55 * dim,
            record=record)
    return None


def _to_frame(key, s, cx, cy, scale=1.0):
    if not key:
        return None
    ox = cx - s.img.width / 2
    oy = cy - s.img.height / 2
    return (ox + key[0], oy + key[1], ox + key[2], oy + key[3])


def _base_painter(kind):
    def paint(ctx, box, tb, t, pl):
        paint_beat(ctx, 0, {"kind": kind, "payload": pl}, tb, t)
    return paint


BEAT_PAINTERS = {k: _base_painter(k) for k in ("LINE", "SECTOR_PAIR", "SECTOR_TILE", "MOVER_BAR",
                                               "BREAKOUT", "VOLUME", "RADAR", "FLOWS", "CUE",
                                               "EVENT", "METRIC")}


# --------------------------------------------------------------------------- hero painters
def _panel(ctx, style="panel"):
    W, H = STAGE[2] - STAGE[0], STAGE[3] - STAGE[1]
    s = K.Slip(W, H, style=style)
    return s


def _place_panel(ctx, s, k=1.0):
    s.place(ctx, (STAGE[0] + STAGE[2]) / 2, (STAGE[1] + STAGE[3]) / 2, 0.0, 1.0, k, shadow=0.3,
            mark="hook_hero")


def hero_waterline(ctx, box, th, t, pl):
    """QUIET / hidden action: every flagged stock pinned as a paper tag at its own move, on one
    % scale around a 0% waterline. Nifty sits on the line, ringed in neon - the calm surface;
    the tags are what happened underneath."""
    s = _panel(ctx)
    X0, Y0, X1, Y1 = s.box
    pal = s.pal
    s.text((X0 + 34, Y0 + 26), pl.get("axis_label", ""), font(theme.T_SMALL), pal.muted, "axis")
    idx, stocks = pl["index"], pl["stocks"]
    cols = [dict(idx, is_index=True)] + list(stocks)
    maxpos = max(0.0, max(c["numeric"] for c in cols))
    maxneg = max(0.0, -min(c["numeric"] for c in cols))
    tag_h = 100
    ytop, ybot = Y0 + 70 + tag_h + 26, Y1 - 30 - tag_h - 26
    scale = (ybot - ytop) / max(maxpos + maxneg, 1e-6)
    zy = min(ytop + maxpos * scale, Y1 - 150)
    scale = min(scale, (zy - ytop) / maxpos if maxpos else scale,
                (ybot - zy) / maxneg if maxneg else scale)
    # the plot stays left of the Shorts action rail in the lower part of the stage
    left = X0 + 70
    right = X0 + (theme.RIGHT_RAIL_X - STAGE[0]) - 10
    step = (right - left) / len(cols)
    hr = s.hr()
    span = maxpos + maxneg
    grid = 1.0 if span <= 6 else (2.0 if span <= 14 else 5.0)
    g = grid
    while g <= max(maxpos, maxneg) + 1e-9:
        for yy in (zy - g * scale, zy + g * scale):
            if ytop - 40 <= yy <= ybot + 40:
                hr.line([(X0 + 30, yy), (X1 - 30, yy)], (255, 255, 255, 12), 1.2, caps=False)
        g += grid
    band = max(4.0, abs(idx["numeric"]) * scale)
    hr.rect((X0 + 30, zy - band, X1 - 30, zy + band), fill=K.a(K.NEON, 0.13))
    hr.dashed((X0 + 30, zy), (X1 - 30, zy), K.a(theme.TEXT_SECONDARY, 0.85), 2.0, dash=10, gap=8)
    tags = []
    for i, c in enumerate(cols):
        cx = left + step * (i + 0.5)
        if c.get("is_index"):
            yv = zy - c["numeric"] * scale
            hr.circle(cx, yv, 11, fill=K.a(theme.TEXT_PRIMARY, 1.0))
            K.neon_ring(hr, cx, yv, 34, 30, ease((th - 0.05) / 0.4), color=K.NEON, seed=5)
            tags.append((cx, yv, c, True, 1.0))
            continue
        gp = ease((th - 0.1 - i * 0.07) / 0.35)
        yv = zy - c["numeric"] * scale * gp
        col = signed_color(c["positive"])
        hr.line([(cx, zy), (cx, yv)], K.a(col, 0.95), 5.0)
        hr.circle(cx, yv, 30, fill=K.a(col, 0.14 * gp))
        hr.circle(cx, yv, 12, fill=K.a(col, 1.0), outline=K.a(pal.fill, 1.0), width=3)
        tags.append((cx, zy - c["numeric"] * scale, c, False, K.snap(th, 0.2 + i * 0.07, 0.3)))
    hr.composite(s.img)
    fz = font(theme.T_SMALL, False)
    s.text((X0 + 34, zy - fz.size - 10), pl.get("zero_label", ""), fz, theme.TEXT_MUTED, "axis")
    # the index label sits under the waterline, clear of the tags
    for cx, yv, c, is_idx, k in tags:
        if is_idx:
            fn, fv = font(26), font(32)
            s.text((cx - tlen(c["name"], fn) / 2, zy + 44), c["name"], fn, K.NEON, "lollipop_name")
            s.text((cx - tlen(c["value"], fv) / 2, zy + 76), c["value"], fv, theme.TEXT_PRIMARY,
                   "lollipop_value")
    _place_panel(ctx, s)
    ox, oy = STAGE[0] - s.x0, STAGE[1] - s.y0
    tw = min(step - 8, 180)
    for j, (cx, yv, c, is_idx, k) in enumerate(tags):
        if is_idx or k <= 0:
            continue
        tag = K.Slip(tw, tag_h, style="paper", radius=12)
        fn = fit(c["name"], tw - 16, 30, min_size=theme.MIN_FONT)
        fv = fit(c["value"], tw - 16, 42, min_size=theme.MIN_FONT)
        tag.text((tag.x0 + (tw - tlen(c["name"], fn)) / 2, tag.y0 + 8), c["name"], fn, K.PAPER_INK,
                 "lollipop_name")
        tag.text((tag.x0 + (tw - tlen(c["value"], fv)) / 2, tag.y0 + 12 + fn.size), c["value"], fv,
                 K.PAPER_PAL.signed(c["positive"]), "lollipop_value")
        up = c["numeric"] >= 0
        ty = yv - 22 - tag_h / 2 if up else yv + 22 + tag_h / 2
        tag.place(ctx, cx + ox, ty + oy + (1 - min(k, 1.0)) * (20 if up else -20),
                  2.2 if j % 2 else -2.2, 1.0, min(1.0, k), shadow=0.45, mark="hook_tag")


def hero_headline(ctx, box, th, t, pl):
    s = _panel(ctx)
    X0, Y0, X1, Y1 = s.box
    W = s.w
    col = signed_color(pl.get("positive"))
    fl = font(theme.T_TITLE)
    s.text((X0 + (W - tlen(pl["label"], fl)) / 2, Y0 + 30), pl["label"], fl, theme.TEXT_SECONDARY,
           "hero_label")
    fv = fit(pl["value"], W - 120, 170, min_size=96)
    vx = X0 + (W - tlen(pl["value"], fv)) / 2
    vb = K.number_reveal(s.ctx, vx, Y0 + 84, pl["value"], fv, col, K.clamp(th / 0.3), "hero_value")
    hr = s.hr()
    if vb:
        K.marker_underline(hr, vb[0] - 6, vb[2] + 6, vb[3] + 18, ease((th - 0.2) / 0.35), color=col,
                           width=6)
    chart = (X0 + 50, Y0 + 320, X1 - 50, Y1 - 130)
    c = pl.get("candles")
    last = None
    if c and c.get("close"):
        n = len(c["close"])
        area = ChartArea.fit(chart, c["high"] + c["low"], n, pad=0.08)
        draw_grid(hr, area, rows=3)
        draw_candles(hr, area, c["open"], c["high"], c["low"], c["close"], ease((th - 0.05) / 0.5))
        last = (area.px(n - 1), area.py(c["close"][-1]))
    elif pl.get("series"):
        sr = pl["series"]
        area = ChartArea.fit(chart, sr, len(sr), pad=0.1)
        draw_grid(hr, area, rows=3)
        pts = reveal_points(area, sr, ease((th - 0.05) / 0.5))
        if len(pts) >= 2:
            hr.area_fill(pts, area.y1, col, top_alpha=60)
            hr.line(pts, K.a(theme.TEXT_PRIMARY, 1.0), 4.0)
            last = pts[-1]
    if last and th > 0.45:
        K.neon_ring(hr, last[0], last[1], 26, 30, ease((th - 0.45) / 0.3), color=col, seed=6)
    hr.composite(s.img)
    if pl.get("sub"):
        s.text((X0 + 50, Y1 - 96), pl["sub"], font(theme.T_BODY), theme.TEXT_PRIMARY, "hero_sub")
    _place_panel(ctx, s)
    if pl.get("context"):
        f = fit(pl["context"], 420, 30, min_size=theme.MIN_FONT)
        lines = balanced_lines(pl["context"], f, 400)
        w = max(tlen(ln, f) for ln in lines) + 44
        h = len(lines) * int(f.size * 1.22) + 34
        slip = K.Slip(w, h, style="paper", radius=12)
        y = slip.y0 + 16
        for ln in lines:
            slip.text((slip.x0 + 22, y), ln, f, K.PAPER_INK, "hero_context")
            y += int(f.size * 1.22)
        shr = slip.hr()
        K.tape(shr, slip.x0 + w / 2, slip.y0 + 2, 90, 26, 5, 1.0)
        shr.composite(slip.img)
        k = K.snap(th, 0.5, 0.3)
        if k > 0:
            cx = min(theme.RIGHT_RAIL_X - w / 2 - 10, STAGE[2] - w / 2 - 36)
            slip.place(ctx, cx, STAGE[3] - h / 2 - 28 + (1 - min(k, 1)) * 30, 2.5, 1.0, min(1.0, k),
                       mark="hook_tag")


def hero_versus(ctx, box, th, t, pl):
    gap = 36
    W = (STAGE[2] - STAGE[0] - gap) / 2
    H = STAGE[3] - STAGE[1] - 20
    sides = (pl["left"], pl["right"])
    qual = pl.get("qualitative")
    frames = []
    for i, sd in enumerate(sides):
        s = K.Slip(W, H, style="paper")
        X0, Y0, X1, Y1 = s.box
        pad = 34
        col = K.PAPER_PAL.signed(sd.get("positive"))
        s.text((X0 + pad, Y0 + pad), sd["title"], font(theme.T_SMALL), K.PAPER_MUTED, "panel_title")
        fn = fit(sd["name"], (W - 2 * pad) * 2, 48, min_size=28)
        y = Y0 + pad + 44
        for ln in wrap(sd["name"], fn, W - 2 * pad)[:2]:
            s.text((X0 + pad, y), ln, fn, K.PAPER_INK, "panel_name")
            y += int(fn.size * 1.15)
        fv = fit(sd["value"], W - 2 * pad, 104, min_size=44)
        K.number_reveal(s.ctx, X0 + pad - 2, y + 14, sd["value"], fv, col,
                        K.clamp((th - 0.15 - 0.1 * i) / 0.25), "panel_value")
        hr = s.hr()
        cy = Y0 + H * 0.7
        cx = X0 + W / 2
        up = bool(sd.get("positive"))
        k = ease((th - 0.3 - 0.1 * i) / 0.3)
        hr.circle(cx, cy, 70 * k, fill=K.a(col, 0.12))
        triangle(hr, cx, cy, 46 * (0.6 + 0.4 * k), up, K.a(col, k))
        hr.composite(s.img)
        if sd.get("note"):
            fnote = fit(sd["note"], W - 2 * pad, theme.T_LABEL, bold=False, min_size=theme.MIN_FONT)
            s.text((X0 + pad, Y1 - pad - fnote.size - 4), sd["note"], fnote, col, "panel_note")
        sp = K.snap(th, 0.05 * i, 0.35)
        side = -1 if i == 0 else 1
        cx = STAGE[0] + W / 2 + i * (W + gap) + side * 520 * (1 - sp)
        s.place(ctx, cx, (STAGE[1] + STAGE[3]) / 2, (-2.0 if i == 0 else 2.0) + side * 7 * (1 - sp),
                1.0, min(1.0, sp * 1.5), shadow=0.55)
    mx, cy = (STAGE[0] + STAGE[2]) / 2, (STAGE[1] + STAGE[3]) / 2
    kv = ease((th - 0.35) / 0.3)
    if kv > 0:
        hr = HiRes((mx - 60, cy - 60, mx + 60, cy + 60))
        hr.circle(mx, cy, 40, fill=K.a(theme.PANEL, kv))
        K.neon_ring(hr, mx, cy, 42, 42, kv, color=K.NEON_WARM, seed=7)
        hr.composite(ctx.layer)
        vs = pl.get("vs_label", "")
        if vs:
            f = font(30)
            ctx.ink.text(ctx.d, (mx - tlen(vs, f) / 2, cy - 18), vs, f, alpha(K.NEON_WARM, kv), "vs")
    ctx.mark("hook_hero", STAGE)


def hero_stack(ctx, box, th, t, pl):
    s = _panel(ctx)
    X0, Y0, X1, Y1 = s.box
    pad = 36
    col = K.PANEL_PAL.signed(pl.get("positive"))
    fsym = font(72)
    s.text((X0 + pad, Y0 + 22), pl["symbol"], fsym, theme.TEXT_PRIMARY, "symbol")
    if pl.get("change"):
        fc = font(theme.T_TITLE + 6)
        cw = tlen(pl["change"], fc) + 44
        hr = s.hr()
        hr.rect((X1 - pad - cw, Y0 + 30, X1 - pad, Y0 + 30 + fc.size + 26), fill=K.a(col, 0.2),
                radius=(fc.size + 26) / 2, outline=K.a(col, 0.9), width=2.2)
        hr.composite(s.img)
        s.text((X1 - pad - cw + 22, Y0 + 42), pl["change"], fc, col, "change")
    if pl.get("event_label"):
        s.text((X0 + pad, Y0 + 30 + fsym.size), pl["event_label"], font(theme.T_LABEL, False),
               theme.TEXT_SECONDARY, "hero_sub")
    has_vol = bool(pl.get("volumes"))
    chart_box = (X0 + pad, Y0 + 170, X1 - pad - 20, Y0 + (420 if has_vol else 540))
    area = _mini_event_chart(s, chart_box, pl, ease(th / 0.45), t)
    if pl.get("edge_label") and pl.get("band"):
        fe = font(theme.T_SMALL)
        down = (pl.get("direction") or 0) < 0
        ey = area.py(pl["band"][0 if down else 1])
        s.text((X0 + pad + 180, ey + (8 if down else -fe.size - 10)), pl["edge_label"], fe,
               col, "edge_label")
    spike_pt = None
    if has_vol:
        vb = (X0 + pad + 330, Y0 + 452, X1 - pad - 20, Y0 + 560)
        spike_pt = _mini_volume(s, vb, pl["volumes"], ease((th - 0.1) / 0.35),
                                K.clamp((th - 0.4) / 0.2), t)
    hr = s.hr()
    px, py = area.px(len(pl["series"]) - 1), area.py(pl["series"][-1])
    K.neon_ring(hr, px, py, 34, 34, ease((th - 0.4) / 0.35), color=K.NEON, seed=8)
    hr.composite(s.img)
    xx = X0 + pad
    chips = pl.get("chips") or []
    _place_panel(ctx, s)
    ox, oy = STAGE[0] - s.x0, STAGE[1] - s.y0
    for i, c in enumerate(chips):
        pc = ease((th - 0.55 - i * 0.08) / 0.2)
        if pc > 0:
            xx = check_chip(ctx, xx + ox, STAGE[3] - 70, c, pc) - ox + 14
    if has_vol and pl.get("volume_label"):
        f = font(34)
        w = tlen(pl["volume_label"], f) + 40
        slip = K.Slip(w, 64, style="paper", radius=12)
        slip.text((slip.x0 + 20, slip.y0 + 12), pl["volume_label"], f, (150, 90, 0), "volume_label")
        k = K.snap(th, 0.45, 0.3)
        if k > 0:
            sx, sy = STAGE[0] + 36 + w / 2, STAGE[1] + 470
            slip.place(ctx, sx, sy + (1 - min(k, 1)) * 20, -2.5, 1.0, min(1.0, k), mark="hook_tag")
            if spike_pt and k >= 1:
                hr = HiRes((sx + w / 2 - 20, STAGE[1] + 420, spike_pt[0] + ox + 30, STAGE[1] + 600))
                K.marker_arrow(hr, (sx + w / 2 + 8, sy - 6), (spike_pt[0] + ox - 16, spike_pt[1] + oy - 8),
                               -30, ease((th - 0.75) / 0.25), color=theme.VOLUME, width=3.6)
                hr.composite(ctx.layer)


def hero_overnight(ctx, box, th, t, pl):
    s = _panel(ctx, style="night")
    X0, Y0, X1, Y1 = s.box
    pad = 40
    hr = s.hr()
    for i in range(18):   # a few fixed stars - night, not decoration
        sx = X0 + 40 + (i * 157) % (s.w - 80)
        sy = Y0 + 30 + (i * 83) % 220
        hr.circle(sx, sy, 1.6 + (i % 3) * 0.6, fill=K.a((200, 215, 255), 0.35 + 0.25 * math.sin(t * 2 + i)))
    hr.composite(s.img)
    lead = pl["lead"]
    col = K.NIGHT_PAL.signed(lead.get("positive"))
    _moon(s, X0 + pad + 18, Y0 + pad + 18, 18, K.NIGHT)
    s.text((X0 + pad + 50, Y0 + pad + 2), lead.get("note", ""), font(theme.T_LABEL), K.NEON_WARM,
           "cue_note")
    fn = fit(lead["name"], 560, 62, min_size=30)
    s.text((X0 + pad, Y0 + pad + 56), lead["name"], fn, theme.TEXT_PRIMARY, "cue_name")
    fv = font(150)
    vb = K.number_reveal(s.ctx, X0 + pad - 4, Y0 + pad + 70 + fn.size, lead["value"], fv, col,
                         K.clamp(th / 0.3), "cue_value")
    hr = s.hr()
    if vb:
        K.marker_underline(hr, vb[0], vb[2], vb[3] + 16, ease((th - 0.2) / 0.35), color=col, width=6)
    others = pl.get("others") or []
    ry0, ry1 = Y1 - 200, Y1 - 40
    if others:
        gw = (s.w - 2 * pad - 16 * (len(others) - 1)) / len(others)
        for i, o in enumerate(others):
            x = X0 + pad + i * (gw + 16)
            k = ease((th - 0.25 - 0.07 * i) / 0.3)
            hr.rect((x, ry0 + (1 - k) * 20, x + gw, ry1 + (1 - k) * 20), fill=K.a((20, 28, 64), k),
                    radius=16, outline=K.a((52, 66, 120), k), width=1.4)
    hr.composite(s.img)
    if others:
        for i, o in enumerate(others):
            x = X0 + pad + i * (gw + 16)
            oc = K.NIGHT_PAL.signed(o.get("positive"))
            s.text((x + 22, ry0 + 20), o["name"], fit(o["name"], gw - 40, 28, min_size=theme.MIN_FONT),
                   theme.TEXT_SECONDARY, "cue_name")
            h2 = HiRes((x + 16, ry0 + 76, x + 56, ry0 + 126))
            triangle(h2, x + 34, ry0 + 102, 12, bool(o.get("positive")), K.a(oc, 1.0))
            h2.composite(s.img)
            s.text((x + 58, ry0 + 76), o["value"], fit(o["value"], gw - 80, 50, min_size=30), oc,
                   "cue_value")
    _place_panel(ctx, s)
    gift = pl.get("gift")
    if gift:
        w, h = 330, 210
        slip = K.Slip(w, h, style="paper")
        gc = K.PAPER_PAL.signed(gift.get("positive"))
        slip.text((slip.x0 + 26, slip.y0 + 26), gift["name"], font(34), K.PAPER_INK, "cue_name")
        slip.text((slip.x0 + 26, slip.y0 + 70), gift.get("note", ""), font(theme.T_SMALL, False),
                  K.PAPER_MUTED, "cue_note")
        slip.text((slip.x0 + 26, slip.y0 + 110), gift["value"], font(72), gc, "cue_value")
        shr = slip.hr()
        K.tape(shr, slip.x0 + w / 2, slip.y0 + 2, 110, 28, -5, 1.0)
        shr.composite(slip.img)
        k = K.snap(th, 0.4, 0.32)
        if k > 0:
            slip.place(ctx, STAGE[2] - w / 2 - 30 + (1 - min(k, 1)) * 300, STAGE[1] + 70 + h / 2, 3.0,
                       1.0, min(1.0, k * 1.4), mark="hook_tag")


def hero_calendar(ctx, box, th, t, pl):
    s = _panel(ctx)
    X0, Y0, X1, Y1 = s.box
    x = X0 + 400
    K.pill(None, s.ctx, x, Y0 + 60, pl["tag"], K.NEON, theme.INK, role="hero_tag")
    ft = fit(pl["title"], (X1 - 40 - x) * 4, 52, min_size=30)
    y = Y0 + 124
    for line in wrap(pl["title"], ft, X1 - 40 - x)[:4]:
        s.text((x, y), line, ft, theme.TEXT_PRIMARY, "event_title")
        y += int(ft.size * 1.18)
    hr = s.hr()
    if pl.get("when"):
        fw = font(44)
        s.text((x, y + 26), pl["when"], fw, K.NEON_WARM, "event_when")
        K.neon_ring(hr, x + tlen(pl["when"], fw) / 2, y + 26 + fw.size / 2 + 4, tlen(pl["when"], fw) / 2 + 30,
                    fw.size / 2 + 22, ease((th - 0.3) / 0.4), color=K.NEON_WARM, seed=9)
    hr.composite(s.img)
    if pl.get("context"):
        fc = font(theme.T_LABEL)
        w = tlen(pl["context"], fc) + 40
        hr = s.hr()
        hr.rect((X0 + 40, Y1 - 96, X0 + 40 + w, Y1 - 96 + fc.size + 26), fill=K.a(theme.SURFACE, 1.0),
                radius=(fc.size + 26) / 2, outline=K.a(theme.PANEL_BORDER, 1.0), width=1.5)
        hr.composite(s.img)
        s.text((X0 + 60, Y1 - 85), pl["context"], fc, theme.TEXT_SECONDARY, "hero_context")
    _place_panel(ctx, s)
    cal = K.Slip(300, 360, style="paper")
    _mini_calendar(cal, (cal.x0 + 18, cal.y0 + 18, cal.x1 - 18, cal.y1 - 18), pl["day"], pl["month"],
                   pl.get("weekday", ""))
    chr_ = cal.hr()
    K.tape(chr_, cal.x0 + 150, cal.y0 + 2, 120, 30, -6, 1.0)
    chr_.composite(cal.img)
    k = K.snap(th, 0.0, 0.35)
    cal.place(ctx, STAGE[0] + 40 + 150, STAGE[1] + 60 + 180 - (1 - min(k, 1)) * 60, -4.0 + 6 * (1 - k),
              1.0, min(1.0, k * 1.5), mark="hook_tag")


def hero_numbered(ctx, box, th, t, pl):
    items = pl["items"][:3]
    gap = 22
    h = (STAGE[3] - STAGE[1] - gap * (len(items) - 1)) / max(len(items), 1)
    w = theme.RIGHT_RAIL_X - STAGE[0] - 24
    for i, it in enumerate(items):
        s = K.Slip(w, h - 8, style="paper")
        X0, Y0, X1, Y1 = s.box
        col = K.PAPER_PAL.signed(it.get("positive"))
        n = it.get("n") or ""
        fnum = font(int(min(96, h * 0.55)))
        s.text((X0 + 30, Y0 + (s.h - fnum.size) / 2 - 8), n.zfill(2) if n else "", fnum,
               (40, 130, 210), "list_n")
        x = X0 + 44 + tlen("00", fnum)
        fl = fit(it["label"], X1 - 30 - x, theme.T_LABEL + 2, min_size=theme.MIN_FONT)
        s.text((x, Y0 + 26), it["label"], fl, K.PAPER_MUTED, "list_label")
        width = X1 - 30 - x
        fv = fit(it["value"], width, 64, min_size=34)
        if tlen(it["value"], fv) <= width:
            s.text((x, Y0 + 30 + fl.size + 6), it["value"], fv, col, "list_value")
        else:
            ft = fit(it["value"], width * 2, theme.T_BODY, min_size=theme.MIN_FONT + 2)
            ly = Y0 + 30 + fl.size + 6
            for line in balanced_lines(it["value"], ft, width):
                s.text((x, ly), line, ft, K.PAPER_INK, "list_value")
                ly += int(ft.size * 1.2)
        sp = K.snap(th, 0.1 * i, 0.32)
        side = -1 if i % 2 == 0 else 1
        cx = STAGE[0] + 12 + w / 2 + side * 500 * (1 - sp)
        s.place(ctx, cx, STAGE[1] + i * (h + gap) + h / 2, (-1.2 if i % 2 == 0 else 1.2) * (1 + 3 * (1 - sp)),
                1.0, min(1.0, sp * 1.5), shadow=0.5)
    ctx.mark("hook_hero", STAGE)


HERO_PAINTERS = {
    "DEPTH_LOLLIPOP": hero_waterline, "HEADLINE_NUMBER": hero_headline,
    "VERSUS_SPLIT": hero_versus, "SIGNAL_STACK_CHART": hero_stack,
    "OVERNIGHT_BOARD": hero_overnight, "EVENT_CALENDAR": hero_calendar,
    "NUMBERED_LIST": hero_numbered,
}


# --------------------------------------------------------------------------- the scene
class DynamicHookScene(Scene):
    """`spec.data`: {"beats": [{"id","kind","payload"}], "hero": {"kind","payload"},
    "timing": {...}}. `spec.texts`: eyebrow, curiosity, summary (+ label/body), stamps (one per
    beat, may be empty) and every beat/hero string (declared for the content scan)."""

    def paint(self, ctx, t):
        s = self.spec
        dt_ = s.data
        tm = dt_["timing"]
        b, settle = tm["beat_seconds"], tm["settle_start"]
        rec = ctx.ink.rec
        self._top(ctx, t)
        beats = dt_["beats"]
        if beats and t < settle + 0.02:
            self._collage(ctx, t, b, settle, beats, rec)
            self._pips(ctx, t, b, len(beats), settle)
        if t >= settle - 0.02:
            th = max(0.0, t - settle)
            k = ease(min(1.0, (t - settle + 0.02) / 0.22))
            layer = new_layer()
            hctx = Ctx(layer, rec if k >= 0.999 else None)
            HERO_PAINTERS[dt_["hero"]["kind"]](hctx, STAGE, th, t, dt_["hero"]["payload"])
            hctx.flush()
            if k < 0.999:
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * k)))
            ctx.layer.alpha_composite(layer, (0, int((1 - k) * 18)))
            self._bottom(ctx, th)

    def _collage(self, ctx, t, b, settle, beats, rec):
        """Beats accumulate: the base card, then slips pinned on top of it. Everything clears
        together just before the hero settles."""
        stamps = self.spec.texts.get("stamps") or []
        exit_k = 1.0 - ease(max(0.0, (t - settle + 0.16) / 0.16))   # clears by settle
        if exit_k <= 0:
            return
        layer = new_layer()
        lctx = Ctx(layer, rec if exit_k >= 0.999 else None)
        slots = slip_slots(beats)
        cur = min(int(t / b), len(beats) - 1) if b > 0 else 0
        base_dim = 1.0 - 0.5 * ease((t - b) / 0.25) if len(beats) > 1 else 1.0
        # frame 0 opens part-way into the first card's own animation: its figure is up already
        key = paint_beat(lctx, 0, beats[0], t + 0.25, t, dim=base_dim)

        def _stamp(i):
            txt = stamps[i] if i < len(stamps) else ""
            if not txt:
                return
            ps = ease((t - i * b - 0.32) / 0.18)
            if i == 0 and key:
                # pinned to the first figure; later slips cover it, like paper on a board
                sx, sy, ang = min(key[2] + 40, STAGE[2] - 170), key[1] - 20, -6.0
            else:
                # the free corner of the collage (the slots leave lower-right open), kept
                # left of the Shorts action rail
                sx, sy, ang = theme.RIGHT_RAIL_X - 135, STAGE[3] - 64, -4.0
            dim = base_dim if i == 0 else 1.0
            K.stamp(lctx, txt, sx, sy, ps * dim, color=K.NEON_WARM, angle=ang, fill=(12, 16, 36))

        _stamp(0)
        for i in range(1, cur + 1):
            older = 1.0 - 0.18 * ease((t - (i + 1) * b) / 0.2) if i < cur else 1.0
            paint_beat(lctx, i, beats[i], t - i * b, t, slot=slots[i], dim=older)
            _stamp(i)
        lctx.flush()
        if exit_k < 0.999:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * exit_k)))
        ctx.layer.alpha_composite(layer, (0, int((1 - exit_k) * -24)))

    # ------------------------------------------------------------------ fixed layers
    def _top(self, ctx, t):
        tx = self.spec.texts
        f = font(theme.T_LABEL)
        x = theme.X0
        hr = HiRes((x, EYEBROW_Y + 4, x + 24, EYEBROW_Y + 30))
        pulse = 0.6 + 0.4 * math.sin(t * 5)
        hr.circle(x + 9, EYEBROW_Y + 17, 8, fill=alpha(theme.BRAND, 1.0))
        hr.circle(x + 9, EYEBROW_Y + 17, 12, outline=alpha(theme.BRAND, 0.5 * pulse), width=2)
        hr.composite(ctx.layer)
        ctx.ink.text(ctx.d, (x + 32, EYEBROW_Y), tx["eyebrow"], f, theme.BRAND, "eyebrow")
        # Fully legible on frame 0 - the feed autoplays from it - with a small upward settle.
        _, boxes = draw_rich(ctx, theme.X0, CURIOSITY_Y - 10 * (1 - ease(t / 0.35)), tx["curiosity"],
                             theme.CONTENT_W, CURIOSITY_SIZE, CURIOSITY_MIN, p=1.0,
                             turn_color=theme.BRAND)
        # the curiosity line's first figure gets the neon marker underline as it draws
        fig = next(((w, bx) for w, bx in boxes if _NUM.match(w)), None)
        if fig:
            w, bx = fig
            col = _word_color(w)
            x1 = bx[2] - (tlen(w[-1], font(CURIOSITY_SIZE)) * 0.9 if w[-1] in ".,:;" else 0)
            hu = HiRes((bx[0] - 20, bx[3] - 10, x1 + 20, bx[3] + 30))
            K.marker_underline(hu, bx[0] - 4, x1 + 2, bx[3] + 8, ease((t - 0.15) / 0.4), color=col,
                               width=5.0)
            hu.composite(ctx.layer)

    def _bottom(self, ctx, th):
        """Settled frame: one summary line under a label chip - no agenda chips, nothing else
        competing with the curiosity line and the hero."""
        tx = self.spec.texts
        p = phase(th, 0.12, 0.3)
        if p <= 0:
            return
        label = tx.get("summary_label") or ""
        body = tx.get("summary_body") or tx["summary"]
        y = SUMMARY_Y + (1 - p) * 10
        if label:
            fl = font(theme.T_SMALL)
            w = tlen(label, fl) + 30
            h = theme.T_SMALL + 16
            hr = HiRes((theme.X0, y, theme.X0 + w, y + h))
            hr.rect((theme.X0, y, theme.X0 + w, y + h), fill=alpha(theme.BRAND, p), radius=h / 2)
            hr.composite(ctx.layer)
            ctx.ink.text(ctx.d, (theme.X0 + 15, y + 7), label, fl, alpha(theme.INK, p), "summary_label")
            y += h + 14
        max_w = theme.RIGHT_RAIL_X - theme.X0
        fb = fit(body, max_w * 2, theme.T_BODY + 4, bold=False, min_size=theme.T_LABEL + 2)
        for line in balanced_lines(body, fb, max_w):
            ctx.ink.text(ctx.d, (theme.X0, y), line, fb, alpha(theme.TEXT_PRIMARY, p), "summary")
            y += int(fb.size * 1.24)

    def _pips(self, ctx, t, b, n, settle):
        k = 1.0 - ease(max(0.0, (t - settle + 0.1) / 0.2))
        if k <= 0 or n < 2:
            return
        w, gap = 64, 12
        x = (theme.CANVAS_W - (n * w + (n - 1) * gap)) / 2
        hr = HiRes((x - 4, PIPS_Y - 6, x + n * (w + gap) + 4, PIPS_Y + 12))
        for i in range(n):
            frac = max(0.0, min(1.0, (t - i * b) / b))
            hr.rect((x, PIPS_Y, x + w, PIPS_Y + 6), fill=alpha(theme.PANEL_BORDER, k), radius=3)
            if frac > 0:
                hr.rect((x, PIPS_Y, x + max(6, w * frac), PIPS_Y + 6), fill=alpha(theme.BRAND, k),
                        radius=3)
            x += w + gap
        hr.composite(ctx.layer)


__all__ = ["DynamicHookScene", "BEAT_PAINTERS", "HERO_PAINTERS", "STAGE", "draw_rich",
           "rich_lines", "balanced_lines", "paint_beat", "slip_slots", "content"]

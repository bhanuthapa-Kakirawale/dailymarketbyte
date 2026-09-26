"""Animated 1080x1920 renderer: Pillow draws each frame, frames are piped into ffmpeg."""
import glob
import math
import os
import subprocess
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import (W, H, FPS, ASSETS_DIR, BG1, BG2, CARD, CARD_ACTIVE, TEXT, SUB,
                    ACCENT, GREEN, RED, YELLOW, MUSIC_VOLUME, fmt_in)
from editorial import HEATMAP_CLIP_PCT, HEATMAP_COLUMNS, HEATMAP_MAX_BG_MIX, SceneType

# Ranked-mover scan list (Top 5 Gainers / Top 5 Losers): how far a row's magnitude bar may
# extend, in pixels, reserved at the right edge of each row alongside the printed percentage.
RANKED_MOVER_BAR_W = 220

# Layout (inside the Shorts safe zone: bottom ~20% and right edge are covered by YouTube UI)
X0, X1 = 50, 1030
TICK_Y = 288
LINE_Y = 340
SECTION_Y = 344
TOP = 470
LAYER_H = 1210
CAP_BOX = (40, 1215, 1040, 1475)
DISC_Y = 1494

# ----------------------------------------------------------------------------- fonts
_BOLD = sorted(glob.glob(os.path.join(ASSETS_DIR, "fonts", "*Bold*.ttf"))) + [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf"]
_REG = sorted(f for f in glob.glob(os.path.join(ASSETS_DIR, "fonts", "*.ttf")) if "Bold" not in f) + [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]
_fc = {}


def font(size, bold=True):
    key = (int(size), bold)
    if key not in _fc:
        for p in (_BOLD if bold else _REG):
            if os.path.exists(p):
                _fc[key] = ImageFont.truetype(p, int(size))
                break
        else:
            _fc[key] = ImageFont.load_default(size=int(size))
    return _fc[key]


def _has_glyph(f, ch):
    try:
        return bytes(f.getmask(ch)) != bytes(f.getmask("\ue000"))
    except Exception:
        return False


RS = "₹" if _has_glyph(font(40), "₹") else "Rs "


def tlen(text, f):
    return f.getlength(text)


def fit(text, maxw, size, bold=True, min_size=22):
    while size > min_size and tlen(text, font(size, bold)) > maxw:
        size -= 2
    return font(size, bold)


def ellipsize(text, f, maxw):
    if tlen(text, f) <= maxw:
        return text
    while text and tlen(text + "...", f) > maxw:
        text = text[:-1]
    return text.rstrip() + "..."


def wrap(text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if tlen(t, f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def triangle(d, x, y, s, up, color):
    if up:
        d.polygon([(x, y + s), (x + s, y + s), (x + s / 2, y)], fill=color)
    else:
        d.polygon([(x, y), (x + s, y), (x + s / 2, y + s)], fill=color)


def pct_color(p):
    return GREEN if p >= 0 else RED


def mix(a, b, k):
    return tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def fmt_val(v, dec=0, prefix=""):
    return prefix + fmt_in(v, dec)


# ----------------------------------------------------------------------------- background, header, ticker
class Backdrop:
    """Slow drifting gradient + floating particles, so no frame is ever static."""
    def __init__(self, seed=5):
        self.extra = 360
        hh = H + self.extra
        g = np.linspace(0, 1, hh)[:, None, None]
        arr = (np.array(BG1) * (1 - g) + np.array(BG2) * g) * np.ones((1, W, 1))
        yy, xx = np.mgrid[0:hh, 0:W]
        for cx, cy, sx, sy, col in [(900, 300, 520, 420, (20, 50, 70)), (150, 1600, 620, 520, (40, 18, 70))]:
            arr += np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))[..., None] * np.array(col) * 0.7
        self.img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
        rng = np.random.default_rng(seed)
        self.p = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1.5, 4.0), rng.uniform(8, 28),
                   rng.uniform(0, 6.28)) for _ in range(42)]

    def frame(self, t):
        off = int(self.extra / 2 * (1 + math.sin(2 * math.pi * t / 18)))
        im = self.img.crop((0, off, W, off + H))
        d = ImageDraw.Draw(im)
        for x, y, r, sp, ph in self.p:
            yy = (y - sp * t) % H
            xx = x + 14 * math.sin(t * 0.5 + ph)
            c = 0.5 + 0.5 * math.sin(t * 1.3 + ph)
            d.ellipse((xx - r, yy - r, xx + r, yy + r), fill=mix((30, 45, 85), (80, 140, 190), c))
        return im


def header_layer(info, demo=False):
    img = Image.new("RGBA", (W, LINE_Y + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(74)
    d.text((62, 74), "DAILY MARKET", font=f, fill=(0, 0, 0, 90))
    d.text((62 + tlen("DAILY MARKET ", f), 74), "BYTE", font=f, fill=(0, 0, 0, 90))
    d.text((60, 72), "DAILY MARKET", font=f, fill=TEXT)
    d.text((60 + tlen("DAILY MARKET ", f), 72), "BYTE", font=f, fill=ACCENT)
    d.text((60, 182), info["today_str"], font=font(44), fill=YELLOW)
    d.text((60, 236), f"Recap of {info['recap_str']} session", font=font(30, False), fill=SUB)
    if demo:
        txt = "DEMO DATA - NOT REAL"
        fw = tlen(txt, font(30))
        d.rounded_rectangle((X1 - fw - 36, 228, X1, 274), 14, fill=(220, 30, 40))
        d.text((X1 - fw - 18, 234), txt, font=font(30), fill=(255, 255, 255))
    return img


class Ticker:
    def __init__(self, items):
        f = font(28)
        tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        segs, w = [], 30
        for label, val, pct in items:
            a, b = f"{label} {val} ", f"{pct:+.2f}%"
            segs.append((w, a, b, pct))
            w += tlen(a, f) + tlen(b, f) + 70
        self.w = max(int(w), W)
        strip = Image.new("RGB", (self.w * 2, 50), (12, 18, 40))
        d = ImageDraw.Draw(strip)
        for rep in (0, self.w):
            for x, a, b, pct in segs:
                d.text((rep + x, 9), a, font=f, fill=TEXT)
                d.text((rep + x + tlen(a, f), 9), b, font=f, fill=pct_color(pct))
                d.ellipse((rep + x - 42, 21, rep + x - 34, 29), fill=ACCENT)
        self.strip = strip

    def paste(self, frame, t):
        off = int(t * 95) % self.w
        frame.paste(self.strip.crop((off, 0, off + W, 50)), (0, TICK_Y))


# ----------------------------------------------------------------------------- captions
_cap_cache = {}


def caption_layer(text, frac):
    words = text.split()
    n = len(words)
    shown = max(1, min(n, math.ceil(n * min(1.0, frac / 0.6))))
    key = (text, shown)
    if key in _cap_cache:
        return _cap_cache[key]
    bw, bh = CAP_BOX[2] - CAP_BOX[0], CAP_BOX[3] - CAP_BOX[1]
    for size in (54, 48, 44, 40, 36, 32):
        f = font(size)
        lines = wrap(text, f, bw - 90)
        if len(lines) <= 3:
            break
    lines = lines[:3]
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, bw - 1, bh - 1), 28, fill=(4, 7, 18, 215), outline=(60, 80, 130, 255), width=2)
    d.rounded_rectangle((0, 0, 12, bh - 1), 6, fill=ACCENT + (255,))
    lh = int(size * 1.22)
    y = (bh - lh * len(lines)) // 2
    idx = 0
    for line in lines:
        x = (bw - tlen(line, f)) / 2
        for w in line.split():
            if idx < shown:
                d.text((x, y), w, font=f, fill=YELLOW if (idx == shown - 1 and shown < n) else TEXT)
            x += tlen(w + " ", f)
            idx += 1
        y += lh
    _cap_cache[key] = img
    return img


# ----------------------------------------------------------------------------- scenes
class Scene:
    """One scene. `show_ticker` is per-scene so a viewer reading a number is not also being
    asked to track a scrolling strip - competing motion during reading time is the cheapest
    thing to remove."""
    show_ticker = True

    def __init__(self, dur, texts):
        self.dur, self.texts = dur, texts
        self._cache = OrderedDict()

    def captions(self):
        k = len(self.texts)
        if not k:
            return []
        return [(i * self.dur / k, self.dur / k, c) for i, c in enumerate(self.texts)]

    def caption(self, t):
        for s, d, txt in self.captions():
            if s <= t < s + d:
                return txt, (t - s) / d
        return None

    def key(self, t):
        return 0

    def draw(self, d, L, t):
        pass

    def layer(self, t):
        k = self.key(t)
        if k in self._cache:
            self._cache.move_to_end(k)
            return self._cache[k]
        L = Image.new("RGBA", (W, LAYER_H), (0, 0, 0, 0))
        self.draw(ImageDraw.Draw(L), L, t)
        self._cache[k] = L
        if len(self._cache) > 14:
            self._cache.popitem(last=False)
        return L

    def section(self, d, title, color, sub=None):
        d.text((60, SECTION_Y), title, font=font(62), fill=color)
        if sub:
            d.text((62, SECTION_Y + 76), sub, font=font(30, False), fill=SUB)


def anim_key(t, until):
    return int(min(t, until) * FPS)


class HookScene(Scene):
    """The opening three seconds: one dominant fact, branding kept small and secondary.

    A viewer scrolling past does not care what the channel is called yet - they care whether
    anything happened. So the number is the biggest thing on screen; the wordmark is left to
    the header, where it already appears on every frame.
    """
    show_ticker = True

    def __init__(self, plan, dur=3.0):
        super().__init__(dur, [])
        self.plan = plan

    def key(self, t):
        return anim_key(t, 1.0)

    def draw(self, d, L, t):
        p = self.plan
        e = ease(t / 0.55)
        colour = TEXT if p.primary_positive is None else pct_color(1 if p.primary_positive else -1)

        label = p.primary_text
        if label:
            fl = fit(label, 960, 66)
            d.text(((W - tlen(label, fl)) / 2, 470 - 30 * (1 - e)), label, font=fl, fill=SUB)

        if p.primary_value:
            fv = fit(p.primary_value, 980, 168, min_size=96)
            d.text(((W - tlen(p.primary_value, fv)) / 2, 560), p.primary_value, font=fv, fill=colour)

        if p.secondary_text:
            fs = fit(p.secondary_text, 940, 50, min_size=34)
            lines = wrap(p.secondary_text, fs, 940)[:2]
            y = 790
            for line in lines:
                d.text(((W - tlen(line, fs)) / 2, y), line, font=fs, fill=TEXT)
                y += int(fs.size * 1.25)

        # No wordmark here: `render` already draws it across the top of every frame, so a
        # second copy was both redundant and a hardcoded string the plan never saw - outside
        # `public_text()`, and so outside the content scan and the reading budget.


BAND_BOTTOM = 1195   # bottom of the safe zone content sits above (see CLAUDE.md safe-zone note)


# ----------------------------------------------------------------------------- heatmap grid
def heatmap_intensity(pct) -> float:
    """Deterministic colour intensity in [-1, 1] for one heatmap cell's percent move.

    The move is CLIPPED to +/-HEATMAP_CLIP_PCT before scaling, not compressed - clipping
    keeps the mapping linear and easy to reason about, and stops a single outlier cell from
    washing every other cell's colour toward a uniform pale shade. A move this large is rare;
    when it happens, that cell simply reads as fully saturated rather than stretching the
    whole grid's scale around it. No randomness, no model - the same input always maps to the
    same intensity.
    """
    if pct is None:
        return 0.0
    clipped = max(-HEATMAP_CLIP_PCT, min(HEATMAP_CLIP_PCT, float(pct)))
    return clipped / HEATMAP_CLIP_PCT


def heatmap_cell_colour(pct) -> tuple:
    """A cell's background: neutral CARD blended toward GREEN/RED by `heatmap_intensity`,
    capped at HEATMAP_MAX_BG_MIX so even the most extreme cell keeps enough contrast for its
    text to stay readable. The background carries magnitude; it is never the only carrier of
    sign - the cell's percentage text is always drawn too, with its own +/- sign."""
    intensity = heatmap_intensity(pct)
    if intensity >= 0:
        return mix(CARD, GREEN, intensity * HEATMAP_MAX_BG_MIX)
    return mix(CARD, RED, -intensity * HEATMAP_MAX_BG_MIX)


def draw_heatmap_grid(d, items, x0, x1, top, bottom, t, columns=HEATMAP_COLUMNS,
                      max_row_h=190, gap=14):
    """A reusable scan grid: `columns`-wide, rows sized from item count, each cell coloured by
    `heatmap_cell_colour` and always labelled with its name and signed percentage.

    Built generically over anything shaped like an `EditorialItem` (title/value/numeric/
    positive) rather than tied to sectors, so it is a genuinely reusable component - any
    small, glanceable set of labelled percentages can use it. Items are drawn in the order
    given (row-major, left to right, top to bottom); the caller decides that order - here,
    sectors are already sorted strongest to weakest, so the grid reads the same direction a
    narrative list would, just all at once instead of one row at a time.

    No explanatory text is drawn per cell - a name and a signed percentage, nothing else -
    text density stays low regardless of how many cells are shown.
    """
    n = len(items)
    if n == 0:
        return
    rows = math.ceil(n / columns)
    cell_w = (x1 - x0 - gap * (columns - 1)) / columns
    row_h = min(max_row_h, (bottom - top - gap * (rows - 1)) / rows)
    row_h = max(row_h, 100)

    for i, item in enumerate(items):
        row, col = divmod(i, columns)
        e = ease((t - col * 0.05 - row * 0.12) / 0.4)
        if e <= 0:
            continue
        dy = (1 - e) * 24

        cx0 = x0 + col * (cell_w + gap)
        cy0 = top + row * (row_h + gap)
        cx1, cy1 = cx0 + cell_w, cy0 + row_h
        if cy1 > bottom + row_h:      # a stray extra row would run off the safe zone
            continue

        bg = heatmap_cell_colour(item.numeric)
        value_colour = TEXT if item.positive is None else pct_color(1 if item.positive else -1)

        d.rounded_rectangle((cx0, cy0 + dy, cx1, cy1 + dy), 14, fill=bg)

        fn = fit(item.title, cell_w - 24, 30, min_size=18)
        d.text((cx0 + 14, cy0 + dy + 14), item.title, font=fn, fill=TEXT)

        fv = fit(item.value, cell_w - 24, 42, min_size=22)
        d.text((cx0 + 14, cy0 + dy + row_h - fv.size - 16), item.value, font=fv,
               fill=value_colour)


def draw_global_grid(d, items, x0, x1, top, bottom, t, gap=20):
    """A compact scan grid for GLOBAL cues - up to 4 cells, name + signed percentage, nothing
    else. Balanced by count rather than forcing a fixed column count: 2 or 3 cues get one row
    that wide (a clean 2-up or 3-up layout), 4 get a 2x2 grid, so there is never a half-empty
    row. Unlike `draw_heatmap_grid` the cell background stays neutral (CARD) with a coloured
    accent stripe, matching GLOBAL's existing card look - only the count and layout changed,
    not the visual language; positive/negative is still carried by both colour and the +/-
    sign already in the printed value, never colour alone.
    """
    n = len(items)
    if n == 0:
        return
    columns = n if n <= 3 else 2
    rows = math.ceil(n / columns)
    cell_w = (x1 - x0 - gap * (columns - 1)) / columns
    cell_h = min(230, (bottom - top - gap * (rows - 1)) / rows)
    cell_h = max(cell_h, 150)

    for i, item in enumerate(items):
        row, col = divmod(i, columns)
        e = ease((t - col * 0.06 - row * 0.14) / 0.4)
        if e <= 0:
            continue
        dy = (1 - e) * 26

        cx0 = x0 + col * (cell_w + gap)
        cy0 = top + row * (cell_h + gap)
        cx1, cy1 = cx0 + cell_w, cy0 + cell_h
        if cy1 > bottom + cell_h:
            continue

        colour = TEXT if item.positive is None else pct_color(1 if item.positive else -1)

        d.rounded_rectangle((cx0, cy0 + dy, cx1, cy1 + dy), 22, fill=CARD)
        d.rounded_rectangle((cx0, cy0 + dy, cx1, cy0 + dy + 6), 4, fill=colour)

        ft = fit(item.title, cell_w - 44, 40, min_size=22)
        d.text((cx0 + 22, cy0 + dy + 26), item.title, font=ft, fill=TEXT)

        fv = fit(item.value, cell_w - 44, 66, min_size=32)
        d.text((cx0 + 22, cy0 + dy + cell_h - fv.size - 26), item.value, font=fv, fill=colour)


def draw_ranked_mover_list(d, items, x0, x1, top, bottom, t, max_abs_pct=None, columns=1,
                           max_row_h=180, gap=16, bar_w=RANKED_MOVER_BAR_W):
    """A reusable ranked-mover scan list: rank, symbol and signed percentage, plus a
    deterministic magnitude bar - the same "many small facts scanned at once" grammar as
    `draw_heatmap_grid`, built generically over anything shaped like an `EditorialItem`
    (title/value/rank/numeric/positive) rather than tied to gainers or losers specifically.

    Rows are drawn in the order given - the caller (`gainers_scene`/`losers_scene`) has
    already ranked them, largest move first - top to bottom, one row per item.

    The bar is a SECONDARY encoding: its length is `abs(item.numeric) / max_abs_pct` (the
    largest move actually shown in this scene, so the scale is always local and deterministic
    for the same items), scaled within the reserved `bar_w` pixels. The percentage itself is
    always printed too, and its sign is always in the text - direction is never conveyed by
    colour alone.
    """
    n = len(items)
    if n == 0:
        return
    row_h = min(max_row_h, (bottom - top - gap * (n - 1)) / n)
    row_h = max(row_h, 92)
    max_abs = max_abs_pct if max_abs_pct else max((abs(i.numeric or 0) for i in items), default=1.0)
    max_abs = max_abs or 1.0

    for i, item in enumerate(items):
        e = ease((t - i * 0.12) / 0.4)
        if e <= 0:
            continue
        dx = -(1 - e) * 90
        y0 = top + i * (row_h + gap)
        y1 = y0 + row_h
        if y1 > bottom + row_h:
            continue

        colour = TEXT if item.positive is None else pct_color(1 if item.positive else -1)

        d.rounded_rectangle((x0 + dx, y0, x1 + dx, y1), 18, fill=CARD)
        d.rounded_rectangle((x0 + dx, y0, x0 + 8 + dx, y1), 4, fill=colour)

        chip = min(row_h - 24, 58)
        cy = y0 + (row_h - chip) / 2
        cx = x0 + dx + 26
        d.rounded_rectangle((cx, cy, cx + chip, cy + chip), chip / 2, fill=ACCENT)
        rank_text = str(item.rank or (i + 1))
        fr = fit(rank_text, chip - 12, 34, min_size=18)
        d.text((cx + (chip - tlen(rank_text, fr)) / 2, cy + (chip - fr.size) / 2 - 2),
              rank_text, font=fr, fill=(10, 14, 30))

        value_font = fit(item.value, 150, 46, min_size=26) if item.value else None
        value_w = (tlen(item.value, value_font) + 24) if value_font else 0
        bar_x1 = x1 + dx
        bar_x0 = bar_x1 - bar_w
        name_x0 = cx + chip + 24
        name_x1 = bar_x0 - value_w - 24

        fn = fit(item.title, max(20, name_x1 - name_x0), 46, min_size=24)
        d.text((name_x0, y0 + (row_h - fn.size) / 2), item.title, font=fn, fill=TEXT)

        if value_font:
            d.text((bar_x0 - value_w + 12, y0 + (row_h - value_font.size) / 2), item.value,
                  font=value_font, fill=colour)

        bar_h = min(24, row_h * 0.22)
        by = y0 + (row_h - bar_h) / 2
        d.rounded_rectangle((bar_x0, by, bar_x1, by + bar_h), bar_h / 2, fill=mix(CARD, colour, 0.18))
        frac = max(0.0, min(1.0, abs(item.numeric or 0) / max_abs))
        filled = max(bar_h, bar_w * frac) if frac > 0 else 0
        if filled > 0:
            d.rounded_rectangle((bar_x0, by, bar_x0 + filled, by + bar_h), bar_h / 2, fill=colour)


def _distribute(n, heights, top, bottom, gap=22, max_gap=None):
    """Y positions for `n` stacked blocks of the given heights, spreading any leftover room
    in the band as extra gap between them (and a little above the first one) instead of
    leaving it all as dead space below the last block.

    This is deliberately not "vertically centre the stack": the first block still starts
    close to the section heading, so the heading and its content keep reading as one group.

    `max_gap` caps how much of that leftover becomes gap between items - unset it behaves as
    before; capped, anything past the cap is simply left as trailing space below the last
    block instead of stretching two cards apart into a canyon.
    """
    if n == 0:
        return []
    total = sum(heights) + gap * max(n - 1, 0)
    extra = max(0.0, (bottom - top) - total)
    lead = extra * 0.12 if n == 1 else 0.0       # a single block gets a little air above it
    extra_gap = (extra - lead) / max(n - 1, 1) if n > 1 else 0.0
    if max_gap is not None:
        extra_gap = min(extra_gap, max_gap)
    ys, y = [], top + lead
    for h in heights:
        ys.append(y)
        y += h + gap + extra_gap
    return ys


class RowsScene(Scene):
    """Draws a ScenePlan's items. The layout is chosen by scene type, but every layout draws
    exactly the items the plan already selected - low information density, higher visual
    density: the same small set of facts is made to occupy more of the usable canvas through
    size, spacing and hierarchy, never through more words or more cards.

    SECTORS gets a strongest-vs-weakest side-by-side comparison, or - once there are enough
    sectors for it to be worth it - a scannable heatmap grid (`draw_heatmap_grid`); GAINERS
    and LOSERS get a ranked scan list (`draw_ranked_mover_list`); GLOBAL gets a compact scan
    grid (`draw_global_grid`, 2-4 cells, balanced by count); MOVERS and FLOWS get large
    stacked panels; CONTEXT and EVENTS keep the card style but spread their (still capped)
    cards across the vertical band with `_distribute` instead of stacking them under the
    heading and leaving everything below empty.
    """
    show_ticker = False

    def __init__(self, plan, dur=5.0):
        super().__init__(dur, [])
        self.plan = plan

    def key(self, t):
        return anim_key(t, 1.2)

    def draw(self, d, L, t):
        p = self.plan
        self.section(d, p.primary_text, ACCENT, p.secondary_text or None)
        y0 = TOP + (30 if p.secondary_text else 0)
        if not p.items:
            return
        if p.scene_type is SceneType.SECTORS and p.metadata.get("presentation") == "HEATMAP":
            draw_heatmap_grid(d, p.items, X0, X1, y0, BAND_BOTTOM, t)
        elif p.scene_type in (SceneType.GAINERS, SceneType.LOSERS):
            draw_ranked_mover_list(d, p.items, X0, X1, y0, BAND_BOTTOM, t,
                                   max_abs_pct=p.metadata.get("scan_max_abs_pct"))
        elif p.scene_type is SceneType.GLOBAL and p.metadata.get("presentation") == "GLOBAL_SCAN":
            draw_global_grid(d, p.items, X0, X1, y0, BAND_BOTTOM, t)
        elif p.scene_type is SceneType.SECTORS and len(p.items) >= 2:
            self._draw_comparison(d, p, t, y0)
        elif p.scene_type in (SceneType.MOVERS, SceneType.FLOWS):
            self._draw_panels(d, p, t, y0)
        elif p.scene_type is SceneType.CONTEXT:
            self._draw_context(d, p, t, y0)
        else:
            self._draw_cards(d, p, t, y0)

    # ------------------------------------------------------------------ CONTEXT
    def _draw_context(self, d, p, t, y0):
        """Strong hierarchy, not a card: the subject (`item.label` - NIFTY/VIX/FII/DII, the
        short tag the planner already attaches) is the large key element, and the already
        glanceable interpretation (`context_line()`'s output) sits underneath it, smaller.

        Panels are sized like FLOWS/MOVERS - one or two of them are meant to read as the
        scene's main content, not as a card floating near the heading with the rest of the
        canvas empty beneath it.
        """
        items = p.items
        heights = [max(200, (BAND_BOTTOM - y0 - 22 * (len(items) - 1)) / len(items))
                  for _ in items]
        ys = _distribute(len(items), heights, y0, BAND_BOTTOM)
        for i, (item, y, h) in enumerate(zip(items, ys, heights)):
            e = ease((t - i * 0.2) / 0.5)
            if e <= 0:
                continue
            dy = (1 - e) * 50
            colour = TEXT if item.positive is None else pct_color(1 if item.positive else -1)

            d.rounded_rectangle((X0, y + dy, X1, y + h), 26, fill=CARD)
            d.rounded_rectangle((X0, y + dy, X0 + 8, y + h), 4, fill=colour)

            ft = fit(item.title, X1 - X0 - 84, 42, min_size=28)
            lines = wrap(item.title, ft, X1 - X0 - 84)[:2]
            label_h = int(font(70).size * 1.15) if item.label else 0
            block_h = label_h + 14 + int(ft.size * 1.28) * len(lines)

            tx = X0 + 42
            ty = y + dy + max(36, (h - block_h) / 2)
            if item.label:
                fl = font(70)
                d.text((tx, ty), item.label, font=fl, fill=colour)
                ty += int(fl.size * 1.15)

            ty += 14
            for line in lines:
                d.text((tx, ty), line, font=ft, fill=TEXT)
                ty += int(ft.size * 1.28)

    # ------------------------------------------------------------------ SECTORS
    def _draw_comparison(self, d, p, t, y0):
        """Strongest vs weakest, side by side. `_sectors_scene` (editorial/planner.py)
        orders items [strongest, weakest, runner-up], so the first two panels are exactly
        that comparison by construction - nothing is re-ranked here."""
        pair = p.items[:2]
        gap = 28
        panel_w = (X1 - X0 - gap) / 2
        bottom = BAND_BOTTOM - (90 if len(p.items) > 2 else 0)
        tags = ("STRONGEST", "WEAKEST")
        for i, (item, tag) in enumerate(zip(pair, tags)):
            e = ease((t - i * 0.18) / 0.5)
            if e <= 0:
                continue
            dy = (1 - e) * 60
            x0 = X0 + i * (panel_w + gap)
            x1 = x0 + panel_w
            colour = TEXT if item.positive is None else pct_color(1 if item.positive else -1)

            d.rounded_rectangle((x0, y0 + dy, x1, bottom), 28, fill=CARD)
            d.rounded_rectangle((x0, y0 + dy, x1, y0 + dy + 8), 4, fill=colour)

            ft = font(26)
            tw = tlen(tag, ft) + 30
            d.rounded_rectangle((x0 + (panel_w - tw) / 2, y0 + dy + 34, x0 + (panel_w + tw) / 2,
                                 y0 + dy + 74), 20, fill=colour)
            d.text((x0 + (panel_w - tlen(tag, ft)) / 2, y0 + dy + 40), tag, font=ft,
                   fill=(10, 14, 30))

            fv = fit(item.value, panel_w - 40, 128, min_size=64)
            vy = y0 + dy + (bottom - (y0 + dy)) / 2 - fv.size * 0.55
            d.text((x0 + (panel_w - tlen(item.value, fv)) / 2, vy), item.value, font=fv,
                   fill=colour)

            fn = fit(item.title, panel_w - 40, 42, min_size=28)
            d.text((x0 + (panel_w - tlen(item.title, fn)) / 2, bottom - fn.size * 1.6),
                   item.title, font=fn, fill=TEXT)

        for i, item in enumerate(p.items[2:], start=2):
            e = ease((t - i * 0.18) / 0.5)
            if e <= 0:
                continue
            self._draw_card_row(d, item, e, X0, X1, bottom + 20, 60, small=True)

    # ------------------------------------------------------------------ MOVERS / FLOWS
    def _draw_panels(self, d, p, t, y0):
        """Large stacked panels - one per item, direction (buy/sell, gain/loss) carried by
        the label chip and colour exactly as the plan already states it."""
        items = p.items
        heights = [max(160, (BAND_BOTTOM - y0 - 22 * (len(items) - 1)) / len(items))
                  for _ in items]
        ys = _distribute(len(items), heights, y0, BAND_BOTTOM)
        # Exactly two movers (best gainer, worst loser, no runner-up) already get the whole
        # band split evenly between them - the panels were never small. What made them read
        # as small was the content inside: default card fonts stayed top-pinned in a ~350px
        # panel. The "big"/"hero" sizing and centring already built for a lone card apply
        # here too, so the number and symbol fill the panel they're actually given.
        pair = p.scene_type is SceneType.MOVERS and len(items) == 2
        for i, (item, y) in enumerate(zip(items, ys)):
            e = ease((t - i * 0.16) / 0.45)
            if e <= 0:
                continue
            self._draw_card_row(d, item, e, X0, X1, y, heights[i], big=pair, hero=pair)

    # ------------------------------------------------------------------ GLOBAL / CONTEXT / EVENTS
    def _draw_cards(self, d, p, t, y0):
        items = p.items
        n = len(items)
        big = n <= 2
        # A lone card is common (a cold-start day with one global cue, one event) and is the
        # exact pathological case this phase exists to fix: sized to its own text, it sits as
        # a sliver under the heading with the rest of the canvas empty. So it gets a hero
        # size - most of the band - and its content is centred inside that panel rather than
        # pinned to the top of it. Two or three cards keep the plain card treatment and are
        # spread across the band by `_distribute` instead.
        hero = n == 1
        laid = []
        for item in items:
            value_font = font(84 if hero else (72 if big else 58)) if item.value else None
            value_w = tlen(item.value, value_font) + 44 if item.value else 0
            size = 60 if hero else (50 if big else 40)
            title_font = fit(item.title, X1 - X0 - 130 - value_w, size, min_size=28)
            lines = wrap(item.title, title_font, X1 - X0 - 130 - value_w)[:2]
            note_font = font(34 if hero else (30 if big else 26), False)
            note_lines = (wrap(item.note, note_font, X1 - X0 - 140 - value_w)[:2]
                          if item.note else [])
            natural = (52 + int(title_font.size * 1.22) * len(lines)
                      + sum(int(note_font.size * 1.3) for _ in note_lines))
            h = max(natural, 150 if big else 118)
            if hero:
                h = max(h, (BAND_BOTTOM - y0) * 0.5)
            laid.append(h)
        # Exactly two GLOBAL cards is where the leftover band room used to become one large
        # canyon between them - `_distribute` puts ALL unused space into the one gap between
        # two items. Capped here; the remainder is left as trailing space below the pair
        # instead, which reads as breathing room rather than a gap that looks like a mistake.
        cap = 90 if (p.scene_type is SceneType.GLOBAL and n == 2) else None
        ys = _distribute(n, laid, y0, BAND_BOTTOM, max_gap=cap)
        for i, (item, y, h) in enumerate(zip(items, ys, laid)):
            e = ease((t - i * 0.16) / 0.45)
            if e <= 0:
                continue
            if hero and p.scene_type is SceneType.EVENTS:
                self._draw_event_hero(d, item, e, X0, X1, y, h)
            else:
                self._draw_card_row(d, item, e, X0, X1, y, h, big=big, hero=hero)

    # ------------------------------------------------------------------ single EVENTS hero
    def _draw_event_hero(self, d, item, e, x0, x1, y, h):
        """A single "watch next" item, dedicated rather than reusing the value-bearing card
        layout: events never carry a `value` (no number to be dominant), so the tag and the
        headline themselves are what get the emphasis - a bigger pill, a bigger headline,
        both centred in the panel instead of pinned under it."""
        dx = -(1 - e) * 120
        avail = x1 - x0

        title_font = fit(item.title, avail - 100, 66, min_size=34)
        lines = wrap(item.title, title_font, avail - 100)[:3]
        label_h = 0
        fl = font(32)
        if item.label:
            label_h = int(fl.size * 1.3) + 22
        block_h = label_h + int(title_font.size * 1.24) * len(lines)

        d.rounded_rectangle((x0 + dx, y, x1 + dx, y + h), 26, fill=CARD)
        d.rounded_rectangle((x0 + dx, y, x0 + 8 + dx, y + h), 4, fill=ACCENT)

        tx = 50 + x0 + dx
        ty = y + max(30, (h - block_h) / 2)
        if item.label:
            lw = tlen(item.label, fl) + 34
            d.rounded_rectangle((tx, ty, tx + lw, ty + int(fl.size * 1.3)), 22, fill=ACCENT)
            d.text((tx + 17, ty + 7), item.label, font=fl, fill=(10, 14, 30))
            ty += label_h

        for line in lines:
            d.text((tx, ty), line, font=title_font, fill=TEXT)
            ty += int(title_font.size * 1.24)

    # ------------------------------------------------------------------ shared card renderer
    def _draw_card_row(self, d, item, e, x0, x1, y, h, dx_amount=120, big=False, small=False,
                       hero=False):
        dx = -(1 - e) * dx_amount
        colour = TEXT if item.positive is None else pct_color(1 if item.positive else -1)
        avail = x1 - x0

        value_font = None
        value_w = 0
        if item.value:
            value_font = font(30 if small else (84 if hero else (72 if big else 58)))
            value_w = tlen(item.value, value_font) + 44

        title_size = 26 if small else (60 if hero else (50 if big else 40))
        title_font = fit(item.title, avail - 130 - value_w, title_size, min_size=24)
        lines = wrap(item.title, title_font, avail - 130 - value_w)[:2]
        note_font = font(26 if small else (34 if hero else (30 if big else 26)), False)
        note_lines = (wrap(item.note, note_font, avail - 140 - value_w)[:2 if big else 1]
                      if item.note else [])

        d.rounded_rectangle((x0 + dx, y, x1 + dx, y + h), 22, fill=CARD)
        d.rounded_rectangle((x0 + dx, y, x0 + 8 + dx, y + h), 4, fill=colour)

        label_h = (32 if small else 38) + 14 if item.label else 0
        block_h = label_h + int(title_font.size * 1.22) * len(lines) \
            + sum(int(note_font.size * 1.3) for _ in note_lines)
        tx = 42 + x0 + dx
        # A hero card centres its content in the panel; every other card stays pinned under
        # its top edge so a viewer's eye lands in the same place scene to scene.
        ty = y + max(22, (h - block_h) / 2) if hero else y + 22
        if item.label:
            fl = font(22 if small else 26)
            lw = tlen(item.label, fl) + 26
            d.rounded_rectangle((tx, ty, tx + lw, ty + (32 if small else 38)), 18, fill=ACCENT)
            d.text((tx + 13, ty + 5), item.label, font=fl, fill=(10, 14, 30))
            ty += (44 if small else 52)

        for line in lines:
            d.text((tx, ty), line, font=title_font, fill=TEXT)
            ty += int(title_font.size * 1.22)
        for line in note_lines:
            d.text((tx, ty), line, font=note_font, fill=SUB)
            ty += int(note_font.size * 1.3)

        if item.value:
            d.text((x1 - 32 - tlen(item.value, value_font) + dx, y + (h - value_font.size) / 2),
                   item.value, font=value_font, fill=colour)


class NiftyScene(Scene):
    """The index: one number, one observation, and the chart. No chip row of DMAs, RSI,
    pivots and levels - all of that stays in the report, where it is still available."""
    show_ticker = False

    def __init__(self, plan, chart_paths, dur=6.0):
        super().__init__(dur, [])
        self.plan = plan
        self.layers = [Image.open(p).convert("RGB") for p in chart_paths]

    def key(self, t):
        if t < 2.4:
            return ("rev", int(t * FPS))
        z = 1 + 0.06 * ease((t - 2.4) / max(0.1, self.dur - 2.4))
        return ("zoom", round(z / 0.003) * 0.003)

    def chart_frame(self, t):
        base, cand, full = self.layers
        cw, ch = full.size
        if t < 1.4:
            img = base.copy()
            cut = int(ease(t / 1.4) * cw)
            if cut > 0:
                img.paste(cand.crop((0, 0, cut, ch)), (0, 0))
            return img
        if t < 2.4:
            img = cand.copy()
            cut = int(ease((t - 1.4) / 1.0) * cw)
            if cut > 0:
                img.paste(full.crop((0, 0, cut, ch)), (0, 0))
            return img
        z = self.key(t)[1]
        big = full.resize((int(cw * z), int(ch * z)), Image.BILINEAR)
        top = (big.size[1] - ch) // 2
        return big.crop((big.size[0] - cw, top, big.size[0], top + ch))

    def draw(self, d, L, t):
        p = self.plan
        colour = TEXT if p.primary_positive is None else pct_color(1 if p.primary_positive else -1)
        d.text((60, SECTION_Y), p.primary_text, font=font(62), fill=TEXT)

        fv = fit(p.primary_value, 460, 104, min_size=72)
        d.text((X1 - tlen(p.primary_value, fv), SECTION_Y - 14), p.primary_value, font=fv, fill=colour)

        if p.secondary_text:
            fs = fit(p.secondary_text, X1 - X0, 38, min_size=30)
            d.text((60, SECTION_Y + 84), p.secondary_text, font=fs, fill=SUB)

        L.paste(self.chart_frame(t), (50, 508))


class OutroScene(Scene):
    """A short sign-off. Branding does not need five seconds of a sixty-second Short."""
    show_ticker = True

    def __init__(self, plan=None, dur=2.0):
        super().__init__(dur, [])
        self.plan = plan

    def key(self, t):
        return int((math.sin(t * 5) + 1) * 3)

    def draw(self, d, L, t):
        lvl = self.key(t)
        # Drawn from the plan, not from literals here: text on screen that the plan does not
        # know about would escape both the content scan and the readability budget.
        cta = (self.plan.primary_text if self.plan else "SUBSCRIBE") or "SUBSCRIBE"
        f2 = font(104 + lvl * 2)
        w2 = tlen(cta, f2)
        d.rounded_rectangle(((W - w2) / 2 - 50, 640 - 84 - lvl * 2, (W + w2) / 2 + 50, 640 + 84 + lvl * 2), 40,
                            fill=(230, 33, 23))
        d.text(((W - w2) / 2, 640 - f2.size * 0.62), cta, font=f2, fill=(255, 255, 255))
        sub = (self.plan.secondary_text if self.plan else "") or ""
        if sub:
            fs = font(38, False)
            d.text(((W - tlen(sub, fs)) / 2, 800), sub, font=fs, fill=SUB)


# --------------------------------------------------------------------- plan -> scenes
def scenes_from_plan(plan, chart_paths=None):
    """Build the renderer's scenes from an editorial plan.

    The plan decides what each scene says and how long it runs; this only chooses which of
    the three layouts draws it. Every scene carries exactly one caption-free message, so
    nothing cycles text while the viewer is reading.
    """
    scenes = []
    for scene_plan in plan.scenes:
        kind = scene_plan.scene_type.value
        duration = scene_plan.planned_duration
        if kind == "HOOK":
            scene = HookScene(scene_plan, duration)
        elif kind == "OUTRO":
            scene = OutroScene(scene_plan, duration)
        elif kind == "NIFTY" and chart_paths:
            scene = NiftyScene(scene_plan, chart_paths, duration)
        else:
            scene = RowsScene(scene_plan, duration)
        scene.show_ticker = scene_plan.show_ticker
        scene.plan = scene_plan
        scenes.append(scene)
    return scenes


# ----------------------------------------------------------------------------- layout density
# Diagnostic only - not a publication gate, not wired into main.py. It exists to catch the
# specific pathological layout Phase 4.1.2 shipped with: a scene whose cards all end in the
# top third of the safe zone, leaving the rest of the canvas empty. It counts pixels, not
# words or facts - no computer vision, no model, just "how far down did we actually paint."
TOP_HEAVY_SCENE_TYPES = {"GLOBAL", "FLOWS", "SECTORS", "MOVERS", "GAINERS", "LOSERS",
                         "CONTEXT", "EVENTS"}
MIN_LAYOUT_DENSITY = 1 / 3


def content_bbox(scene, t=None):
    """Alpha-channel bounding box of everything a scene has painted, once its entrance
    animation has settled (defaults to just before the scene ends). None if the scene drew
    nothing at that instant."""
    if t is None:
        t = max(scene.dur - 0.05, 0.0)
    return scene.layer(t).getchannel("A").getbbox()


def layout_density(scene, band_top=TOP, band_bottom=BAND_BOTTOM, t=None):
    """How far down the usable band (`band_top`..`band_bottom`) this scene's content reaches,
    as a fraction (0..1). 1.0 means the content's lowest pixel is at the bottom of the band;
    it does not mean the band is full end to end, only that it isn't stuck at the top."""
    bbox = content_bbox(scene, t)
    if not bbox:
        return 0.0
    bottom = bbox[3]
    span = band_bottom - band_top
    return max(0.0, min(1.0, (bottom - band_top) / span)) if span else 0.0


def check_layout_density(scenes):
    """One entry per scene whose type is expected to use the vertical band (HOOK, OUTRO and
    NIFTY are exempt - they have their own, deliberate whitespace or are chart-dominated).
    `top_heavy` flags the pathological case this metric exists to catch; it does not require
    every scene to reach the same depth."""
    findings = []
    for scene in scenes:
        kind = scene.plan.scene_type.value if hasattr(scene, "plan") else type(scene).__name__
        if kind not in TOP_HEAVY_SCENE_TYPES:
            continue
        density = layout_density(scene)
        findings.append({"scene": kind, "density": round(density, 3),
                         "top_heavy": density < MIN_LAYOUT_DENSITY})
    return findings


# ----------------------------------------------------------------------------- render
def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


PROV_BOX = (X0, 1380, 920, 1462)     # SOURCE / DATA AS OF plate: left of the Shorts action rail


def provenance_layer(prov):
    """The legacy renderer's SOURCE / DATA AS OF plate (same size/position grammar as
    daily_video.provenance_bar): two bold 26 px lines on a dark plate, above the disclaimer."""
    key = ("prov", prov.get("source"), prov.get("as_of"))
    if key in _cap_cache:
        return _cap_cache[key]
    bw, bh = PROV_BOX[2] - PROV_BOX[0], PROV_BOX[3] - PROV_BOX[1]
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, bw - 1, bh - 1), 14, fill=(12, 19, 44, 225), outline=(44, 60, 104, 255),
                        width=2)
    d.rounded_rectangle((0, 12, 5, bh - 13), 3, fill=ACCENT + (255,))
    y = 10
    for line in (prov.get("source"), prov.get("as_of")):
        if not line:
            continue
        f = fit(line, bw - 40, 26, min_size=24)
        head, sep, rest = line.partition(": ")
        if sep:
            d.text((22, y), head + ":", font=f, fill=SUB)
            d.text((22 + tlen(head + ": ", f), y), rest, font=f, fill=TEXT)
        else:
            d.text((22, y), line, font=f, fill=TEXT)
        y += 34
    _cap_cache[key] = img
    return img


def render(scenes, info, ticker_items, music_path, out_path, demo=False, provenance=None):
    """`provenance`: optional list aligned with `scenes` - {"source", "as_of"} or None - drawn
    as the SOURCE / DATA AS OF plate for the whole of that scene (public profile)."""
    total = sum(s.dur for s in scenes)
    bd = Backdrop()
    head = header_layer(info, demo)
    tick = Ticker(ticker_items)
    disc = "For information only - not investment advice"
    fdisc = font(28, False)

    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path, "-map", "0:v", "-map", "1:a",
                "-c:a", "aac", "-b:a", "160k",
                "-af", f"volume={MUSIC_VOLUME},afade=t=in:st=0:d=1,afade=t=out:st={total - 2.5}:d=2.5"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", f"{total:.2f}", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    starts = np.cumsum([0] + [s.dur for s in scenes[:-1]])
    cur = -1
    for fi in range(int(round(total * FPS))):
        t = fi / FPS
        i = int(np.searchsorted(starts, t, side="right") - 1)
        sc, tl = scenes[i], t - starts[i]
        if i != cur:
            if cur >= 0:
                scenes[cur]._cache.clear()
            cur = i
        frame = bd.frame(t)
        frame.paste(head, (0, 0), head)
        # Scenes the viewer has to read hide the ticker: a scrolling strip competing with a
        # number someone is trying to take in is the cheapest distraction to remove.
        if getattr(sc, "show_ticker", True):
            tick.paste(frame, t)
        L = sc.layer(tl)
        if tl < 0.35:
            e = ease(tl / 0.35)
            L = L.copy()
            L.putalpha(L.getchannel("A").point(lambda v: int(v * e)))
            frame.paste(L, (0, int((1 - e) * 40)), L)
        else:
            frame.paste(L, (0, 0), L)
        cap = sc.caption(tl)
        if cap:
            layer = caption_layer(*cap)
            frame.paste(layer, CAP_BOX[:2], layer)
        prov = provenance[i] if provenance and i < len(provenance) else None
        if prov:
            pl = provenance_layer(prov)
            frame.paste(pl, PROV_BOX[:2], pl)
        d = ImageDraw.Draw(frame)
        d.line((X0, LINE_Y, X1, LINE_Y), fill=(40, 54, 96), width=5)
        d.line((X0, LINE_Y, X0 + (X1 - X0) * (t / total), LINE_Y), fill=ACCENT, width=5)
        d.text(((W - tlen(disc, fdisc)) / 2, DISC_Y), disc, font=fdisc, fill=SUB)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    return out_path

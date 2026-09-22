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
    def __init__(self, dur, texts):
        self.dur, self.texts = dur, texts
        self._cache = OrderedDict()

    def captions(self):
        k = len(self.texts)
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


class IntroScene(Scene):
    def __init__(self, info, hook, dur=2.0):
        super().__init__(dur, [f"Your daily market byte for {info['recap_str']}."])
        self.hook = hook

    def key(self, t):
        return anim_key(t, 0.8)

    def draw(self, d, L, t):
        e = ease(t / 0.7)
        f = font(100 + 30 * e)
        y1 = 500 - 60 * e
        tw1 = tlen("DAILY MARKET", f)
        d.text(((W - tw1) / 2, y1), "DAILY MARKET", font=f, fill=TEXT)
        y2 = y1 + 130
        tw2 = tlen("BYTE", f)
        d.text(((W - tw2) / 2, y2), "BYTE", font=f, fill=ACCENT)
        s = "Indian Stock Market Recap"
        d.text(((W - tlen(s, font(50))) / 2, 830), s, font=font(50), fill=TEXT)
        if self.hook:
            fh = fit(self.hook, 880, 40)
            hw = tlen(self.hook, fh)
            d.rounded_rectangle(((W - hw) / 2 - 30, 930, (W + hw) / 2 + 30, 1010), 22, outline=YELLOW, width=3,
                                fill=(40, 36, 10, int(200 * e)))
            d.text(((W - hw) / 2, 947), self.hook, font=fh, fill=YELLOW)
        items = [("GLOBAL", ACCENT), ("NIFTY", TEXT), ("SECTORS", YELLOW), ("GAINERS", GREEN), ("LOSERS", RED)]
        fs = font(28)
        widths = [tlen(i, fs) + 36 for i, _ in items]
        x = (W - sum(widths) - 14 * 4) / 2
        for k, ((it, c), w) in enumerate(zip(items, widths)):
            if t > 0.1 + k * 0.08:
                d.rounded_rectangle((x, 1060, x + w, 1114), 27, outline=c, width=3)
                d.text((x + 18, 1071), it, font=fs, fill=c)
            x += w + 14


class GlobalScene(Scene):
    def __init__(self, tiles, texts, dur=7.0):
        super().__init__(dur, texts)
        self.tiles = tiles[:6]

    def key(self, t):
        return anim_key(t, 1.8)

    def draw(self, d, L, t):
        self.section(d, "GLOBAL CUES", ACCENT, "Overnight markets & commodities | morning IST")
        tw, th, gap = 480, 216, 20
        for i, tl in enumerate(self.tiles):
            st = i * 0.12
            e = ease((t - st) / 0.45)
            if e <= 0:
                continue
            x = X0 + (i % 2) * (tw + gap)
            y = TOP + (i // 2) * (th + gap) + (1 - e) * 40
            d.rounded_rectangle((x, y, x + tw, y + th), 24, fill=CARD)
            d.rounded_rectangle((x, y, x + 8, y + th), 4, fill=pct_color(tl["pct"]))
            d.text((x + 30, y + 24), tl["label"], font=font(30), fill=SUB)
            prev = tl["value"] / (1 + tl["pct"] / 100)
            v = prev + (tl["value"] - prev) * ease((t - st) / 1.1)
            d.text((x + 30, y + 70), fmt_val(v, tl["dec"], tl.get("prefix", "")), font=fit(
                fmt_val(tl["value"], tl["dec"], tl.get("prefix", "")), tw - 60, 58), fill=TEXT)
            up = tl["pct"] >= 0
            triangle(d, x + 30, y + 156, 24, up, pct_color(tl["pct"]))
            d.text((x + 66, y + 146), f"{tl['pct']:+.2f}%", font=font(38), fill=pct_color(tl["pct"]))


class FiiDiiScene(Scene):
    def __init__(self, fd, recap_str, texts, dur=5.0):
        super().__init__(dur, texts)
        self.fd, self.recap = fd, recap_str

    def key(self, t):
        return anim_key(t, 1.4)

    def draw(self, d, L, t):
        src = "NSE" if self.fd.get("source") == "NSE" else "web sources"
        self.section(d, "FII / DII FLOWS", YELLOW, f"Cash market | {self.recap} | provisional ({src})")
        mx = max(abs(self.fd["fii"]), abs(self.fd["dii"]), 1)
        for i, (lab, v) in enumerate([("FII / FPI", self.fd["fii"]), ("DII", self.fd["dii"])]):
            y = TOP + 20 + i * 300
            e = ease((t - i * 0.2) / 1.1)
            d.rounded_rectangle((X0, y, X1, y + 270), 26, fill=CARD)
            d.text((84, y + 28), lab, font=font(46), fill=TEXT)
            col = pct_color(v)
            pill = "NET BUY" if v >= 0 else "NET SELL"
            pw = tlen(pill, font(28)) + 36
            d.rounded_rectangle((X1 - 34 - pw, y + 32, X1 - 34, y + 82), 25, fill=col)
            d.text((X1 - 34 - pw + 18, y + 40), pill, font=font(28), fill=(10, 14, 30))
            d.text((84, y + 100), f"{'+' if v >= 0 else '-'}{RS}{fmt_in(abs(v) * e)} cr", font=font(70), fill=col)
            bw = int((X1 - X0 - 68) * abs(v) / mx * e)
            d.rounded_rectangle((84, y + 212, 84 + max(bw, 10), y + 236), 12, fill=col)
        net = self.fd["fii"] + self.fd["dii"]
        s = f"Combined net: {'+' if net >= 0 else '-'}{RS}{fmt_in(abs(net))} cr"
        d.text((84, TOP + 640), s, font=font(36, False), fill=SUB)


class NiftyScene(Scene):
    def __init__(self, m, chart_paths, texts, dur=16.0):
        super().__init__(dur, texts)
        self.m = m
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
        m = self.m
        d.text((60, SECTION_Y), "NIFTY 50", font=font(62), fill=TEXT)
        up = m["chg"] >= 0
        col = pct_color(m["chg"])
        v = m["prev"] + (m["close"] - m["prev"]) * ease(t / 1.0)
        val = fmt_in(v, 2)
        fv = font(56)
        d.text((X1 - tlen(fmt_in(m["close"], 2), fv), SECTION_Y + 4), val, font=fv, fill=TEXT)
        chs = f"{m['chg']:+.2f} ({m['pct']:+.2f}%)"
        fc = font(34)
        cx = X1 - tlen(chs, fc)
        d.text((cx, SECTION_Y + 72), chs, font=fc, fill=col)
        triangle(d, cx - 36, SECTION_Y + 80, 24, up, col)
        chips = [f"H {fmt_in(m['high'])}", f"L {fmt_in(m['low'])}"]
        if m.get("bank_pct") is not None:
            chips.append(f"BankNifty {m['bank_pct']:+.2f}%")
        if m.get("vix") is not None:
            chips.append(f"VIX {m['vix']:.1f}")
        fs = font(28)
        x, y = 60, 446
        for c in chips:
            w = tlen(c, fs) + 32
            d.rounded_rectangle((x, y, x + w, y + 46), 23, fill=CARD)
            d.text((x + 16, y + 7), c, font=fs, fill=TEXT)
            x += w + 14
        L.paste(self.chart_frame(t), (50, 508))


class SectorScene(Scene):
    def __init__(self, sectors, recap_str, texts, dur=8.0):
        super().__init__(dur, texts)
        self.sec, self.recap = sectors[:12], recap_str

    def key(self, t):
        pulse = int((math.sin(t * 6) + 1) * 2) if t > 1.4 else -1
        return (anim_key(t, 1.4), pulse)

    def draw(self, d, L, t):
        self.section(d, "SECTOR HEATMAP", YELLOW, f"Nifty sectoral indices | {self.recap}")
        cols = 3
        tw, th, gap = (X1 - X0 - 2 * 16) / cols, 166, 16
        n = len(self.sec)
        pulse = self.key(t)[1]
        for i, s in enumerate(self.sec):
            e = ease((t - i * 0.07) / 0.4)
            if e <= 0:
                continue
            x = X0 + (i % cols) * (tw + gap)
            y = TOP + (i // cols) * (th + gap) + (1 - e) * 30
            k = 0.2 + 0.8 * min(1.0, abs(s["pct"]) / 2.0)
            fill = mix(CARD, (18, 150, 100) if s["pct"] >= 0 else (190, 40, 70), k)
            special = pulse >= 0 and (i == 0 or i == n - 1)
            d.rounded_rectangle((x, y, x + tw, y + th), 22, fill=fill,
                                outline=YELLOW if special else None, width=3 + pulse if special else 0)
            d.text((x + 22, y + 22), s["name"], font=fit(s["name"], tw - 40, 34), fill=TEXT)
            d.text((x + 22, y + 84), f"{s['pct']:+.2f}%", font=font(48), fill=TEXT)


class MoversScene(Scene):
    def __init__(self, kind, rows, universe_label, recap_str, dur=13.5):
        self.kind, self.rows = kind, rows
        self.color = pct_color(1 if kind == "gainers" else -1)
        self.uni, self.recap = universe_label, recap_str
        self.maxabs = max(abs(r["pct"]) for r in rows) or 1
        super().__init__(dur, [])

    def captions(self):
        word = "gainers" if self.kind == "gainers" else "losers"
        step = (self.dur - 1.0) / len(self.rows)
        caps = [(0, 1.0, f"Top 5 {word} in {self.uni}.")]
        caps += [(1.0 + i * step, step, f"{r['symbol']} {r['pct']:+.1f}%: {r['reason']}")
                 for i, r in enumerate(self.rows)]
        return caps

    def active(self, t):
        if t < 1.0:
            return None
        return min(len(self.rows) - 1, int((t - 1.0) / ((self.dur - 1.0) / len(self.rows))))

    def key(self, t):
        a = self.active(t)
        pulse = int((math.sin(t * 2 * math.pi * 1.2) + 1) * 2) if a is not None else 0
        return (anim_key(t, 1.5), a, pulse)

    def draw(self, d, L, t):
        _, active, pulse = self.key(t)
        title = "TOP 5 GAINERS" if self.kind == "gainers" else "TOP 5 LOSERS"
        self.section(d, title, self.color, f"{self.uni}  |  {self.recap}")
        for i, r in enumerate(self.rows):
            e = ease((t - i * 0.12) / 0.45)
            if e <= 0:
                continue
            dx = (1 - e) * 160
            y = TOP + i * 145
            act = i == active
            d.rounded_rectangle((X0 + dx, y, X1 + dx, y + 131), 22, fill=CARD_ACTIVE if act else CARD,
                                outline=self.color if act else None, width=3 + pulse if act else 0)
            cx = 102 + dx
            d.ellipse((cx - 30, y + 35, cx + 30, y + 95), fill=self.color if act else (44, 58, 104))
            num = str(i + 1)
            d.text((cx - tlen(num, font(34)) / 2, y + 44), num, font=font(34), fill=(10, 14, 30) if act else TEXT)
            pct = f"{r['pct']:+.2f}%"
            fp = font(50)
            px = X1 - 26 - tlen(pct, fp) + dx
            d.text((px, y + 16), pct, font=fp, fill=self.color)
            d.text((152 + dx, y + 18), r["symbol"], font=fit(r["symbol"], px - 170 - dx, 46), fill=TEXT)
            d.text((152 + dx, y + 76), ellipsize(r["name"], font(26, False), 440), font=font(26, False), fill=SUB)
            meta = fmt_in(r["close"], 2) + (f"  |  Vol {r['volx']:.1f}x" if r.get("volx") else "")
            fm = font(26, False)
            d.text((X1 - 26 - tlen(meta, fm) + dx, y + 78), meta, font=fm, fill=SUB)
            bw = int(300 * abs(r["pct"]) / self.maxabs * ease((t - 0.3 - i * 0.12) / 0.9))
            d.rounded_rectangle((152 + dx, y + 116, 152 + dx + max(bw, 8), y + 122), 3, fill=self.color)


class EventsScene(Scene):
    def __init__(self, events, info, dur=7.0):
        super().__init__(dur, [f"Key events to watch today, {info['today_short']}.",
                               "Big news from these can move stocks sharply."])
        self.events = events[:5]

    def key(self, t):
        return anim_key(t, 1.8)

    def draw(self, d, L, t):
        self.section(d, "EVENTS TODAY", YELLOW, "What traders are tracking")
        y = TOP
        ft, fx = font(24), font(36)
        for i, e in enumerate(self.events):
            a = ease((t - i * 0.25) / 0.45)
            lines = wrap(e["text"], fx, 700)[:2]
            h = 40 + 46 * len(lines)
            if y + h > 1195:
                break
            if a > 0:
                dx = -(1 - a) * 160
                d.rounded_rectangle((X0 + dx, y, X1 + dx, y + h), 22, fill=CARD)
                tw = tlen(e["tag"], ft) + 28
                d.rounded_rectangle((72 + dx, y + 22, 72 + tw + dx, y + 62), 20, fill=YELLOW)
                d.text((86 + dx, y + 28), e["tag"], font=ft, fill=(10, 14, 30))
                for j, line in enumerate(lines):
                    d.text((72 + max(tw, 150) + 22 + dx, y + 20 + j * 46), line, font=fx, fill=TEXT)
            y += h + 18


class ContextScene(Scene):
    """Historical context: how today compares with recent sessions.

    `lines` are (label, statement) pairs already computed, selected and content-checked by
    the intelligence layer - this scene only draws them. It follows EventsScene's card
    layout so the Short keeps one visual language, and it breaks out of the loop rather than
    overflow the safe zone if the statements run long.
    """
    def __init__(self, lines, dur=6.0):
        super().__init__(dur, ["How today compares with recent sessions.",
                               "Based only on previously recorded sessions."])
        self.lines = lines[:3]

    def key(self, t):
        return anim_key(t, 1.8)

    def draw(self, d, L, t):
        self.section(d, "MARKET CONTEXT", ACCENT, "Versus recent recorded sessions")
        y = TOP
        ft, fx = font(24), font(32)
        for i, (label, statement) in enumerate(self.lines):
            a = ease((t - i * 0.25) / 0.45)
            lines = wrap(statement, fx, 880)[:3]
            # 20 top pad + 38 chip + 10 gap + text + 20 bottom pad, so text never overruns
            # the card it sits in.
            h = 88 + 42 * len(lines)
            if y + h > 1195:
                break
            if a > 0:
                dx = -(1 - a) * 160
                d.rounded_rectangle((X0 + dx, y, X1 + dx, y + h), 22, fill=CARD)
                tw = tlen(label, ft) + 28
                d.rounded_rectangle((72 + dx, y + 20, 72 + tw + dx, y + 58), 19, fill=ACCENT)
                d.text((86 + dx, y + 26), label, font=ft, fill=(10, 14, 30))
                for j, line in enumerate(lines):
                    d.text((72 + dx, y + 68 + j * 42), line, font=fx, fill=TEXT)
            y += h + 18


class OutroScene(Scene):
    def __init__(self, dur=3.0):
        super().__init__(dur, ["Subscribe for your daily market byte. See you next session!"])

    def key(self, t):
        return int((math.sin(t * 5) + 1) * 3)

    def draw(self, d, L, t):
        lvl = self.key(t)
        s1 = "Found this useful?"
        d.text(((W - tlen(s1, font(52))) / 2, 520), s1, font=font(52), fill=TEXT)
        f2 = font(96 + lvl * 2)
        w2 = tlen("SUBSCRIBE", f2)
        d.rounded_rectangle(((W - w2) / 2 - 50, 700 - 80 - lvl * 2, (W + w2) / 2 + 50, 700 + 80 + lvl * 2), 40,
                            fill=(230, 33, 23))
        d.text(((W - w2) / 2, 700 - f2.size * 0.62), "SUBSCRIBE", font=f2, fill=(255, 255, 255))
        s3 = "for your DAILY MARKET BYTE"
        d.text(((W - tlen(s3, font(56))) / 2, 830), s3, font=font(56), fill=ACCENT)
        s4 = "New recap every trading day  |  8 AM IST"
        d.text(((W - tlen(s4, font(34, False))) / 2, 930), s4, font=font(34, False), fill=SUB)


# ----------------------------------------------------------------------------- render
def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def render(scenes, info, ticker_items, music_path, out_path, demo=False):
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
        d = ImageDraw.Draw(frame)
        d.line((X0, LINE_Y, X1, LINE_Y), fill=(40, 54, 96), width=5)
        d.line((X0, LINE_Y, X0 + (X1 - X0) * (t / total), LINE_Y), fill=ACCENT, width=5)
        d.text(((W - tlen(disc, fdisc)) / 2, DISC_Y), disc, font=fdisc, fill=SUB)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    return out_path

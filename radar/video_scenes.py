"""Radar-specific scene drawing (Phase 4.2 Packet 6.2R).

Packet 6.2 built these scenes against an independently-invented visual system; human review
found the result looked like a different product. This rewrite draws every Radar scene INSIDE
the main video's own visual grammar - the same background (`video.Backdrop`), the same content
band (`video.TOP`..`video.BAND_BOTTOM`), the same section-title/subtitle convention
(`video.SECTION_Y`, `font(62)`/`font(38)`), the same card style (rounded panel, colored left
accent bar), the same disclaimer position and wording. `radar/video_renderer.py` draws the
shared header/progress-line/disclaimer on EVERY frame (matching how `video.render()` does it for
the main product); scenes in this module draw ONLY their own unique content region, exactly like
`video.py`'s own `HookScene`/`RowsScene`/`NiftyScene`/`OutroScene` do.

Deliberately NOT a subclass of `video.Scene` - that class's caption/ticker machinery is specific
to the main product's own editorial plan shape. What IS reused is `video.py`'s free drawing
utilities (`font`, `fit`, `ellipsize`, `wrap`, `tlen`, `ease`, `mix`, `pct_color`) and its
`Backdrop` class directly (a small, safe extraction/reuse - `video.py` itself is never modified).

## Intentional differences from the main product (packet spec section 37)

Two things stay deliberately different, because Radar's OWN product requirement says so, not
because of an oversight:

* **Price is never the visual hero.** `NiftyScene` gives its stat a font up to 104pt, bigger
  than its own section title. A Radar story's price (`SIZE_PRICE=44`) stays smaller than the
  instrument name (`SIZE_CARD_LABEL=70`) always - Packet 6.2's own product identity rule
  ("this is not another top-movers video") still applies and is not overridden by visual
  alignment.
* **No ticker.** The main product's ticker shows live index levels (`NIFTY 50`/`BANK NIFTY`/
  `GIFT NIFTY`) that `RadarPresentation` does not carry and this renderer must not fetch
  (packet spec section 24/25) - so it is simply absent, not faked.

## No new facts (packet spec section 25)

Every string/number drawn here is read directly from a `radar.presentation.
RadarStoryPresentation`/`RadarPresentation` field, or is one of: the shared brand/date/
disclaimer/progress elements (drawn once, globally, by the renderer), a scene counter, or a
fixed template word already validated by Packet 6.1's content-safety pass.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from video import Backdrop, ease, fit, font, pct_color, tlen, wrap

from .video_theme import (BRAND_ACCENT, CONTENT_BOTTOM, CONTENT_LEFT, CONTENT_RIGHT, CONTENT_TOP,
                          CONTENT_WIDTH, FAMILY_INACTIVE, FAMILY_RELATIVE, FAMILY_STRUCTURE,
                          FAMILY_VOLUME, H, MIXED, NEGATIVE, POSITIVE, QUIET, SECTION_TITLE_Y,
                          SIZE_BADGE, SIZE_CARD_LABEL, SIZE_CLOSING_HEADLINE, SIZE_CLOSING_SUB,
                          SIZE_CONTEXT, SIZE_COUNTER, SIZE_EVIDENCE, SIZE_FAMILY_LABEL,
                          SIZE_FINDING, SIZE_HOOK_LABEL, SIZE_HOOK_NUMBER, SIZE_PRICE,
                          SIZE_PRICE_QUIET, SIZE_SECTION_SUBTITLE, SIZE_SECTION_TITLE,
                          SURFACE, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, W)

# --------------------------------------------------------------------------- Packet 6.2V: chart
# geometry/animation constants for ChartStoryScene, the evidence-card slot's chart layout.
CHART_HEIGHT = 260
VOLUME_HEIGHT = 80
RELATIVE_HEIGHT = 96
_CHART_GAP = 16

# Plain-English sentences for every `radar.models.TechnicalEventType` value - the SAME 9 keys
# `radar.presentation.TECHNICAL_EVENT_LABELS` already covers exhaustively, phrased as a full
# viewer sentence instead of a short badge label. No new facts: this only rewords an event the
# story's own `technical_context.events` already carries (packet spec section 30 - plain
# English, no jargon, no internal enum syntax on screen).
PLAIN_EVENT_SENTENCE = {
    "BREAK_ABOVE_20D_RANGE": "Price moved above its recent trading range.",
    "BREAK_BELOW_20D_RANGE": "Price moved below its recent trading range.",
    "BREAK_ABOVE_50D_RANGE": "Price moved above its longer-term trading range.",
    "BREAK_BELOW_50D_RANGE": "Price moved below its longer-term trading range.",
    "CROSS_ABOVE_SMA20": "Price moved above its 20-day average.",
    "CROSS_BELOW_SMA20": "Price moved below its 20-day average.",
    "CROSS_ABOVE_SMA50": "Price moved above its 50-day average.",
    "CROSS_BELOW_SMA50": "Price moved below its 50-day average.",
    "RANGE_COMPRESSION": "Price has been trading in a noticeably tighter range.",
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

TRANSITION_SECONDS = 0.35
CARD_RADIUS = 26           # exact radius video.py's own cards use (RowsScene._draw_context)
BADGE_RADIUS = 10
CARD_TOP = CONTENT_TOP + 38  # a NiftyScene-style chart would start at CONTENT_TOP+38 (508); the
                             # Radar evidence card takes that same slot instead of a chart

_DIRECTION_COLOR = {"ALIGNED_POSITIVE": POSITIVE, "ALIGNED_NEGATIVE": NEGATIVE, "MIXED": MIXED}

_CARD_LABEL = {
    "THREE_SIGNAL_CARD": ("THREE INDEPENDENT SIGNALS", BRAND_ACCENT),
    "TECHNICAL_CHANGE_CARD": ("NEW TECHNICAL EVENT", FAMILY_STRUCTURE),
    "VOLUME_STRUCTURE_CARD": ("VOLUME + STRUCTURE", FAMILY_VOLUME),
    "RELATIVE_STRUCTURE_CARD": ("STRUCTURE + RELATIVE PERFORMANCE", FAMILY_RELATIVE),
    "MIXED_SIGNAL_CARD": ("MIXED SIGNALS", MIXED),
    "QUIET_SIGNAL_CARD": ("QUIET SIGNAL", QUIET),
}

_FAMILY_ROWS = (("VOLUME", FAMILY_VOLUME), ("STRUCTURE", FAMILY_STRUCTURE),
               ("RELATIVE_PERFORMANCE", FAMILY_RELATIVE))
_FAMILY_LABEL_TEXT = {"VOLUME": "Volume", "STRUCTURE": "Structure",
                      "RELATIVE_PERFORMANCE": "Relative"}

_TIER_SIZE = {"PRIMARY": SIZE_EVIDENCE + 4, "SECONDARY": SIZE_EVIDENCE, "SUPPORTING": SIZE_EVIDENCE - 6}
_TIER_COLOR = {"PRIMARY": TEXT_PRIMARY, "SECONDARY": TEXT_SECONDARY, "SUPPORTING": TEXT_SECONDARY}


def new_layer() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _section_title(d: ImageDraw.Draw, title: str, value: str | None, value_color,
                   subtitle: str | None, value_size: int = SIZE_PRICE) -> None:
    """The exact `NiftyScene` layout: a white instrument/subject title at `SECTION_TITLE_Y`,
    an optional stat inline at the right (deliberately SMALLER than `NiftyScene`'s own stat -
    see module docstring), and a `SUB`-colored subtitle line below (same position/size
    `NiftyScene` uses for its own "Closed above its 20-day average" line).

    `value_size` is the ONE place a QUIET_SIGNAL_CARD story visually de-emphasizes its price
    (`SIZE_PRICE_QUIET`, smaller than the default) - the price is shown exactly ONCE, here,
    never duplicated elsewhere on the card just to look "subordinate" twice over."""
    ft = fit(title, 560, SIZE_SECTION_TITLE)
    d.text((CONTENT_LEFT, SECTION_TITLE_Y), title, font=ft, fill=TEXT_PRIMARY)
    if value:
        fv = fit(value, 340, value_size, min_size=24)
        vy = SECTION_TITLE_Y + 6 + (SIZE_PRICE - value_size)
        d.text((CONTENT_RIGHT - tlen(value, fv), vy), value, font=fv, fill=value_color)
    if subtitle:
        fs = fit(subtitle, CONTENT_WIDTH, SIZE_SECTION_SUBTITLE, bold=False, min_size=24)
        d.text((CONTENT_LEFT, SECTION_TITLE_Y + 84), subtitle, font=fs, fill=TEXT_SECONDARY)


def _story_counter(d: ImageDraw.Draw, scene_number: int, story_count: int) -> None:
    text = f"{scene_number} / {story_count}"
    f = font(SIZE_COUNTER, True)
    d.text((CONTENT_RIGHT - tlen(text, f), SECTION_TITLE_Y - 40), text, font=f, fill=TEXT_MUTED)


def _draw_family_indicator(d: ImageDraw.Draw, x: int, y: int, active_families: list) -> int:
    """`VOLUME`/`STRUCTURE`/`RELATIVE_PERFORMANCE` as three rows - active ones lit and
    labelled, inactive ones dimmed but never hidden. No score, no "x/3" (packet spec section
    12/19)."""
    f = font(SIZE_FAMILY_LABEL, True)
    row_h = SIZE_FAMILY_LABEL + 24
    active = set(active_families)
    for i, (key, color) in enumerate(_FAMILY_ROWS):
        is_active = key in active
        dot_color = color if is_active else FAMILY_INACTIVE
        text_color = TEXT_PRIMARY if is_active else TEXT_MUTED
        cy = y + i * row_h
        d.ellipse((x, cy + 3, x + 16, cy + 19), fill=dot_color)
        d.text((x + 28, cy), _FAMILY_LABEL_TEXT[key], font=f, fill=text_color)
    return y + len(_FAMILY_ROWS) * row_h


def _draw_evidence_items(d: ImageDraw.Draw, x: int, y: int, max_width: int, items: list) -> int:
    cy = y
    for item in items:
        size = _TIER_SIZE.get(item.tier, SIZE_EVIDENCE)
        color = _TIER_COLOR.get(item.tier, TEXT_SECONDARY)
        f = fit(item.label, max_width, size, bold=(item.tier == "PRIMARY"))
        for line in wrap(item.label, f, max_width)[:2]:
            d.text((x, cy), line, font=f, fill=color)
            cy += size + 14
        cy += 14
    return cy


def _draw_badge(d: ImageDraw.Draw, x: int, y: int, visual_type: str) -> int:
    """A pill-shaped badge - the same rounded-rectangle-chip vocabulary `video.py`'s own
    ranked-mover/heatmap cells already use, not a new shape language."""
    label, color = _CARD_LABEL.get(visual_type, ("RADAR SIGNAL", TEXT_PRIMARY))
    f = fit(label, CONTENT_WIDTH - 100, SIZE_BADGE, bold=True)
    w = tlen(label, f)
    d.rounded_rectangle((x, y, x + w + 32, y + SIZE_BADGE + 20), BADGE_RADIUS, fill=SURFACE)
    d.text((x + 16, y + 10), label, font=f, fill=color)
    return y + SIZE_BADGE + 20


class HookScene:
    """Mirrors `video.HookScene`'s exact positions/sizes (label at `fit(...,66)` around
    `TOP-30`, giant number at y=560 with `fit(...,168,min_size=96)`) - the SAME hook format the
    main product already uses for its own opening stat, not a parallel one. The only Radar
    addition is a small, subtle ring behind the number (a restrained nod to "Radar", never a
    competing animation - packet spec section 16/20)."""
    role = "HOOK"

    def __init__(self, duration_seconds: float, headline: str, subheadline: str | None,
                story_count: int, session_date: str):
        self.duration_seconds = duration_seconds
        self.headline, self.subheadline = headline, subheadline
        self.story_count, self.session_date = story_count, session_date

    def draw(self, t: float) -> Image.Image:
        L = new_layer()
        d = ImageDraw.Draw(L)
        e = ease(t / 0.55)

        label = self.headline
        if label:
            fl = fit(label, 960, SIZE_HOOK_LABEL)
            d.text(((W - tlen(label, fl)) / 2, CONTENT_TOP - 30 * (1 - e)), label, font=fl,
                  fill=TEXT_SECONDARY)

        number = str(self.story_count)
        cx, cy = W // 2, 560 + 60
        r = 78 + 4 * math.sin(t * 1.6)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(*QUIET, 130), width=3)

        fv = fit(number, 980, SIZE_HOOK_NUMBER, min_size=96)
        d.text(((W - tlen(number, fv)) / 2, 560), number, font=fv, fill=TEXT_PRIMARY)

        if self.subheadline:
            fs = fit(self.subheadline, 960, 34, bold=False)
            d.text(((W - tlen(self.subheadline, fs)) / 2, 760), self.subheadline, font=fs,
                  fill=TEXT_SECONDARY)
        return L


class StoryCardScene:
    """One STORY scene, drawn entirely inside `video.py`'s own content band (`CONTENT_TOP` =
    470 to `CONTENT_BOTTOM` = 1195) using its section-title/card conventions. One class, not
    six - only the badge text/accent color vary by `visual_type` (packet spec sections 13-18)."""
    role = "STORY"

    def __init__(self, duration_seconds: float, story, scene_number: int, story_count: int,
                session_date: str):
        self.duration_seconds = duration_seconds
        self.story = story
        self.scene_number, self.story_count = scene_number, story_count
        self.session_date = session_date

    def draw(self, t: float) -> Image.Image:
        s = self.story
        L = new_layer()
        d = ImageDraw.Draw(L)

        price_color = None
        if s.price_change_display:
            price_color = POSITIVE if s.price_change_display.startswith("+") else NEGATIVE
        is_quiet = s.visual_type == "QUIET_SIGNAL_CARD"
        _section_title(d, s.instrument, s.price_change_display, price_color,
                       s.headline.split(": ", 1)[1] if ": " in s.headline else s.headline,
                       value_size=SIZE_PRICE_QUIET if is_quiet else SIZE_PRICE)
        _story_counter(d, self.scene_number, self.story_count)

        _, accent = _CARD_LABEL.get(s.visual_type, ("RADAR SIGNAL", TEXT_PRIMARY))
        card_y1 = CONTENT_BOTTOM
        d.rounded_rectangle((CONTENT_LEFT, CARD_TOP, CONTENT_RIGHT, card_y1), CARD_RADIUS, fill=SURFACE)
        d.rounded_rectangle((CONTENT_LEFT, CARD_TOP, CONTENT_LEFT + 8, card_y1), 4, fill=accent)

        pad_x, pad_top = 42, 34
        content_top = CARD_TOP + pad_top
        content_width = (CONTENT_RIGHT - CONTENT_LEFT) - 2 * pad_x
        dummy = ImageDraw.Draw(new_layer())
        content_height = self._draw_card_content(
            dummy, CONTENT_LEFT + pad_x, content_top, content_width, s) - content_top
        usable = (card_y1 - pad_top) - content_top
        y_offset = max(0, (usable - content_height) // 3)  # bias toward the badge/top of the card
        self._draw_card_content(d, CONTENT_LEFT + pad_x, content_top + y_offset, content_width, s)
        return L

    def _draw_card_content(self, d: ImageDraw.Draw, x: int, y: int, width: int, s) -> int:
        y = _draw_badge(d, x, y, s.visual_type) + 30
        y = _draw_family_indicator(d, x, y, s.active_families) + 26
        y = _draw_evidence_items(d, x, y, width, s.evidence_items) + 6

        if s.direction_label:
            color = _DIRECTION_COLOR.get(
                {"Positive Alignment": "ALIGNED_POSITIVE", "Negative Alignment": "ALIGNED_NEGATIVE",
                 "Mixed Signals": "MIXED"}.get(s.direction_label), TEXT_SECONDARY)
            fy = font(SIZE_CONTEXT, True)
            d.text((x, y), s.direction_label, font=fy, fill=color)
            y += SIZE_CONTEXT + 10
        return y


def _takeaway_sentence(evidence, story) -> str:
    """One plain-English line - the primary detected event, or, when none is available, the
    already-validated relative-performance direction or the story's own headline. Never a new
    claim: every branch reads a field the Radar detectors/editorial layer already validated."""
    if getattr(story, "quiet_signal", False):
        return "Price didn't move much, but something changed underneath."
    if getattr(story, "direction_label", None) == "Mixed Signals":
        return "The signals don't fully agree."
    if evidence.highlight_events:
        sentence = PLAIN_EVENT_SENTENCE.get(evidence.highlight_events[0])
        if sentence:
            return sentence
    if evidence.market_relative_20d_pp is not None:
        return ("It has been outperforming Nifty." if evidence.market_relative_20d_pp > 0
               else "It has been underperforming Nifty.")
    return story.headline or ""


def _draw_price_chart(d: ImageDraw.Draw, x: int, y: int, w: int, h: int, evidence,
                      reveal_frac: float) -> None:
    """Progressively-revealed close-price line, with a range-boundary or SMA overlay chosen by
    the story's OWN primary detected event (never a hardcoded choice per instrument) and a
    highlight marker on the latest session once the line is fully drawn."""
    closes = evidence.close_series
    n = len(closes)
    if n < 2:
        return

    primary_event = evidence.highlight_events[0] if evidence.highlight_events else None
    is_sma_event = bool(primary_event) and "SMA" in primary_event
    is_range_event = (bool(primary_event) and "RANGE" in primary_event and
                      "COMPRESSION" not in primary_event)

    overlay = rng_high = rng_low = None
    values = list(closes)
    if is_sma_event:
        overlay = evidence.sma20_series if "SMA20" in primary_event else evidence.sma50_series
        values += [v for v in overlay if v is not None]
    elif is_range_event:
        rng_high = evidence.range_20_high if "20D" in primary_event else evidence.range_50_high
        rng_low = evidence.range_20_low if "20D" in primary_event else evidence.range_50_low
        if rng_high is not None:
            values.append(rng_high)
        if rng_low is not None:
            values.append(rng_low)

    vmin, vmax = min(values), max(values)
    if vmax <= vmin:
        vmax = vmin + 1.0
    pad = (vmax - vmin) * 0.1
    vmin, vmax = vmin - pad, vmax + pad

    def px(i: int) -> float:
        return x + (i / (n - 1)) * w

    def py(v: float) -> float:
        return y + h - (v - vmin) / (vmax - vmin) * h

    visible_n = min(n, max(2, int(round(n * reveal_frac))))

    if is_range_event:
        for level in (rng_high, rng_low):
            if level is not None:
                ly = py(level)
                d.line((x, ly, x + w, ly), fill=TEXT_MUTED, width=2)
    elif is_sma_event and overlay:
        pts = [(px(i), py(overlay[i])) for i in range(visible_n) if overlay[i] is not None]
        if len(pts) >= 2:
            d.line(pts, fill=TEXT_MUTED, width=3, joint="curve")

    pts = [(px(i), py(closes[i])) for i in range(visible_n)]
    if len(pts) >= 2:
        d.line(pts, fill=TEXT_PRIMARY, width=4, joint="curve")

    if visible_n == n:
        cx, cy = pts[-1]
        pulse = 7 + 3 * math.sin(reveal_frac * 14)
        marker_color = POSITIVE if closes[-1] >= closes[0] else NEGATIVE
        d.ellipse((cx - pulse, cy - pulse, cx + pulse, cy + pulse), outline=marker_color, width=3)
        d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=marker_color)


def _draw_volume_bars(d: ImageDraw.Draw, x: int, y: int, w: int, h: int, volumes: list,
                      reveal_frac: float) -> None:
    """Compact volume-bar strip, current session emphasized - progressively revealed left to
    right, same convention as the price line above it."""
    n = len(volumes)
    valid = [v for v in volumes if v]
    if n == 0 or not valid:
        return
    vmax = max(valid)
    visible_n = min(n, max(0, int(round(n * reveal_frac))))
    bar_w = w / n
    for i in range(visible_n):
        v = volumes[i] or 0.0
        bh = (v / vmax) * h if vmax else 0.0
        bx0 = x + i * bar_w
        color = BRAND_ACCENT if i == n - 1 else FAMILY_INACTIVE
        d.rectangle((bx0 + bar_w * 0.18, y + h - bh, bx0 + bar_w * 0.82, y + h), fill=color)


def _draw_relative_bars(d: ImageDraw.Draw, x: int, y: int, w: int, h: int, evidence,
                        reveal_frac: float) -> None:
    """Two horizontal bars - this stock vs. Nifty - both already normalized to start at 100 by
    `radar.visual_evidence` (never here); this function only maps those two already-computed
    endpoint values onto pixels growing out from a shared 100-baseline."""
    stock_series = evidence.relative_stock_normalized
    bench_series = evidence.relative_benchmark_normalized
    if not stock_series or not bench_series:
        return
    stock_end, bench_end = stock_series[-1], bench_series[-1]
    max_dev = max(abs(stock_end - 100.0), abs(bench_end - 100.0), 1.0)
    baseline_x = x + w * 0.42
    scale = (w - (baseline_x - x)) / max_dev * 0.85
    bar_h = 24
    row_gap = 16
    label_to_bar = 26
    f_label = font(SIZE_FAMILY_LABEL, True)

    d.line((baseline_x, y, baseline_x, y + (bar_h + row_gap) + label_to_bar + bar_h),
          fill=TEXT_MUTED, width=2)
    for i, (label, value) in enumerate((("This stock", stock_end), ("Nifty", bench_end))):
        by = y + i * (bar_h + row_gap)
        d.text((x, by), label, font=f_label, fill=TEXT_SECONDARY)
        dev = (value - 100.0) * scale * reveal_frac
        bar_color = POSITIVE if value >= 100.0 else NEGATIVE
        bar_top = by + label_to_bar
        bx0, bx1 = (baseline_x, baseline_x + dev) if dev >= 0 else (baseline_x + dev, baseline_x)
        d.rectangle((bx0, bar_top, bx1, bar_top + bar_h), fill=bar_color)


class ChartStoryScene:
    """A STORY scene backed by real, already-validated chart evidence
    (`radar.visual_evidence.RadarVisualEvidence`) instead of `StoryCardScene`'s evidence-line
    text - draws the price line, a range/SMA overlay chosen by the story's own primary event, a
    volume strip, a Stock-vs-Nifty comparison, and a plain-English takeaway. Computes nothing:
    every number/series it draws already exists on `evidence`, built entirely upstream by
    `radar.visual_evidence.build_visual_evidence` (packet spec: renderer draws, never
    calculates). Same content-band, badge, header/section-title/counter as `StoryCardScene` -
    only the evidence region itself differs."""
    role = "STORY"

    def __init__(self, duration_seconds: float, story, evidence, scene_number: int,
                story_count: int, session_date: str):
        self.duration_seconds = duration_seconds
        self.story = story
        self.evidence = evidence
        self.scene_number, self.story_count = scene_number, story_count
        self.session_date = session_date

    def draw(self, t: float) -> Image.Image:
        s, ev = self.story, self.evidence
        L = new_layer()
        d = ImageDraw.Draw(L)

        price_color = None
        if s.price_change_display:
            price_color = POSITIVE if s.price_change_display.startswith("+") else NEGATIVE
        is_quiet = s.visual_type == "QUIET_SIGNAL_CARD"
        _section_title(d, s.instrument, s.price_change_display, price_color,
                       s.headline.split(": ", 1)[1] if ": " in s.headline else s.headline,
                       value_size=SIZE_PRICE_QUIET if is_quiet else SIZE_PRICE)
        _story_counter(d, self.scene_number, self.story_count)

        _, accent = _CARD_LABEL.get(s.visual_type, ("RADAR SIGNAL", TEXT_PRIMARY))
        card_y1 = CONTENT_BOTTOM
        d.rounded_rectangle((CONTENT_LEFT, CARD_TOP, CONTENT_RIGHT, card_y1), CARD_RADIUS, fill=SURFACE)
        d.rounded_rectangle((CONTENT_LEFT, CARD_TOP, CONTENT_LEFT + 8, card_y1), 4, fill=accent)

        pad_x, pad_top = 42, 30
        x = CONTENT_LEFT + pad_x
        y = CARD_TOP + pad_top
        width = (CONTENT_RIGHT - CONTENT_LEFT) - 2 * pad_x

        y = _draw_badge(d, x, y, s.visual_type) + 18

        chart_reveal = _clamp((t - 1.0) / 2.0, 0.0, 1.0)
        _draw_price_chart(d, x, y, width, CHART_HEIGHT, ev, chart_reveal)
        y += CHART_HEIGHT + _CHART_GAP

        volume_reveal = _clamp((t - 4.5) / 1.5, 0.0, 1.0)
        if volume_reveal > 0:
            _draw_volume_bars(d, x, y, width, VOLUME_HEIGHT, ev.volume_series, volume_reveal)
            if ev.rvol:
                rv_text = f"RVOL {ev.rvol:.1f}x"
                f_rvol = font(SIZE_FAMILY_LABEL, True)
                d.text((x + width - tlen(rv_text, f_rvol), y), rv_text, font=f_rvol,
                      fill=TEXT_SECONDARY)
        y += VOLUME_HEIGHT + _CHART_GAP

        relative_reveal = _clamp((t - 6.0) / 1.2, 0.0, 1.0)
        if relative_reveal > 0:
            _draw_relative_bars(d, x, y, width, RELATIVE_HEIGHT, ev, relative_reveal)
        y += RELATIVE_HEIGHT + _CHART_GAP

        takeaway_reveal = _clamp((t - 7.2) / 0.8, 0.0, 1.0)
        if takeaway_reveal > 0:
            text = _takeaway_sentence(ev, s)
            f = fit(text, width, SIZE_CONTEXT, bold=False)
            slide = 10 * (1 - ease(takeaway_reveal))
            for line in wrap(text, f, width)[:2]:
                d.text((x, y + slide), line, font=f, fill=TEXT_PRIMARY)
                y += SIZE_CONTEXT + 8
        return L


class ClosingScene:
    """The two validated lines only - brand/date/disclaimer are already drawn globally, every
    frame, by the renderer (matching how `video.OutroScene` never redraws the disclaimer
    itself). No CTA (packet spec section 22/39 - not yet)."""
    role = "CLOSING"

    def __init__(self, duration_seconds: float, headline: str, subheadline: str | None):
        self.duration_seconds = duration_seconds
        self.headline, self.subheadline = headline, subheadline

    def draw(self, t: float) -> Image.Image:
        L = new_layer()
        d = ImageDraw.Draw(L)
        cy = (CONTENT_TOP + CONTENT_BOTTOM) // 2 - 60
        fh = fit(self.headline, CONTENT_WIDTH, SIZE_CLOSING_HEADLINE)
        for line in wrap(self.headline, fh, CONTENT_WIDTH):
            x = (W - tlen(line, fh)) / 2
            d.text((x, cy), line, font=fh, fill=TEXT_PRIMARY)
            cy += SIZE_CLOSING_HEADLINE + 14
        if self.subheadline:
            cy += 26
            fs = fit(self.subheadline, CONTENT_WIDTH, SIZE_CLOSING_SUB, bold=False)
            for line in wrap(self.subheadline, fs, CONTENT_WIDTH):
                x = (W - tlen(line, fs)) / 2
                d.text((x, cy), line, font=fs, fill=TEXT_SECONDARY)
                cy += SIZE_CLOSING_SUB + 12
        return L


# --------------------------------------------------------------------------- shared, global-per-frame
# These three mirror EXACTLY what `video.render()` does for the main product: the header is
# built ONCE (a static RGBA layer, pasted every frame - `video.header_layer`'s own pattern),
# the progress line and disclaimer are drawn directly onto each frame since their content
# changes (progress) or is cheap either way (disclaimer). `radar/video_renderer.py` calls all
# three, never a per-scene equivalent - exactly one header/disclaimer/progress-line per video,
# not one per scene.
def radar_header_layer(session_date: str) -> Image.Image:
    """The SAME two-tone-wordmark-with-drop-shadow technique `video.header_layer` uses for
    "DAILY MARKET BYTE" - reused here, not reinvented, for "MARKET RADAR" - plus the small
    `BRAND_KICKER` line so the channel identity stays visible even though the big wordmark now
    names the Radar segment specifically (packet spec section 8: same brand grammar, presented
    as a section of the same product, never a second unrelated logo system)."""
    from .video_theme import (BRAND_KICKER, DATE_COLOR, SIZE_BRAND_KICKER, SIZE_WORDMARK,
                              WORDMARK_PART1, WORDMARK_PART2)
    img = Image.new("RGBA", (W, CONTENT_TOP), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    fk = font(SIZE_BRAND_KICKER, False)
    d.text((60, 36), BRAND_KICKER, font=fk, fill=TEXT_SECONDARY)

    fw = font(SIZE_WORDMARK)
    d.text((62, 74), WORDMARK_PART1, font=fw, fill=(0, 0, 0, 90))
    d.text((62 + tlen(WORDMARK_PART1, fw), 74), WORDMARK_PART2, font=fw, fill=(0, 0, 0, 90))
    d.text((60, 72), WORDMARK_PART1, font=fw, fill=TEXT_PRIMARY)
    d.text((60 + tlen(WORDMARK_PART1, fw), 72), WORDMARK_PART2, font=fw, fill=QUIET)

    date_y = 72 + SIZE_WORDMARK + 24
    fd = font(36)
    d.text((60, date_y), session_date, font=fd, fill=DATE_COLOR)
    return img


def draw_progress_line(d: ImageDraw.Draw, frac: float) -> None:
    """The exact progress-bar `video.render()` draws on every frame: a dim full-width track,
    an accent-colored fill proportional to elapsed time."""
    from .video_theme import PROGRESS_LINE_Y
    from video import ACCENT
    d.line((CONTENT_LEFT, PROGRESS_LINE_Y, CONTENT_RIGHT, PROGRESS_LINE_Y), fill=(40, 54, 96), width=5)
    d.line((CONTENT_LEFT, PROGRESS_LINE_Y, CONTENT_LEFT + CONTENT_WIDTH * frac, PROGRESS_LINE_Y),
          fill=ACCENT, width=5)


def draw_disclaimer(d: ImageDraw.Draw) -> None:
    """The EXACT same text, font, color and (adjusted for Radar's slightly different content
    band) position `video.render()` draws on every single frame of the main product - never a
    Radar-specific rewording."""
    from .video_theme import DISCLAIMER_Y, SIZE_DISCLAIMER
    text = "For information only - not investment advice"
    f = font(SIZE_DISCLAIMER, False)
    d.text(((W - tlen(text, f)) / 2, DISCLAIMER_Y), text, font=f, fill=TEXT_SECONDARY)


__all__ = ["HookScene", "StoryCardScene", "ChartStoryScene", "ClosingScene", "new_layer",
          "TRANSITION_SECONDS", "Backdrop", "radar_header_layer", "draw_progress_line",
          "draw_disclaimer", "PLAIN_EVENT_SENTENCE"]

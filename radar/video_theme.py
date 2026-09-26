"""Visual tokens for the Radar renderer (Phase 4.2 Packet 6.2R).

Packet 6.2 built an independent palette/layout that was technically clean but visually read as
a different product. This module now REUSES the main video's own constants directly
(`config.py`'s colors, `video.py`'s layout geometry) so a Radar frame and a main-video frame
share the same design system by construction - not by two people picking similar-looking
numbers. Only concepts the main product has no equivalent for (evidence-family colors, the
MIXED/QUIET semantic states) get new tokens, chosen to sit naturally alongside the existing
palette rather than introduce a new hue family.

`config.py`/`video.py` are read-only inputs here - nothing in this package imports FROM this
module back into either of those, so the main video's own behavior is provably unaffected by
anything Radar does with these tokens.
"""
from __future__ import annotations

from config import (ACCENT, BG1, BG2, CARD, CARD_ACTIVE, GREEN, FPS, H, RED, SUB, TEXT, W, YELLOW)
from video import BAND_BOTTOM, CAP_BOX, DISC_Y, LINE_Y, SECTION_Y, TICK_Y, TOP, X0, X1

RADAR_RENDERER_VERSION = "2.0"  # bumped: Packet 6.2R is a visual-system realignment, not a patch

# --------------------------------------------------------------------------- shared palette
# Re-exported under their main-video names so `radar/video_scenes.py` never has to know
# whether a color token "belongs" to Radar or to the shared system - it's all one palette.
BACKGROUND_TOP, BACKGROUND_BOTTOM = BG1, BG2
SURFACE = CARD
SURFACE_ACTIVE = CARD_ACTIVE
TEXT_PRIMARY = TEXT
TEXT_SECONDARY = SUB
BRAND_ACCENT = ACCENT
POSITIVE = GREEN
NEGATIVE = RED
DATE_COLOR = YELLOW

# --------------------------------------------------------------------------- Radar-only extensions
# No main-video equivalent exists for "mixed" or "quiet" as states, or for the three evidence
# families - these are new, but deliberately mixed FROM the existing palette (never a fresh,
# unrelated hue) so they read as accents within the same system, not a second palette.
MIXED = YELLOW
QUIET = (110, 190, 255)          # a cooler tint of ACCENT - "quiet" reads as calmer, not alarming
TEXT_MUTED = (110, 120, 156)     # a darker step of SUB, for the least prominent label (footer/meta)

FAMILY_VOLUME = YELLOW
FAMILY_STRUCTURE = ACCENT
FAMILY_RELATIVE = GREEN
FAMILY_INACTIVE = (58, 66, 100)  # a muted step of CARD_ACTIVE

# --------------------------------------------------------------------------- shared layout geometry
# Imported directly from video.py (never redefined) - this IS the alignment: Radar's content
# band, section-title position, and disclaimer position are the exact same numbers the main
# product's own RowsScene/NiftyScene/disclaimer already use, not look-alike copies of them.
CONTENT_LEFT, CONTENT_RIGHT = X0, X1               # 50, 1030
CONTENT_WIDTH = CONTENT_RIGHT - CONTENT_LEFT
CONTENT_TOP = TOP                                   # 470 - below header/ticker/progress line
CONTENT_BOTTOM = BAND_BOTTOM                        # 1195 - top of the disclaimer/caption band
SECTION_TITLE_Y = SECTION_Y                         # 344
DISCLAIMER_Y = DISC_Y                                # 1494
TICKER_Y = TICK_Y                                    # 288 (unused - Radar has no ticker data; see below)
PROGRESS_LINE_Y = LINE_Y                             # 340

MIN_FONT_SIZE = 26

# --------------------------------------------------------------------------- shared typography scale
# The SAME sizes the main video's own scenes use (video.py: HookScene 66/168,
# Scene.section() 62/30, RowsScene._draw_context 70, disclaimer 28) - not a parallel scale that
# happens to look similar. A Radar card's "big label" uses exactly the same font(70) a main
# CONTEXT-scene card uses for its own big label, because it is visually playing the same role.
SIZE_HOOK_LABEL = 66
SIZE_HOOK_NUMBER = 168
SIZE_SECTION_TITLE = 62
SIZE_SECTION_SUBTITLE = 30
SIZE_CARD_LABEL = 70          # instrument name - matches _draw_context's big label
SIZE_FINDING = 40             # card body text - matches _draw_context's wrapped title size
SIZE_BADGE = 30
SIZE_PRICE = 44               # matches the header's date-line size, never larger than the card label
SIZE_PRICE_QUIET = 32
SIZE_EVIDENCE = 32
SIZE_CONTEXT = 28
SIZE_FAMILY_LABEL = 26
SIZE_COUNTER = 28
SIZE_DISCLAIMER = 28
SIZE_CLOSING_HEADLINE = 44
SIZE_CLOSING_SUB = 30
SIZE_BRAND_KICKER = 28
SIZE_WORDMARK = 58            # "MARKET RADAR" - smaller than the main wordmark's 74 (this is a
                              # section within the channel, not the channel's own title card)

BRAND_KICKER = "DAILY MARKET BYTE"
WORDMARK_PART1, WORDMARK_PART2 = "MARKET ", "RADAR"

__all__ = [
    "RADAR_RENDERER_VERSION", "W", "H", "FPS",
    "BACKGROUND_TOP", "BACKGROUND_BOTTOM", "SURFACE", "SURFACE_ACTIVE", "TEXT_PRIMARY",
    "TEXT_SECONDARY", "TEXT_MUTED", "BRAND_ACCENT", "POSITIVE", "NEGATIVE", "DATE_COLOR",
    "MIXED", "QUIET", "FAMILY_VOLUME", "FAMILY_STRUCTURE", "FAMILY_RELATIVE", "FAMILY_INACTIVE",
    "CONTENT_LEFT", "CONTENT_RIGHT", "CONTENT_WIDTH", "CONTENT_TOP", "CONTENT_BOTTOM",
    "SECTION_TITLE_Y", "DISCLAIMER_Y", "TICKER_Y", "PROGRESS_LINE_Y", "MIN_FONT_SIZE",
    "SIZE_HOOK_LABEL", "SIZE_HOOK_NUMBER", "SIZE_SECTION_TITLE", "SIZE_SECTION_SUBTITLE",
    "SIZE_CARD_LABEL", "SIZE_FINDING", "SIZE_BADGE", "SIZE_PRICE", "SIZE_PRICE_QUIET",
    "SIZE_EVIDENCE", "SIZE_CONTEXT", "SIZE_FAMILY_LABEL", "SIZE_COUNTER", "SIZE_DISCLAIMER",
    "SIZE_CLOSING_HEADLINE", "SIZE_CLOSING_SUB", "SIZE_BRAND_KICKER", "SIZE_WORDMARK",
    "BRAND_KICKER", "WORDMARK_PART1", "WORDMARK_PART2",
]

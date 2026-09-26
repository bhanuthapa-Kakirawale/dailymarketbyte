"""Design tokens for the unified Daily Market Byte video: one palette, one type scale, one grid.

Every scene - main market sections and the Market Radar section alike - reads its colours,
sizes and positions from here. Base colours are imported from `config.py` (the product's own
palette) rather than redefined; only semantics the main product never needed (volume, quiet,
benchmark line, highlight) are added, mixed from that same palette.
"""
from __future__ import annotations

from config import (ACCENT, BG1, BG2, CARD, CARD_ACTIVE, FPS, GREEN, H, RED, SUB, TEXT, W,
                    YELLOW)

# --------------------------------------------------------------------------- palette
BRAND = ACCENT
POSITIVE = GREEN
NEGATIVE = RED
DATE = YELLOW
HIGHLIGHT = YELLOW                 # "look here": spotlight rings, callout borders
TEXT_PRIMARY = TEXT
TEXT_SECONDARY = SUB
TEXT_MUTED = (104, 116, 156)
SURFACE = CARD
SURFACE_RAISED = CARD_ACTIVE
PANEL = (12, 19, 44)               # chart panels: darker than cards so data reads on top
PANEL_BORDER = (44, 60, 104)
GRID = (255, 255, 255, 16)
VOLUME = (255, 170, 64)            # amber - the volume family's colour
VOLUME_BASE = (58, 70, 110)
BENCHMARK = (150, 162, 196)        # Nifty's line in any comparison - calm, never competing
QUIET = (110, 190, 255)
MIXED = YELLOW
INK = (10, 14, 30)                 # dark text on bright chips

# --------------------------------------------------------------------------- canvas / safe areas
CANVAS_W, CANVAS_H = W, H
X0, X1 = 60, 1020                  # content left/right edge
CONTENT_W = X1 - X0
RIGHT_RAIL_X = 930                 # below RIGHT_RAIL_Y the Shorts action rail covers x > this
RIGHT_RAIL_Y = 1100

HEADER_BRAND_Y = 62
HEADER_DATE_Y = 124
HEADER_CHIP_Y = 170
PROGRESS_Y = 238
STAGE_TOP = 272
STAGE_BOTTOM = 1452
FOOTER_Y = 1484
RESERVED_BOTTOM = 1524             # below this: YouTube Shorts title/channel overlay - keep empty

# --------------------------------------------------------------------------- type scale
# One ladder, clearly separated steps - never five near-identical sizes on one screen.
T_BRAND = 46
T_DATE = 26
T_CHIP = 24
T_DISPLAY = 150                    # hook number
T_HERO = 104                       # a scene's single dominant number
T_SYMBOL = 84                      # stock / index name
T_HEADLINE = 52                    # level 1: what happened
T_TITLE = 42
T_BODY = 36
T_ANNOT = 30                       # chart callouts
T_LABEL = 26
T_SMALL = 24
T_DISCLAIMER = 24
MIN_FONT = 24

# --------------------------------------------------------------------------- shape / motion
RADIUS_CARD = 28
RADIUS_CHIP = 20
TRANSITION_IN = 0.30
TRANSITION_OUT = 0.22
SUPERSAMPLE = 2                    # chart layers are drawn at 2x and downsampled (anti-aliasing)

__all__ = [n for n in dir() if n.isupper()]

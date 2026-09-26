"""Readability budgets and pacing constants for the Short.

Every number here is an editorial judgement about what a person can absorb on a phone at
normal playback speed, collected in one place rather than scattered through the renderer as
magic numbers. They are deliberately conservative: the failure this phase exists to fix is a
viewer having to pause, and the cost of a scene being half a second too long is far lower
than the cost of it being half a second too short.
"""
from __future__ import annotations

EDITORIAL_VERSION = "1.0"

# --------------------------------------------------------------------- what may be shown
# A fact being valid does not entitle it to screen time. These caps are the whole point:
# the canonical report keeps everything, the Short shows what fits in a glance.
MAX_PRIMARY_WORDS = 8
MAX_SECONDARY_WORDS = 12
MAX_PRIMARY_LINES = 2
MAX_SECONDARY_LINES = 2

MAX_MAJOR_CARDS = 3
# Global cues are shown in a compact scan grid (see GLOBAL_GRID_* below), not read one at a
# time as narrative cards, so this is no longer pinned to MAX_MAJOR_CARDS.
MAX_GLOBAL_CUES = 4
MAX_SECTORS_HIGHLIGHTED = 3
MAX_MOVERS_DISPLAYED = 3
MAX_CONTEXT_INSIGHTS = 2
MAX_EVENTS = 2

# A rank is only worth its line when it sits near an end of the window. "Bigger move than 0
# of the last 20 sessions" is true, carries no information, and reads like a bug; a mid-pack
# rank is no better. Either extreme is genuinely notable and gets said - in its own words.
RANK_NOTABLE_FRACTION = 0.75

# Hard ceilings. Between the soft cap and the hard cap a scene warns; beyond the hard cap it
# blocks, because at that point the text genuinely cannot be read in the time available.
HARD_PRIMARY_WORDS = 11
HARD_SECONDARY_WORDS = 16

# --------------------------------------------------------------------- reading time
# A conservative mobile-reading approximation, not cognitive science. The goal is only to
# stop "35 words + 5 cards + a chart" ever being paired with a 4-second scene.
WORDS_PER_SECOND = 2.6          # effective, for glanced text on a phone
BASE_PROCESSING_SECONDS = 1.2   # orienting to a new scene before reading anything
CARD_SECONDS = 0.6              # each additional card the eye must land on
CHART_SECONDS = 1.2             # a chart needs a beat of its own
VALUE_SECONDS = 0.35            # each large number is a separate fixation

# --------------------------------------------------------------------- pacing
# (minimum, maximum) seconds per scene type. Duration is the estimated reading time clamped
# into this range, so a light scene stays brisk and a heavy one gets room.
SCENE_BOUNDS = {
    "HOOK": (2.8, 4.5),
    # A scan-grid of 2-4 cues, glanced at once - see GLOBAL_GRID_* below.
    "GLOBAL": (3.5, 4.5),
    "NIFTY": (5.0, 7.5),
    # Two highly scannable values (FII net, DII net) - see FLOWS_SCAN_* below. The upper
    # bound stays generous enough that a genuine streak line (extra prose) is never forced
    # to block rather than simply take the extra beat it needs.
    "FLOWS": (3.5, 6.0),
    "SECTORS": (3.5, 7.0),
    "MOVERS": (4.5, 8.0),
    "GAINERS": (3.5, 7.0),
    "LOSERS": (3.5, 7.0),
    "CONTEXT": (4.0, 7.0),
    "EVENTS": (4.0, 6.5),
    # A sign-off carries no information to absorb - a CTA is recognised, not read - so its
    # bound is about pacing rather than reading time. Kept short on purpose: branding should
    # not spend five seconds of a sixty-second Short.
    "OUTRO": (1.8, 3.0),
}

# Rows a scene must keep even if the reading budget is tight. Dropping to zero would leave a
# titled but empty card, which is worse than a slightly busy one - better to keep the pair
# and let readability QA warn than to publish a scene that says nothing.
MIN_ITEMS = {"FLOWS": 2, "SECTORS": 2, "MOVERS": 2, "GLOBAL": 2, "CONTEXT": 1, "EVENTS": 1,
            "GAINERS": 1, "LOSERS": 1}


def min_items_for(scene_type: str) -> int:
    return MIN_ITEMS.get(scene_type, 0)

# --------------------------------------------------------------------- scan/heatmap presentation
# A NARRATIVE scene (the caps above) is read card by card, so a hard cap on how many items
# compete for serial attention is exactly the right rule. A SCAN/HEATMAP presentation is a
# different reading mode: cells are registered in parallel by a scanning eye, not fixated on
# one at a time, so it is governed by its own, much higher, limit instead of stretching the
# narrative one - reducing sector breadth to only the extremes loses information a retail
# viewer can otherwise take in at a glance.
MIN_SECTORS_FOR_HEATMAP = 5     # below this a 3-column grid is an awkward 3+1 or 3+2; the
                                  # narrative strongest/weakest layout already covers it better
MAX_HEATMAP_CARDS = 13          # what a 3-column grid can show clearly on one 1080x1920 frame
                                  # without a cell shrinking past a readable size
HEATMAP_COLUMNS = 3

# A ranked-mover scan list (Top 5 Gainers / Top 5 Losers) is the same reading mode as the
# heatmap - registered by a scanning eye moving down a column, not fixated card by card - so
# it gets its own cap well above MAX_MOVERS_DISPLAYED/MAX_MAJOR_CARDS instead of stretching
# the narrative ones.
MAX_RANKED_MOVERS = 5
# POST freeze - universe coverage gate. A top-gainer / top-loser ranking (and every Movers
# claim built on it) is published only when at least this share of the mover universe
# produced a validated, date-aligned move. Below it - or with no coverage record at all - the
# ranking is unproven: the Movers scenes are suppressed, never ranked over what happened to
# arrive (see editorial/movers_gate.py).
MOVERS_MIN_COVERAGE_PCT = 90.0

# Priced like the heatmap: a base orientation cost plus a small flat cost per row, not the
# narrative per-card/per-value cost `estimate_scene` uses elsewhere - a rank, a symbol and a
# percentage register in one glance, they are not read as prose.
RANKED_MOVERS_BASE_SECONDS = 1.4
RANKED_MOVERS_ROW_SECONDS = 0.45

# Heatmap colour intensity: a cell's percent move, clipped and linearly scaled to [-1, 1].
# Clipping (not compressing) keeps the mapping linear and easy to reason about, and stops one
# outlier cell from washing every other cell's colour toward a uniform pale shade - a move
# this large is rare, and when it happens that cell simply reads as fully saturated rather
# than stretching the whole grid's scale around it.
HEATMAP_CLIP_PCT = 3.0
# How far a cell's BACKGROUND may blend toward full green/red, capped well under 1.0 so even
# the most extreme cell keeps enough contrast for its text to stay readable. The percentage
# figure itself is still drawn in the same full-strength green/red used everywhere else in
# the app - the background carries magnitude, the text carries the fact, and colour is never
# the only carrier of positive/negative (the sign is always in the text too).
HEATMAP_MAX_BG_MIX = 0.55

# A heatmap is scanned as a whole grid, not read card by card - the per-card/per-value
# reading costs above are calibrated for narrative scenes and would demand an absurd ~18s
# scene for 13 cells read that way. These price a heatmap on its own, much smaller, scale.
HEATMAP_BASE_SECONDS = 1.6      # orienting to a grid, a touch more than to a single number
HEATMAP_CELL_SECONDS = 0.18     # a cell registers in a glance; it is not fixated serially

# GLOBAL is a compact 2-4 cell scan grid (name + signed %, no prose per cell), the same
# reading mode as the heatmap but smaller - priced on its own scale rather than the
# per-card/per-value narrative cost. Tuned so a typical 2-4 cue scan lands ~3.5-4.5s once
# its one line of secondary prose ("Overnight, before the open") is added.
GLOBAL_GRID_BASE_SECONDS = 1.3
GLOBAL_GRID_CELL_SECONDS = 0.4

# FII/DII is two highly scannable panels (a label chip, a subject, one number each) - glanced
# at, not read serially - priced the same way rather than the narrative per-card/per-value
# cost, so the ordinary two-item scene lands close to its 3.5-4.0s floor. A genuine streak
# line (real extra prose) still adds its own real reading time on top, rather than being
# hidden inside a flat per-card cost.
FLOWS_SCAN_BASE_SECONDS = 1.7
FLOWS_SCAN_ITEM_SECONDS = 0.95

# The Short's duration follows its content. These are guardrails for QA, not a target: a day
# with little to say should produce a shorter video, never padding to reach a number.
TARGET_MIN_DURATION = 45.0
TARGET_MAX_DURATION = 60.0
MIN_SHORT_DURATION = 15.0       # below this something has gone wrong
MAX_SHORT_DURATION = 70.0       # above this it is no longer a Short-shaped recap

# --------------------------------------------------------------------- motion
# Scenes the viewer has to read carefully hide the scrolling ticker. Competing motion while
# someone is reading a number is the single cheapest thing to remove.
TICKER_SCENES = {"HOOK", "GLOBAL", "OUTRO"}

# --------------------------------------------------------------------- typography floors
# Minimum on-screen sizes at 1080x1920, judged for a phone rather than a desktop preview.
MIN_PRIMARY_VALUE_SIZE = 96
MIN_PRIMARY_TEXT_SIZE = 46
MIN_SECONDARY_TEXT_SIZE = 30
MIN_ITEM_TITLE_SIZE = 34
MIN_LABEL_SIZE = 22


def bounds_for(scene_type: str) -> tuple:
    return SCENE_BOUNDS.get(scene_type, (3.5, 6.0))


__all__ = [name for name in dir() if name.isupper()] + ["bounds_for", "min_items_for"]

"""How long a scene needs to be readable, and how long it therefore runs.

    estimated_read_seconds =
        base visual processing
      + words / effective reading speed
      + a penalty per card
      + a penalty for a chart
      + a penalty per large number

Then:

    planned_duration = clamp(estimated_read_seconds, scene minimum, scene maximum)

The estimate is not precise and does not need to be. It exists so that duration is a
function of how much there is to read, rather than a constant every scene is squeezed into -
which is how a dense scene ends up on screen for four seconds.

When a scene cannot fit even at its maximum duration, the planner drops items rather than
speeding the viewer up. That is why `fits` and `trim_to_fit` live here: the readability
budget decides what survives, not the other way round.
"""
from __future__ import annotations

from .config import (BASE_PROCESSING_SECONDS, CARD_SECONDS, CHART_SECONDS,
                     FLOWS_SCAN_BASE_SECONDS, FLOWS_SCAN_ITEM_SECONDS,
                     GLOBAL_GRID_BASE_SECONDS, GLOBAL_GRID_CELL_SECONDS,
                     HEATMAP_BASE_SECONDS, HEATMAP_CELL_SECONDS,
                     RANKED_MOVERS_BASE_SECONDS, RANKED_MOVERS_ROW_SECONDS, VALUE_SECONDS,
                     WORDS_PER_SECOND, bounds_for)


def estimate_read_seconds(words: int, cards: int = 0, has_chart: bool = False,
                          values: int = 0) -> float:
    """Conservative seconds needed to take in a scene. See the module docstring."""
    seconds = BASE_PROCESSING_SECONDS
    seconds += max(0, words) / WORDS_PER_SECOND
    seconds += max(0, cards) * CARD_SECONDS
    seconds += max(0, values) * VALUE_SECONDS
    if has_chart:
        seconds += CHART_SECONDS
    return round(seconds, 2)


def _is_heatmap(scene) -> bool:
    return scene.metadata.get("presentation") == "HEATMAP"


def estimate_heatmap_scene(scene) -> float:
    """Seconds to scan a heatmap grid, not read it.

    A heatmap is registered as a whole by a scanning eye, so the per-card/per-value costs
    `estimate_scene` uses for narrative scenes do not apply - pricing 13 cells that way would
    demand an absurd ~18s scene for something designed to be taken in in one glance. This
    prices a base orientation cost, the one line of prose (the breadth summary, read at
    normal speed since it IS prose), and a small flat cost per cell.
    """
    seconds = HEATMAP_BASE_SECONDS
    seconds += scene.secondary_words() / WORDS_PER_SECOND
    seconds += len(scene.items) * HEATMAP_CELL_SECONDS
    return round(seconds, 2)


def _is_scan(scene) -> bool:
    return scene.metadata.get("presentation") == "SCAN"


def _is_global_grid(scene) -> bool:
    return scene.metadata.get("presentation") == "GLOBAL_SCAN"


def estimate_global_scene(scene) -> float:
    """Seconds to scan a 2-4 cell GLOBAL grid, not read it cue by cue.

    Same reasoning as `estimate_heatmap_scene`: a name and a signed percentage register in
    one glance around the grid, so the narrative per-card/per-value cost does not apply.
    """
    seconds = GLOBAL_GRID_BASE_SECONDS
    seconds += scene.secondary_words() / WORDS_PER_SECOND
    seconds += len(scene.items) * GLOBAL_GRID_CELL_SECONDS
    return round(seconds, 2)


def _is_flows_scan(scene) -> bool:
    return scene.metadata.get("presentation") == "FLOWS_SCAN"


def estimate_flows_scene(scene) -> float:
    """Seconds to scan the FII/DII panels, not read them as narrative cards.

    Two highly scannable values - a label, a subject and one number each - register in a
    glance, so this prices a base orientation cost, the (optional) streak line as real prose,
    and a small flat cost per panel, rather than the heavier narrative per-card/per-value cost.
    """
    seconds = FLOWS_SCAN_BASE_SECONDS
    seconds += scene.secondary_words() / WORDS_PER_SECOND
    seconds += len(scene.items) * FLOWS_SCAN_ITEM_SECONDS
    return round(seconds, 2)


def estimate_ranked_movers_scene(scene) -> float:
    """Seconds to scan a Top 5 Gainers/Losers list, not read it row by row.

    Same reasoning as `estimate_heatmap_scene`: a rank, a symbol and a percentage register in
    one glance down a column, so the narrative per-card/per-value cost does not apply. Prices
    a base orientation cost, the one line of prose (if any), and a small flat cost per row.
    """
    seconds = RANKED_MOVERS_BASE_SECONDS
    seconds += scene.secondary_words() / WORDS_PER_SECOND
    seconds += len(scene.items) * RANKED_MOVERS_ROW_SECONDS
    return round(seconds, 2)


def estimate_scene(scene) -> float:
    """Reading load of a planned scene.

    Uses `reading_words` rather than everything on screen: formatted numbers and chips are
    glanced at and already priced per fixation, so counting them as prose as well would
    double-charge exactly the scenes the Short is built around.

    A scene flagged HEATMAP or SCAN in its metadata is scanned as a whole, not read card by
    card, and is priced on its own scale instead - see `estimate_heatmap_scene` and
    `estimate_ranked_movers_scene`. Every other scene's estimate is unchanged.
    """
    if _is_heatmap(scene):
        return estimate_heatmap_scene(scene)
    if _is_scan(scene):
        return estimate_ranked_movers_scene(scene)
    if _is_global_grid(scene):
        return estimate_global_scene(scene)
    if _is_flows_scan(scene):
        return estimate_flows_scene(scene)
    return estimate_read_seconds(words=scene.reading_words(), cards=scene.card_count(),
                                 has_chart=scene.has_chart, values=scene.value_count())


def plan_duration(scene) -> float:
    """Estimated reading time, clamped into the scene type's pacing range."""
    low, high = bounds_for(scene.scene_type.value)
    return round(min(max(estimate_scene(scene), low), high), 2)


def fits(scene) -> bool:
    """Whether the scene can be read inside the longest duration its type allows."""
    _, high = bounds_for(scene.scene_type.value)
    return estimate_scene(scene) <= high


def apply_timing(scene):
    """Attach the estimate and the resulting duration to a scene, in place."""
    scene.estimated_read_seconds = estimate_scene(scene)
    scene.planned_duration = plan_duration(scene)
    return scene


def trim_to_fit(scene, minimum_items: int = 1):
    """Drop trailing items until the scene is readable, returning what was dropped.

    Editorial omission, not truncation: a whole row leaves rather than every row shrinking
    into unreadable text. Nothing is removed from the canonical report - this only decides
    what earns screen time.
    """
    dropped = []
    while not fits(scene) and len(scene.items) > minimum_items:
        dropped.append(scene.items.pop())
    apply_timing(scene)
    return dropped


__all__ = ["estimate_read_seconds", "estimate_scene", "estimate_heatmap_scene",
           "estimate_ranked_movers_scene", "estimate_global_scene", "estimate_flows_scene",
           "plan_duration", "fits", "apply_timing", "trim_to_fit"]

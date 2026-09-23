"""Editorial layer: deciding what the Short says, not what the market did.

The canonical MarketReport holds everything validated. This package decides which small part
of it earns screen time, what opens the video, and how long each scene needs to be readable
on a phone at normal playback speed.

Everything here is derived presentation state - reproducible from the report and the
intelligence snapshot, never persisted as canonical fact, and never written back into either.
Selection is rule-based and deterministic: no model chooses the hook or the facts.
"""
from .config import (EDITORIAL_VERSION, HEATMAP_CLIP_PCT, HEATMAP_COLUMNS,
                     HEATMAP_MAX_BG_MIX, MAX_CONTEXT_INSIGHTS, MAX_EVENTS, MAX_GLOBAL_CUES,
                     MAX_HEATMAP_CARDS,
                     MAX_MOVERS_DISPLAYED, MAX_PRIMARY_WORDS, MAX_RANKED_MOVERS,
                     MAX_SECONDARY_WORDS,
                     MAX_SECTORS_HIGHLIGHTED, MAX_SHORT_DURATION, MIN_SECTORS_FOR_HEATMAP,
                     MIN_SHORT_DURATION, TARGET_MAX_DURATION, TARGET_MIN_DURATION, bounds_for)
from .hook import HookCandidate
from .hook import candidates as hook_candidates
from .hook import select as select_hook
from .models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from .planner import context_line, gainers_scene, losers_scene, plan_short
from .readability import (estimate_heatmap_scene, estimate_ranked_movers_scene,
                          estimate_read_seconds, estimate_scene, fits, plan_duration)

__all__ = ["plan_short", "context_line", "ShortsPlan", "ScenePlan", "EditorialItem", "SceneType",
           "select_hook", "hook_candidates", "HookCandidate", "estimate_read_seconds",
           "estimate_scene", "estimate_heatmap_scene", "estimate_ranked_movers_scene",
           "plan_duration", "fits", "bounds_for",
           "EDITORIAL_VERSION", "MIN_SHORT_DURATION", "MAX_SHORT_DURATION",
           "TARGET_MIN_DURATION", "TARGET_MAX_DURATION", "MAX_PRIMARY_WORDS",
           "MAX_SECONDARY_WORDS", "MAX_GLOBAL_CUES", "MAX_SECTORS_HIGHLIGHTED",
           "MAX_MOVERS_DISPLAYED", "MAX_CONTEXT_INSIGHTS", "MAX_EVENTS",
           "MIN_SECTORS_FOR_HEATMAP", "MAX_HEATMAP_CARDS", "HEATMAP_COLUMNS",
           "HEATMAP_CLIP_PCT", "HEATMAP_MAX_BG_MIX", "MAX_RANKED_MOVERS",
           "gainers_scene", "losers_scene"]

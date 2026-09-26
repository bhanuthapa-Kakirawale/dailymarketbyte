"""Dynamic Hook Engine (Phase 1): the first 4-5 seconds of every Daily Market Byte Short.

    sheet_post / sheet_pre / sheet_custom   validated inputs -> HookFactSheet (facts + beats)
    candidates    deterministic HookCandidateBuilder: 7 archetypes, self-validated templates
    gemini        runtime editor: chooses among approved candidates/beats, writes the lines
    validation    strict checks on every line and choice (Gemini's and our own)
    engine        plan_hook(): Gemini if it passes, else the deterministic fallback - always
                  returns a HookPlan
    models / policy / text   vocabulary, thresholds and word lists, shared text helpers

Rendering lives in `daily_video.hook_scene` (teaser beats + hero visuals). See
docs/HOOK_ENGINE.md.
"""
from .candidates import build_candidates
from .engine import plan_hook, timing_for
from .models import (Archetype, BeatKind, HeroVisual, HookCandidate, HookFact, HookFactSheet,
                     HookMode, HookPlan, HookSource, TeaserBeatOption)
from .sheet_custom import CustomStockInputs, custom_stock_sheet
from .sheet_post import post_market_sheet
from .sheet_pre import PreMarketInputs, pre_market_inputs_from_plan, pre_market_sheet

__all__ = ["plan_hook", "timing_for", "build_candidates", "post_market_sheet",
           "pre_market_sheet", "pre_market_inputs_from_plan", "custom_stock_sheet",
           "PreMarketInputs", "CustomStockInputs", "Archetype", "BeatKind", "HeroVisual",
           "HookCandidate", "HookFact", "HookFactSheet", "HookMode", "HookPlan", "HookSource",
           "TeaserBeatOption"]

"""The Dynamic Hook Engine: validated facts in, one validated opening out - always.

    HookFactSheet ──> build_candidates (deterministic, templates self-validated)
                           │
                           ├──> Gemini (optional): chooses candidate / hero / beats, writes lines
                           │         └──> validate_response ── pass ──> GEMINI
                           │                                  └ lines fail, choice valid
                           │                                          ──> GEMINI_CHOICE_TEMPLATE_TEXT
                           └──> top candidate as templated ──> DETERMINISTIC (fallback)

The video never fails because Gemini failed: no key, a network error, a 429, malformed JSON,
an invented number or a forbidden word all land on the deterministic hook, and the plan
records exactly which of those happened.
"""
from __future__ import annotations

from . import policy
from .candidates import build_candidates, emergency_candidate
from .gemini import build_prompt, default_client, parse, response_schema
from .models import (EYEBROW, HeroVisual, HookFactSheet, HookMode, HookPlan, HookSource,
                     HookTiming)
from .validation import check_text, validate_response

_UNSET = object()


def timing_for(n_beats: int) -> HookTiming:
    n = max(0, min(policy.MAX_BEATS, n_beats))
    if n == 0:
        return HookTiming(beat_seconds=0.0, settle_start=0.0, total=3.2)
    if n == 1:
        return HookTiming(beat_seconds=0.95, settle_start=0.95, total=3.4)
    beat = policy.BEAT_SECONDS[n]
    settle = policy.SETTLE_SECONDS[n]
    return HookTiming(beat_seconds=beat, settle_start=round(n * beat, 3),
                      total=round(n * beat + settle, 3))


def beat_entities(sheet: HookFactSheet, beat_id: str) -> set:
    b = sheet.beat(beat_id)
    if b is None:
        return set()
    return {f.entity for f in (sheet.fact(fid) for fid in b.fact_ids) if f is not None}


def curiosity_entity(sheet: HookFactSheet, curiosity: str) -> str | None:
    """The entity the curiosity line is about: its first named entity, read with the same
    alias index the validator uses."""
    from .validation import _alias_index, _find_mentions
    ments = _find_mentions(curiosity or "", _alias_index(sheet))
    return ments[0][2] if ments else None


def lead_beat_first(sheet: HookFactSheet, curiosity: str, beats) -> list:
    """PRE V1 polish: the first (full-stage) teaser beat shows the entity the curiosity line
    names, whenever the sheet has a beat for it; the other beats follow as supporting or
    contrasting facts. One beat per entity (a later beat that shares an entity with the lead
    is dropped), at most MAX_BEATS. No beat for that entity -> the beats are unchanged.
    PRE only: POST beat order is governed by hooks/diversity and is not touched."""
    beats = list(beats)
    if sheet.mode is not HookMode.PRE_MARKET:
        return beats
    entity = curiosity_entity(sheet, curiosity)
    if entity is None:
        return beats
    lead = next((b.beat_id for b in sheet.beats if entity in beat_entities(sheet, b.beat_id)), None)
    if lead is None:
        return beats
    lead_ents = beat_entities(sheet, lead)
    rest = [b for b in beats if b != lead and not (beat_entities(sheet, b) & lead_ents)]
    return ([lead] + rest)[:policy.MAX_BEATS]


def _plan(sheet, cand, hero, beats, curiosity, summary, source, reason=None, issues=(),
          raw=None, offered=()):
    hero = hero if hero in cand.heroes else cand.default_hero
    ordered = lead_beat_first(sheet, curiosity, beats)
    if ordered != list(beats) and not check_text(curiosity, summary, sheet, cand, ordered):
        beats = ordered
    return HookPlan(
        mode=sheet.mode, archetype=cand.archetype, candidate_id=cand.candidate_id,
        eyebrow=EYEBROW[cand.archetype], curiosity_line=curiosity, summary_line=summary,
        hero_visual=hero, hero_payload=cand.heroes[hero],
        hero_strings=tuple(cand.hero_strings.get(hero, ())), teaser_beats=list(beats),
        fact_ids=list(cand.fact_ids), agenda=list(sheet.section_labels),
        timing=timing_for(len(beats)), source=source, fallback_reason=reason,
        validation_issues=list(issues), gemini_response=raw[:4000] if raw else None,
        candidates=[(c.candidate_id, c.archetype.value, c.score) for c in offered],
        priority_rule=sheet.metadata.get("hook_priority"))


def plan_hook(sheet: HookFactSheet, client=_UNSET, use_ai: bool = True) -> HookPlan:
    """The hook for `sheet`. `client` is `(prompt, schema) -> str | None`; left unset it is
    the real Gemini client when a key is configured, else no model is called at all."""
    cands = build_candidates(sheet)
    if not cands:
        cand = emergency_candidate(sheet)
        return _plan(sheet, cand, cand.default_hero, cand.default_beats, cand.curiosity_line,
                     cand.summary_line, HookSource.DETERMINISTIC,
                     "no candidate passed validation", offered=[cand])
    top = cands[0]

    def fallback(reason, issues=(), raw=None):
        return _plan(sheet, top, top.default_hero, top.default_beats, top.curiosity_line,
                     top.summary_line, HookSource.DETERMINISTIC, reason, issues, raw, cands)

    if not use_ai:
        return fallback("AI disabled")
    if client is _UNSET:
        client = default_client()
    if client is None:
        return fallback("no Gemini key configured")
    try:
        raw = client(build_prompt(sheet, cands), response_schema(sheet, cands))
    except Exception as e:                       # never let the model cost a video
        return fallback(f"Gemini call raised {type(e).__name__}: {e}")
    if not raw:
        return fallback("Gemini returned nothing (quota, network or API error)")
    obj = parse(raw)
    if obj is None:
        return fallback("Gemini reply was not a JSON object", raw=raw)

    s_issues, t_issues, cand, cur, summ = validate_response(obj, sheet, cands)
    if s_issues:
        return fallback("Gemini choice failed structural validation", s_issues + t_issues, raw)
    beats = list(obj["teaser_beats"])
    hero = HeroVisual(obj["hero_visual"])
    if not t_issues:
        return _plan(sheet, cand, hero, beats, cur, summ, HookSource.GEMINI, None, (), raw, cands)
    # Gemini's choice was one of ours; its wording was not. Keep the choice, use our lines -
    # which were validated for that candidate, and are re-checked against its beats here.
    if not check_text(cand.curiosity_line, cand.summary_line, sheet, cand, beats):
        return _plan(sheet, cand, hero, beats, cand.curiosity_line, cand.summary_line,
                     HookSource.GEMINI_CHOICE_TEMPLATE_TEXT,
                     "Gemini lines failed text validation", t_issues, raw, cands)
    return fallback("Gemini lines failed text validation", t_issues, raw)


__all__ = ["plan_hook", "timing_for", "lead_beat_first", "curiosity_entity", "beat_entities"]

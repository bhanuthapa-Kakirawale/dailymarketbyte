"""Gemini as the hook's runtime editor - choosing, never inventing.

Gemini receives the validated fact sheet, 1-5 deterministic approved candidates and the
approved teaser beats, and returns structured JSON: which candidate, which of its approved
hero visuals, which 2-3 beats in what order, and a curiosity line + summary line written only
from the supplied facts. `validation.py` then checks every field; anything it rejects falls
back to the deterministic hook. Gemini never sees raw data, never computes, and cannot add a
fact, a number, a layout or a visual that was not offered.

One non-grounded call per run (no Google Search tool - the facts are supplied, so grounding
quota is not spent), short timeout, two tries, 429 fails fast - all through the existing
`news.ask_gemini` client so key handling and the known 404/503 behaviour stay in one place.
"""
from __future__ import annotations

import json
import os

from . import diversity, policy
from .models import HookFactSheet

PROMPT_VERSION = "hook-1.1"

RULES = f"""You are the opening editor of "Daily Market Byte", a 45-60 second faceless YouTube
Short about the Indian stock market (NSE). Informational only - never advice. Pick how today's
Short opens: the first ~4.5 seconds decide whether a trader keeps watching.

Choose ONE candidate, ONE of its allowed hero visuals, and 2-3 teaser beats (short visual cuts
that play before the hook settles, in the order you list them - pick visually varied beats that
build toward the hero). Then write two lines:
  curiosity_line  - the single most interesting validated fact or contrast, as a scroll-stopper.
                    Max {policy.CURIOSITY_MAX_CHARS} characters, {policy.CURIOSITY_MAX_WORDS} words.
  summary_line    - what the viewer will SEE in this Short (only the sections listed), max
                    {policy.SUMMARY_MAX_CHARS} characters, {policy.SUMMARY_MAX_WORDS} words.

HARD RULES - any violation discards your whole answer:
1. Use ONLY the supplied facts. Copy every number exactly as written in a fact's "display"
   (you may drop its leading + or - sign when a verb gives the direction). No rounding, no new
   numbers, no arithmetic, no indicators, no dates or times that are not in a fact.
2. Name only entities that appear in the facts. Write a number next to the entity it belongs to.
3. Never say why anything moved: no because, due to, after, amid, on, driven by, led by, thanks
   to, triggered, reason, behind, explains.
4. Never predict: no will, could, may, might, should, likely, expect, set to, poised, next,
   tomorrow, target, upside, downside, gap-up, gap-down, outlook, points to.
5. Never recommend: no buy, sell, accumulate, invest, opportunity, worth watching, picks, bets,
   entry, exit, stop loss, target price, returns, profit.
6. No hype: no surge, soar, crash, plunge, massive, huge, record, historic, stunning, ever.
7. A characterisation or superlative (just, quiet, big, led, top, biggest, broke, opposite,
   strong, weak ...) is allowed only if a fact you cite lists it under "supports_words_for".
8. The curiosity_line must name an entity or number from the chosen candidate's facts.
9. Start each sentence with an entity name from the facts or a plain common word.
10. archetype must equal the chosen candidate's archetype; hero_visual must be one of its
    allowed_hero_visuals; teaser_beats must be beat_ids from the list.
Each candidate's example lines are valid; you may reuse them or write better ones.
Return JSON only."""


def response_schema(sheet: HookFactSheet, candidates: list) -> dict:
    """Gemini `responseSchema` (OpenAPI subset). Enums pin every choice to what was offered;
    the validator re-checks everything anyway, since a schema is a request, not a guarantee."""
    heroes = sorted({h.value for c in candidates for h in c.heroes})
    n_beats = len(sheet.beats)
    return {
        "type": "OBJECT",
        "properties": {
            "candidate_id": {"type": "STRING", "enum": [c.candidate_id for c in candidates]},
            "archetype": {"type": "STRING",
                          "enum": sorted({c.archetype.value for c in candidates})},
            "hero_visual": {"type": "STRING", "enum": heroes},
            "teaser_beats": {"type": "ARRAY",
                             "items": {"type": "STRING", "enum": [b.beat_id for b in sheet.beats]},
                             "minItems": min(policy.MIN_BEATS, n_beats,
                                             diversity.max_diverse(sheet, policy.MAX_BEATS)
                                             if diversity.applies(sheet) else n_beats),
                             "maxItems": min(policy.MAX_BEATS, n_beats)},
            "curiosity_line": {"type": "STRING"},
            "summary_line": {"type": "STRING"},
            "fact_ids_used": {"type": "ARRAY",
                              "items": {"type": "STRING", "enum": [f.fact_id for f in sheet.facts]}},
        },
        "required": ["candidate_id", "archetype", "hero_visual", "teaser_beats",
                     "curiosity_line", "summary_line", "fact_ids_used"],
        "propertyOrdering": ["candidate_id", "archetype", "hero_visual", "teaser_beats",
                             "curiosity_line", "summary_line", "fact_ids_used"],
    }


POST_BEAT_RULES = """
TEASER BEATS (this is a POST-market Short):
11. Never pick two teaser beats that share an entity (see each beat's "entities") - one beat
    per stock, index or sector. Prefer beats from different "family" values, played in the
    order INDEX, then BREADTH (sector / flow), then STOCK. Two beats are fine when a third would
    repeat an entity or family."""


def build_prompt(sheet: HookFactSheet, candidates: list) -> str:
    payload = {
        "mode": sheet.mode.value,
        "session": sheet.session_date.strftime("%A %d %B %Y"),
        "sections_in_this_short": [{"key": k, "label": lab}
                                   for k, lab in zip(sheet.sections, sheet.section_labels)],
        "facts": [f.to_prompt() for f in sheet.facts],
        "candidates": [c.to_prompt() for c in candidates],
        "teaser_beats": [b.to_prompt() for b in sheet.beats],
    }
    rules = RULES
    if diversity.applies(sheet):
        rules += POST_BEAT_RULES
        for b in payload["teaser_beats"]:
            beat = sheet.beat(b["beat_id"])
            b["entities"] = sorted(diversity.entities(sheet, beat))
            b["family"] = diversity.family(beat)
    return rules + "\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def default_client():
    """A `(prompt, schema) -> str | None` callable backed by the free Gemini API, or None when
    no key is configured (the engine then goes straight to the deterministic hook)."""
    if not os.getenv("GEMINI_API_KEY"):
        return None

    def call(prompt: str, schema: dict):
        from news import ask_gemini
        return ask_gemini(prompt, search=False, tries=2, timeout=45,
                          generation_config={"temperature": 0.5,
                                             "responseMimeType": "application/json",
                                             "responseSchema": schema})
    return call


def parse(raw: str | None):
    """The JSON object in Gemini's reply, or None. Accepts a fenced block defensively."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        obj = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
        except Exception:
            return None
    return obj if isinstance(obj, dict) else None


__all__ = ["RULES", "PROMPT_VERSION", "response_schema", "build_prompt", "default_client", "parse"]

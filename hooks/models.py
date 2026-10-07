"""The vocabulary of the Dynamic Hook Engine.

    HookFact          one validated fact, already formatted, with the words it licenses
    TeaserBeatOption  one approved 0.9s visual beat, built from validated data
    HookCandidate     one approved way to open the Short (archetype + facts + hero + lines)
    HookFactSheet     everything the engine - and Gemini - is allowed to see
    HookPlan          the chosen opening, with a full audit trail of how it was chosen

Nothing in this module fetches or calculates a market value. A HookFact carries a number
somebody upstream validated, the exact strings that number may be written as, and the
characterisations ("quiet", "leader", "breakout") that the deterministic rules found true.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from enum import Enum


class HookMode(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    POST_MARKET = "POST_MARKET"
    CUSTOM_SINGLE_STOCK = "CUSTOM_SINGLE_STOCK"


class Archetype(str, Enum):
    """The story SHAPE the curiosity line exploits - not a screen layout. Every archetype
    shares one visual system; what changes is the hero graphic and the editorial priority."""
    QUIET_MARKET_HIDDEN_ACTION = "QUIET_MARKET_HIDDEN_ACTION"  # small headline, big underneath
    BIG_MOVE = "BIG_MOVE"                                      # one number is the story
    CONTRAST = "CONTRAST"                                      # two things went opposite ways
    UNUSUAL_ACTIVITY = "UNUSUAL_ACTIVITY"                      # signals stacked on one name
    OVERNIGHT_CUE = "OVERNIGHT_CUE"                            # the world moved while India slept
    EVENT_LED = "EVENT_LED"                                    # a dated, named event
    THINGS_TO_KNOW = "THINGS_TO_KNOW"                          # a numbered briefing (universal)


class HeroVisual(str, Enum):
    DEPTH_LOLLIPOP = "DEPTH_LOLLIPOP"          # index at the 0% waterline, stocks far from it
    HEADLINE_NUMBER = "HEADLINE_NUMBER"        # one giant number over its candle strip
    VERSUS_SPLIT = "VERSUS_SPLIT"              # two panels, diverging bars, a VS badge
    SIGNAL_STACK_CHART = "SIGNAL_STACK_CHART"  # price event + volume spike + signal chips
    OVERNIGHT_BOARD = "OVERNIGHT_BOARD"        # lead global cue + the rest of the board
    EVENT_CALENDAR = "EVENT_CALENDAR"          # calendar page + event card
    NUMBERED_LIST = "NUMBERED_LIST"            # 1-2-3 briefing rows


class BeatKind(str, Enum):
    """Renderer for one teaser beat. Each is a quick cut of a real scene's visual language."""
    LINE = "LINE"                  # a price line drawing to its last point
    SECTOR_PAIR = "SECTOR_PAIR"    # leader vs laggard tiles slam in
    SECTOR_TILE = "SECTOR_TILE"    # one heat tile
    MOVER_BAR = "MOVER_BAR"        # one stock, one bar
    BREAKOUT = "BREAKOUT"          # range band + event marker
    VOLUME = "VOLUME"              # bars with the amber spike
    RADAR = "RADAR"                # sweep lighting one blip per flagged stock
    FLOWS = "FLOWS"                # FII vs DII bars
    CUE = "CUE"                    # an overnight tile
    EVENT = "EVENT"                # a calendar card
    METRIC = "METRIC"              # one fundamental figure


# Which heroes each archetype may use, in preference order. Gemini may pick any listed one
# that the candidate actually has data for; nothing else.
ARCHETYPE_HEROES = {
    Archetype.QUIET_MARKET_HIDDEN_ACTION: (HeroVisual.DEPTH_LOLLIPOP, HeroVisual.SIGNAL_STACK_CHART,
                                          HeroVisual.HEADLINE_NUMBER),
    Archetype.BIG_MOVE: (HeroVisual.HEADLINE_NUMBER,),
    Archetype.CONTRAST: (HeroVisual.VERSUS_SPLIT,),
    Archetype.UNUSUAL_ACTIVITY: (HeroVisual.SIGNAL_STACK_CHART, HeroVisual.DEPTH_LOLLIPOP),
    Archetype.OVERNIGHT_CUE: (HeroVisual.OVERNIGHT_BOARD, HeroVisual.HEADLINE_NUMBER),
    Archetype.EVENT_LED: (HeroVisual.EVENT_CALENDAR,),
    Archetype.THINGS_TO_KNOW: (HeroVisual.NUMBERED_LIST,),
}

# The small label above the curiosity line: fixed per archetype, never model-written, so the
# viewer learns to read the family at a glance across days.
EYEBROW = {
    Archetype.QUIET_MARKET_HIDDEN_ACTION: "UNDER THE SURFACE",
    Archetype.BIG_MOVE: "THE BIG NUMBER",
    Archetype.CONTRAST: "SPLIT SESSION",
    Archetype.UNUSUAL_ACTIVITY: "UNUSUAL ACTIVITY",
    Archetype.OVERNIGHT_CUE: "OVERNIGHT",
    Archetype.EVENT_LED: "ON THE CALENDAR",
    Archetype.THINGS_TO_KNOW: "QUICK BRIEFING",
}

MODE_ARCHETYPES = {
    HookMode.POST_MARKET: (Archetype.QUIET_MARKET_HIDDEN_ACTION, Archetype.BIG_MOVE,
                           Archetype.CONTRAST, Archetype.UNUSUAL_ACTIVITY, Archetype.EVENT_LED,
                           Archetype.THINGS_TO_KNOW),
    HookMode.PRE_MARKET: (Archetype.OVERNIGHT_CUE, Archetype.EVENT_LED, Archetype.BIG_MOVE,
                          Archetype.THINGS_TO_KNOW),
    HookMode.CUSTOM_SINGLE_STOCK: (Archetype.UNUSUAL_ACTIVITY, Archetype.CONTRAST,
                                   Archetype.BIG_MOVE, Archetype.QUIET_MARKET_HIDDEN_ACTION,
                                   Archetype.THINGS_TO_KNOW),
}


@dataclass(frozen=True)
class HookFact:
    """One validated fact as the hook layer sees it.

    `numbers` are the ONLY digit strings a hook line may use to cite this fact (normalised:
    no sign, no grouping commas, no unit). `aliases` are the names it may be called by.
    `claims` are characterisations a deterministic rule found true for it - "quiet",
    "leader", "breakout" - and they are what license words like "just", "led" or "broke".
    `anchor_free` facts (counts, the session day) need no entity named next to them - but
    their number must be followed by one of `unit_words` ("5 stocks", "21 Sep"), so a count
    of five stocks cannot license "5 sectors".
    """
    fact_id: str
    kind: str
    entity: str
    statement: str
    display: str
    value: float | None = None
    polarity: int | None = None
    numbers: tuple = ()
    aliases: tuple = ()
    claims: frozenset = frozenset()
    anchor_free: bool = False
    source_refs: tuple = ()
    unit_words: tuple = ()       # an anchor-free number must be followed by one of these

    def to_prompt(self) -> dict:
        out = {"fact_id": self.fact_id, "entity": self.entity, "statement": self.statement,
               "display": self.display}
        if self.claims:
            out["supports_words_for"] = sorted(self.claims)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["claims"] = sorted(self.claims)
        return d


@dataclass
class TeaserBeatOption:
    beat_id: str
    kind: BeatKind
    description: str
    fact_ids: tuple
    payload: dict
    strings: tuple = ()          # every viewer-visible string the beat draws

    def to_prompt(self) -> dict:
        return {"beat_id": self.beat_id, "shows": self.description,
                "fact_ids": list(self.fact_ids)}


@dataclass
class HookCandidate:
    """One approved opening. Its default lines are deterministic templates that must pass the
    same validator Gemini's lines do - a candidate whose own template fails is never offered."""
    candidate_id: str
    archetype: Archetype
    score: float
    fact_ids: tuple
    heroes: dict                 # HeroVisual -> payload (only heroes with data)
    default_hero: HeroVisual
    default_beats: tuple
    curiosity_line: str
    summary_line: str
    rationale: str
    hero_strings: dict = field(default_factory=dict)   # HeroVisual -> tuple of strings

    def to_prompt(self) -> dict:
        return {"candidate_id": self.candidate_id, "archetype": self.archetype.value,
                "why_interesting": self.rationale, "fact_ids": list(self.fact_ids),
                "allowed_hero_visuals": [h.value for h in self.heroes],
                "example_curiosity_line": self.curiosity_line,
                "example_summary_line": self.summary_line}


@dataclass
class HookFactSheet:
    mode: HookMode
    session_date: dt.date
    facts: list
    beats: list
    sections: list               # section keys the Short really contains, in order
    section_labels: list         # the agenda chips, in the same order
    metadata: dict = field(default_factory=dict)

    def fact(self, fact_id: str) -> HookFact | None:
        return next((f for f in self.facts if f.fact_id == fact_id), None)

    def beat(self, beat_id: str) -> TeaserBeatOption | None:
        return next((b for b in self.beats if b.beat_id == beat_id), None)

    def facts_of_kind(self, *kinds) -> list:
        return [f for f in self.facts if f.kind in kinds]


class HookSource(str, Enum):
    GEMINI = "GEMINI"                                  # Gemini's choice and lines, validated
    GEMINI_CHOICE_TEMPLATE_TEXT = "GEMINI_CHOICE_TEMPLATE_TEXT"  # its choice, our lines
    DETERMINISTIC = "DETERMINISTIC"                    # the top candidate, as templated


@dataclass
class HookTiming:
    beat_seconds: float
    settle_start: float
    total: float

    def beat_window(self, i: int) -> tuple:
        return (i * self.beat_seconds, (i + 1) * self.beat_seconds)


@dataclass
class HookPlan:
    mode: HookMode
    archetype: Archetype
    candidate_id: str
    eyebrow: str
    curiosity_line: str
    summary_line: str
    hero_visual: HeroVisual
    hero_payload: dict
    hero_strings: tuple
    teaser_beats: list           # beat ids, in play order
    fact_ids: list               # facts the hook cites
    agenda: list
    timing: HookTiming
    source: HookSource
    fallback_reason: str | None = None
    validation_issues: list = field(default_factory=list)
    gemini_response: str | None = None
    candidates: list = field(default_factory=list)   # [(id, archetype, score)] as offered
    priority_rule: dict | None = None    # POST major-index-day priority decision, when evaluated

    def public_strings(self, sheet: HookFactSheet) -> list:
        out = [self.eyebrow, self.curiosity_line, self.summary_line, *self.agenda,
               *self.hero_strings]
        for bid in self.teaser_beats:
            beat = sheet.beat(bid)
            if beat:
                out.extend(beat.strings)
        return [s for s in out if s]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value, "archetype": self.archetype.value,
            "candidate_id": self.candidate_id, "eyebrow": self.eyebrow,
            "curiosity_line": self.curiosity_line, "summary_line": self.summary_line,
            "hero_visual": self.hero_visual.value, "teaser_beats": list(self.teaser_beats),
            "fact_ids": list(self.fact_ids), "agenda": list(self.agenda),
            "timing": asdict(self.timing), "source": self.source.value,
            "fallback_reason": self.fallback_reason,
            "validation_issues": list(self.validation_issues),
            "gemini_response": self.gemini_response, "candidates": list(self.candidates),
            "priority_rule": self.priority_rule,
        }


__all__ = ["HookMode", "Archetype", "HeroVisual", "BeatKind", "HookFact", "TeaserBeatOption",
           "HookCandidate", "HookFactSheet", "HookSource", "HookTiming", "HookPlan",
           "ARCHETYPE_HEROES", "EYEBROW", "MODE_ARCHETYPES"]

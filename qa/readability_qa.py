"""Deterministic readability checks on the editorial plan, before anything is rendered.

Video QA asks "is this a valid MP4?". Readability QA asks the question that actually caused
this phase: "could someone read this at normal speed without pausing?" Both are arithmetic -
word counts, card counts, estimated reading time against planned duration. No model, no
computer vision, so the same plan always gets the same verdict.

Thresholds are deliberately split so the gate is trustworthy enough to block on:

* **BLOCK** - the scene genuinely cannot be read in the time it is on screen, or its text
  exceeds the hard budget, or too many major items compete at once, or the duration is
  nonsense. These are failures a viewer would definitely notice.
* **WARN** - the scene is tight but readable: slightly over a soft word budget, or reading
  time a little past the duration. Warnings never block, because a gate that fires on
  trivia is a gate someone switches off.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from editorial.config import (HARD_PRIMARY_WORDS, HARD_SECONDARY_WORDS, MAX_GLOBAL_CUES,
                              MAX_HEATMAP_CARDS, MAX_MAJOR_CARDS, MAX_PRIMARY_WORDS,
                              MAX_RANKED_MOVERS, MAX_SECONDARY_WORDS, MAX_SHORT_DURATION,
                              MIN_SHORT_DURATION, bounds_for)

# How far estimated reading time may exceed the scene's duration before it blocks. Below
# this it warns: the estimate is approximate, and a 10% overshoot is not a broken Short.
BLOCK_READ_RATIO = 1.25


@dataclass
class SceneReadability:
    scene_id: str
    scene_type: str
    duration: float
    visible_words: int
    primary_words: int
    secondary_words: int
    cards: int
    caption_changes: int
    estimated_read_seconds: float
    show_ticker: bool
    status: str = "PASS"
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"scene": self.scene_id, "scene_type": self.scene_type,
                "duration": round(self.duration, 2), "visible_words": self.visible_words,
                "primary_words": self.primary_words, "secondary_words": self.secondary_words,
                "cards": self.cards, "caption_changes": self.caption_changes,
                "estimated_read_seconds": round(self.estimated_read_seconds, 2),
                "show_ticker": self.show_ticker, "status": self.status,
                "reasons": list(self.reasons), "warnings": list(self.warnings)}


@dataclass
class ReadabilityReport:
    status: str = "PASS"
    total_duration: float = 0.0
    scenes: list = field(default_factory=list)
    blocking_issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checked_at: dt.datetime | None = None

    @property
    def passed(self) -> bool:
        return self.status != "FAIL"

    def to_dict(self) -> dict:
        return {"status": self.status, "passed": self.passed,
                "total_duration": round(self.total_duration, 2),
                "checked_at": self.checked_at.isoformat() if self.checked_at else None,
                "scenes": [s.to_dict() for s in self.scenes],
                "blocking_issues": list(self.blocking_issues),
                "warnings": list(self.warnings)}


def check_scene(scene) -> SceneReadability:
    low, high = bounds_for(scene.scene_type.value)
    result = SceneReadability(
        scene_id=scene.scene_id, scene_type=scene.scene_type.value,
        duration=scene.planned_duration, visible_words=scene.visible_words(),
        primary_words=scene.primary_words(), secondary_words=scene.secondary_words(),
        cards=scene.card_count(), caption_changes=scene.caption_changes(),
        estimated_read_seconds=scene.estimated_read_seconds, show_ticker=scene.show_ticker)

    if scene.planned_duration <= 0:
        result.reasons.append("scene has no duration")
    elif scene.planned_duration > high + 0.51:
        result.reasons.append(
            f"duration {scene.planned_duration:.2f}s exceeds the {scene.scene_type.value} "
            f"maximum of {high:.2f}s")

    if scene.planned_duration > 0:
        ratio = scene.estimated_read_seconds / scene.planned_duration
        if ratio > BLOCK_READ_RATIO:
            result.reasons.append(
                f"needs about {scene.estimated_read_seconds:.1f}s to read but is on screen "
                f"for {scene.planned_duration:.1f}s")
        elif ratio > 1.0:
            result.warnings.append(
                f"reading time {scene.estimated_read_seconds:.1f}s slightly exceeds its "
                f"{scene.planned_duration:.1f}s on screen")

    if result.primary_words > HARD_PRIMARY_WORDS:
        result.reasons.append(f"primary text is {result.primary_words} words "
                              f"(hard limit {HARD_PRIMARY_WORDS})")
    elif result.primary_words > MAX_PRIMARY_WORDS:
        result.warnings.append(f"primary text is {result.primary_words} words "
                               f"(budget {MAX_PRIMARY_WORDS})")

    if result.secondary_words > HARD_SECONDARY_WORDS:
        result.reasons.append(f"secondary text is {result.secondary_words} words "
                              f"(hard limit {HARD_SECONDARY_WORDS})")
    elif result.secondary_words > MAX_SECONDARY_WORDS:
        result.warnings.append(f"secondary text is {result.secondary_words} words "
                               f"(budget {MAX_SECONDARY_WORDS})")

    # A heatmap is scanned as a grid, not read card by card - "too many things compete for
    # attention at once" is a narrative-reading rule and does not apply to something that is
    # deliberately designed to show many cells in one glance. It gets its own, much higher,
    # ceiling instead of the narrative one.
    if scene.metadata.get("presentation") == "HEATMAP":
        if result.cards > MAX_HEATMAP_CARDS:
            result.reasons.append(f"{result.cards} heatmap cells exceed the maximum of "
                                  f"{MAX_HEATMAP_CARDS}")
    elif scene.metadata.get("presentation") == "SCAN":
        if result.cards > MAX_RANKED_MOVERS:
            result.reasons.append(f"{result.cards} ranked rows exceed the maximum of "
                                  f"{MAX_RANKED_MOVERS}")
    # GLOBAL's compact grid is the same reading mode, capped at MAX_GLOBAL_CUES instead of
    # the narrative ceiling - selection already caps it there, so this only guards against a
    # future regression rather than firing in normal operation.
    elif scene.metadata.get("presentation") == "GLOBAL_SCAN":
        if result.cards > MAX_GLOBAL_CUES:
            result.reasons.append(f"{result.cards} global cues exceed the maximum of "
                                  f"{MAX_GLOBAL_CUES}")
    elif result.cards > MAX_MAJOR_CARDS:
        result.reasons.append(f"{result.cards} major items compete at once "
                              f"(maximum {MAX_MAJOR_CARDS})")

    if result.caption_changes > 0:
        result.reasons.append(f"{result.caption_changes} caption changes within one scene")

    result.status = "FAIL" if result.reasons else ("WARN" if result.warnings else "PASS")
    return result


def check_plan(plan, now: dt.datetime | None = None) -> ReadabilityReport:
    """Readability verdict for a whole Short."""
    report = ReadabilityReport(checked_at=now or dt.datetime.now(dt.timezone.utc),
                               total_duration=plan.total_duration)
    for scene in plan.scenes:
        result = check_scene(scene)
        report.scenes.append(result)
        report.blocking_issues.extend(f"{result.scene_id}: {r}" for r in result.reasons)
        report.warnings.extend(f"{result.scene_id}: {w}" for w in result.warnings)

    total = plan.total_duration
    if total < MIN_SHORT_DURATION:
        report.blocking_issues.append(
            f"the Short is only {total:.1f}s, below the {MIN_SHORT_DURATION:.0f}s floor")
    elif total > MAX_SHORT_DURATION:
        report.blocking_issues.append(
            f"the Short is {total:.1f}s, above the {MAX_SHORT_DURATION:.0f}s ceiling")

    if not plan.scenes:
        report.blocking_issues.append("the plan contains no scenes")

    report.status = ("FAIL" if report.blocking_issues
                     else "WARN" if report.warnings else "PASS")
    return report


__all__ = ["check_plan", "check_scene", "ReadabilityReport", "SceneReadability",
           "BLOCK_READ_RATIO"]

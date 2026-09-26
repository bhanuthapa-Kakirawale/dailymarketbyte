"""Deterministic Radar presentation planner (Phase 4.2 Packet 6.1).

`build_radar_presentation` is the ONE planning implementation (packet spec section 5) - it
accepts either a live `radar.daily_pipeline.DailyRadarResult` object or the equivalent dict
(exactly `DailyRadarResult.to_dict()`'s shape, e.g. loaded from `daily_radar_<date>.json`),
normalizes to the dict form internally, and builds a `radar.presentation.RadarPresentation`
from it. `build_radar_presentation_from_file` is a thin file-loading wrapper around the same
function - there is no second, divergent code path for the on-disk artifact.

See `radar/presentation.py`'s module docstring for the hard boundary this module operates
inside (no acquisition, no ranking, no new facts, numeric integrity, content safety).
"""
from __future__ import annotations

import datetime as dt
import json
import os

import config
from core.content_safety import SafetyStatus, sanitize_field

from .presentation import (
    ARTIFACT_DIR, ASPECT_RATIO, CLOSING_DURATION_SECONDS, DIRECTION_LABELS, HOOK_DURATION_SECONDS,
    MAX_DISPLAY_EVIDENCE, MAX_DURATION_SECONDS, MIN_DURATION_SECONDS, NOVELTY_LABELS,
    PERSISTENCE_LABELS, RADAR_PRESENTATION_VERSION, STORY_DURATION_SECONDS,
    TARGET_DURATION_SECONDS, TECHNICAL_EVENT_LABELS, VOLUME_LEVEL_LABELS, PresentationEvidence,
    PresentationFormat, PresentationQA, PresentationStatus, RadarPresentation,
    RadarPresentationScene, RadarStoryPresentation, SceneRole, VisualType, _fmt_pct, _fmt_pp,
    _fmt_rvol, _word_count, presentation_id, _NOVELTY_STATE_CHANGE_TYPES, _WORD_LIMITS,
)

# Short, space-constrained headline words for technical events - a SECOND, shorter label set
# than `TECHNICAL_EVENT_LABELS` (which stays the full evidence-line wording), keyed by the SAME
# existing `TechnicalEventType` values - never a new event, only a compressed rendering of one
# that already exists (packet spec section 14's "MANKIND: Volume + Breakout" example).
_HEADLINE_EVENT_WORD = {
    "BREAK_ABOVE_20D_RANGE": "Breakout", "BREAK_ABOVE_50D_RANGE": "Breakout",
    "BREAK_BELOW_20D_RANGE": "Breakdown", "BREAK_BELOW_50D_RANGE": "Breakdown",
    "CROSS_ABOVE_SMA20": "Trend Shift", "CROSS_ABOVE_SMA50": "Trend Shift",
    "CROSS_BELOW_SMA20": "Trend Shift", "CROSS_BELOW_SMA50": "Trend Shift",
    "RANGE_COMPRESSION": "Compression",
}


# --------------------------------------------------------------------------- visual type
def _select_visual_type(active_families: list, independent_signal_count: int,
                        direction: str | None, quiet_signal: bool) -> VisualType:
    """Deterministic precedence (packet spec section 31) - first match wins:

    1. quiet signal (a quiet multi-family development is, product-wise, still fundamentally a
       "quiet" one - the whole point of surfacing it - even when it also happens to have 3
       families)
    2. three independent families
    3. mixed direction
    4. STRUCTURE + VOLUME
    5. STRUCTURE + RELATIVE_PERFORMANCE
    6. STRUCTURE alone
    7. fallback (VOLUME + RELATIVE_PERFORMANCE with no STRUCTURE, or anything else) -> MIXED_SIGNAL_CARD
    """
    fams = set(active_families)
    if quiet_signal and independent_signal_count >= 2:
        return VisualType.QUIET_SIGNAL_CARD
    if independent_signal_count >= 3:
        return VisualType.THREE_SIGNAL_CARD
    if direction == "MIXED":
        return VisualType.MIXED_SIGNAL_CARD
    if "STRUCTURE" in fams and "VOLUME" in fams:
        return VisualType.VOLUME_STRUCTURE_CARD
    if "STRUCTURE" in fams and "RELATIVE_PERFORMANCE" in fams:
        return VisualType.RELATIVE_STRUCTURE_CARD
    if "STRUCTURE" in fams:
        return VisualType.TECHNICAL_CHANGE_CARD
    return VisualType.MIXED_SIGNAL_CARD


# --------------------------------------------------------------------------- headline / facts
def _build_headline(instrument: str, active_families: list, events: list,
                    independent_signal_count: int, novelty_type: str | None) -> str:
    fams = set(active_families)
    event_word = next((_HEADLINE_EVENT_WORD[e] for e in events if e in _HEADLINE_EVENT_WORD), None)
    if independent_signal_count >= 3:
        core = "Multiple Signals"
    elif "STRUCTURE" in fams and "VOLUME" in fams:
        core = f"Volume + {event_word}" if event_word else "Volume + Structure Shift"
    elif "STRUCTURE" in fams and "RELATIVE_PERFORMANCE" in fams:
        core = f"{event_word} + Relative Shift" if event_word else "Structure + Relative Shift"
    elif "VOLUME" in fams and "RELATIVE_PERFORMANCE" in fams:
        core = "Volume + Relative Shift"
    elif "STRUCTURE" in fams:
        core = event_word or "Technical Structure Change"
    elif "VOLUME" in fams:
        core = "Unusual Volume"
    else:
        core = NOVELTY_LABELS.get(novelty_type) or "Radar Signal"
    return f"{instrument}: {core}"


def _build_primary_fact(events: list, volume_context: dict, relative_context: dict) -> str | None:
    parts = []
    if events:
        label = TECHNICAL_EVENT_LABELS.get(events[0])
        if label:
            parts.append(label[0].upper() + label[1:])
    level, rvol = volume_context.get("level"), volume_context.get("relative_volume")
    if level and rvol is not None:
        parts.append(f"RVOL {_fmt_rvol(rvol)}")
    if not parts:
        label = PERSISTENCE_LABELS.get(relative_context.get("persistence_state"))
        if label:
            parts.append(label)
    return "; ".join(parts[:2]) if parts else None


def _build_context_line(quiet_signal: bool, direction: str | None,
                        relative_context: dict) -> str | None:
    parts = []
    if quiet_signal:
        parts.append("Quiet Signal")
    direction_label = DIRECTION_LABELS.get(direction) if direction else None
    if direction_label:
        parts.append(direction_label)
    d5 = relative_context.get("market_relative_5d_pp")
    if d5 is not None:
        parts.append(f"5D vs Nifty {_fmt_pp(d5)}")
    return "; ".join(parts) if parts else None


def _select_evidence(novelty_type: str | None, independent_signal_count: int, events: list,
                     volume_context: dict, relative_context: dict,
                     price_change_pct: float | None, visual_type: str | None = None) -> list:
    """Display-priority order (packet spec section 28), capped at `MAX_DISPLAY_EVIDENCE`
    (section 29) - everything else stays available in `provenance`/upstream data, never
    deleted, simply not visually emphasized.

    Priority 1 (the evidence responsible for the novelty/state change) uses the mapped
    `NOVELTY_LABELS` value, never the raw `novelty_reason` sentence - `radar.novelty`'s own
    diagnostic text embeds internal enum spellings (e.g. "new technical event:
    BREAK_ABOVE_20D_RANGE"), which is exactly the internal-enum-syntax exposure packet spec
    section 15 forbids on a viewer-facing label, and is also far longer than the 5-word evidence
    limit. `novelty_reason` is never shown to the viewer by this module.

    Priority 2 ("Three independent signals") is SKIPPED when `visual_type` is already
    `THREE_SIGNAL_CARD` (Phase 4.2 Packet 6.2 finding) - the card's own badge ("THREE
    INDEPENDENT SIGNALS") already states this, so repeating it as the top evidence line wasted
    the most prominent evidence slot on a restatement of the badge instead of an actual fact.
    """
    candidates: list = []
    if novelty_type in _NOVELTY_STATE_CHANGE_TYPES:
        novelty_label = NOVELTY_LABELS.get(novelty_type)
        if novelty_label:
            candidates.append((1, novelty_label, "novelty_type"))
    if independent_signal_count >= 3 and visual_type != "THREE_SIGNAL_CARD":
        candidates.append((2, "Three independent signals", "independent_signal_count"))
    if events:
        label = TECHNICAL_EVENT_LABELS.get(events[0])
        if label:
            candidates.append((3, label[0].upper() + label[1:], "technical_context.events[0]"))
    level, rvol = volume_context.get("level"), volume_context.get("relative_volume")
    if level and rvol is not None:
        vol_label = VOLUME_LEVEL_LABELS.get(level, str(level).title())
        candidates.append((4, f"{vol_label}; RVOL {_fmt_rvol(rvol)}", "volume_context"))
    persistence_label = PERSISTENCE_LABELS.get(relative_context.get("persistence_state"))
    if persistence_label:
        candidates.append((5, persistence_label, "relative_context.persistence_state"))
    if price_change_pct is not None:
        candidates.append((6, f"Session move {_fmt_pct(price_change_pct)}", "price_change_pct"))

    candidates.sort(key=lambda c: c[0])
    tiers = ("PRIMARY", "SECONDARY", "SUPPORTING")
    return [PresentationEvidence(label=label, tier=tiers[min(i, len(tiers) - 1)], source_field=src)
           for i, (_, label, src) in enumerate(candidates[:MAX_DISPLAY_EVIDENCE])]


def _build_narration_facts(events: list, volume_context: dict, relative_context: dict,
                           price_change_pct: float | None) -> list:
    """Every sentence here is built from a field already validated on the input `RadarStory` -
    no inference, no new calculation (packet spec section 32)."""
    facts = []
    rvol = volume_context.get("relative_volume")
    if rvol is not None:
        facts.append(f"Relative volume was {rvol:.1f} times normal.")
    for e in events:
        label = TECHNICAL_EVENT_LABELS.get(e)
        if label:
            facts.append(f"Price showed a {label}.")
    d5 = relative_context.get("market_relative_5d_pp")
    if d5 is not None:
        facts.append(f"5-day relative performance versus Nifty was {_fmt_pp(d5)}.")
    d20 = relative_context.get("market_relative_20d_pp")
    if d20 is not None:
        facts.append(f"20-day relative performance versus Nifty was {_fmt_pp(d20)}.")
    if price_change_pct is not None:
        facts.append(f"Session price move was {_fmt_pct(price_change_pct)}.")
    return facts


def _build_hook_text(story_count: int) -> tuple[str, str]:
    noun = "development" if story_count == 1 else "developments"
    headline = f"Today's Market Radar found {story_count} {noun} worth investigating."
    subheadline = "Not just the biggest movers - these are today's Radar signals."
    return headline, subheadline


def _build_closing_text() -> tuple[str, str]:
    return ("These are market signals, not investment recommendations.",
           "Investigate the evidence before making decisions.")


# --------------------------------------------------------------------------- per-story build
def _build_story_presentation(story: dict, safety_log: list, as_of: dt.datetime) -> RadarStoryPresentation:
    def safe(label: str, text: str | None, fallback: str = "") -> str | None:
        if not text:
            return None
        checked, result = sanitize_field(text, fallback=fallback, now=as_of)
        safety_log.append((label, result))
        return checked or None

    instrument = story["instrument"]
    active_families = list(story.get("active_families") or [])
    novelty_type = story.get("novelty_type")
    direction = story.get("direction")
    price_change_pct = story.get("price_change_pct")
    volume_context = story.get("volume_context") or {}
    technical_context = story.get("technical_context") or {}
    relative_context = story.get("relative_context") or {}
    independent_signal_count = story.get("independent_signal_count", len(active_families))
    events = list(technical_context.get("events") or [])

    quiet_signal = (price_change_pct is not None and abs(price_change_pct) < 2.0
                   and independent_signal_count >= 2)
    visual_type = _select_visual_type(active_families, independent_signal_count, direction,
                                      quiet_signal)

    headline = safe(f"{instrument}.headline",
                    _build_headline(instrument, active_families, events,
                                    independent_signal_count, novelty_type))
    primary_fact = safe(f"{instrument}.primary_fact",
                        _build_primary_fact(events, volume_context, relative_context))
    context_line = safe(f"{instrument}.context_line",
                        _build_context_line(quiet_signal, direction, relative_context))

    raw_evidence = _select_evidence(novelty_type, independent_signal_count, events,
                                    volume_context, relative_context, price_change_pct,
                                    visual_type=visual_type.value)
    evidence_items = [
        PresentationEvidence(label=safe(f"{instrument}.evidence.{i}", e.label) or e.label,
                             tier=e.tier, source_field=e.source_field)
        for i, e in enumerate(raw_evidence)]

    narration_raw = _build_narration_facts(events, volume_context, relative_context,
                                           price_change_pct)
    narration_facts = [safe(f"{instrument}.narration.{i}", f) or f
                       for i, f in enumerate(narration_raw)]

    return RadarStoryPresentation(
        instrument=instrument, company_name=story.get("company_name"), scene_order=0,
        headline=headline or f"{instrument}: {NOVELTY_LABELS.get(novelty_type) or 'Radar Signal'}",
        primary_fact=primary_fact, context_line=context_line, evidence_items=evidence_items,
        active_families=active_families, novelty_label=NOVELTY_LABELS.get(novelty_type),
        direction_label=DIRECTION_LABELS.get(direction) if direction else None,
        price_change_display=_fmt_pct(price_change_pct), quiet_signal=quiet_signal,
        selection_reason=story.get("editorial_selection_reason"), visual_type=visual_type.value,
        duration_seconds=STORY_DURATION_SECONDS, source_selection_id=story.get("selection_id", ""),
        supporting_session_dates=list(story.get("supporting_session_dates") or []),
        provenance=dict(story.get("provenance") or {}), narration_facts=narration_facts)


# --------------------------------------------------------------------------- QA
def _run_qa(story_presentations: list, stories_data: list, estimated_duration: int,
           content_safety_ok: bool) -> PresentationQA:
    instruments = [sp.instrument for sp in story_presentations]
    no_dupes = len(instruments) == len(set(instruments))
    order_preserved = instruments == [s["instrument"] for s in stories_data]
    max_evidence_ok = all(len(sp.evidence_items) <= MAX_DISPLAY_EVIDENCE for sp in story_presentations)
    every_maps = all(sp.source_selection_id for sp in story_presentations)

    violations = []
    for sp in story_presentations:
        if sp.headline and _word_count(sp.headline) > _WORD_LIMITS["headline"]:
            violations.append(f"{sp.instrument}: headline exceeds {_WORD_LIMITS['headline']} words")
        if sp.primary_fact and _word_count(sp.primary_fact) > _WORD_LIMITS["primary_fact"]:
            violations.append(f"{sp.instrument}: primary_fact exceeds {_WORD_LIMITS['primary_fact']} words")
        if sp.context_line and _word_count(sp.context_line) > _WORD_LIMITS["context_line"]:
            violations.append(f"{sp.instrument}: context_line exceeds {_WORD_LIMITS['context_line']} words")
        for e in sp.evidence_items:
            if _word_count(e.label) > _WORD_LIMITS["evidence_label"]:
                violations.append(f"{sp.instrument}: evidence label '{e.label}' exceeds "
                                  f"{_WORD_LIMITS['evidence_label']} words")

    in_range = MIN_DURATION_SECONDS <= estimated_duration <= MAX_DURATION_SECONDS
    short_due_to_count = (not in_range) and len(story_presentations) < 3

    return PresentationQA(
        duration_in_target_range=in_range,
        duration_target_not_reached_due_to_story_count=short_due_to_count,
        story_order_preserved=order_preserved, no_duplicate_instruments=no_dupes,
        max_evidence_respected=max_evidence_ok, every_story_maps_to_selection=every_maps,
        content_safety_passed=content_safety_ok, text_density_violations=violations)


# --------------------------------------------------------------------------- entry points
def _empty_presentation(session_date: str, as_of: dt.datetime, data: dict, *,
                        status: PresentationStatus, warnings: list) -> RadarPresentation:
    content_safety_status = (SafetyStatus.BLOCKED.value
                             if status is PresentationStatus.CONTENT_SAFETY_BLOCKED
                             else SafetyStatus.SAFE.value)
    return RadarPresentation(
        presentation_id=presentation_id(session_date),
        presentation_version=RADAR_PRESENTATION_VERSION, session_date=session_date,
        generated_at=as_of.isoformat(), format=PresentationFormat.MARKET_RADAR_SHORT.value,
        aspect_ratio=ASPECT_RATIO, target_duration_seconds=TARGET_DURATION_SECONDS,
        estimated_duration_seconds=0, headline=None, subheadline=None, scenes=[], story_count=0,
        source_selector_version=data.get("selector_version"),
        source_pipeline_status=data.get("pipeline_status"), status=status.value,
        content_safety_status=content_safety_status, warnings=warnings)


def build_radar_presentation(daily_radar, *, as_of: dt.datetime | None = None) -> RadarPresentation:
    """The ONE presentation-planning implementation (packet spec section 5). `daily_radar` is
    either a `radar.daily_pipeline.DailyRadarResult` or an equivalent dict
    (`DailyRadarResult.to_dict()`'s shape - what `daily_radar_<date>.json` on disk contains).

    Never reruns a detector, fetches data, computes an indicator, or reorders/adds/removes a
    story the production editorial selector already chose (packet spec section 4) - every
    output field is read directly off `daily_radar`'s own `stories`/top-level fields.
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    data = daily_radar.to_dict() if hasattr(daily_radar, "to_dict") else dict(daily_radar)
    session_date = data["session_date"]
    pipeline_status = data.get("pipeline_status")

    if pipeline_status == "FAILED":
        return _empty_presentation(
            session_date, as_of, data, status=PresentationStatus.REJECTED_UPSTREAM_FAILED,
            warnings=["upstream DailyRadarResult.pipeline_status is FAILED; refusing to build "
                     "a presentation from it (packet spec section 41)"])

    stories_data = list(data.get("stories") or [])
    upstream_warnings = list(data.get("warnings") or [])  # DEGRADED state stays visible (section 41)

    if not stories_data:
        return _empty_presentation(
            session_date, as_of, data, status=PresentationStatus.NO_STORIES,
            warnings=upstream_warnings)

    safety_log: list = []

    def safe_top_level(label: str, text: str) -> str:
        checked, result = sanitize_field(text, fallback="", now=as_of)
        safety_log.append((label, result))
        return checked

    story_presentations = [_build_story_presentation(s, safety_log, as_of) for s in stories_data]

    hook_headline_raw, hook_sub_raw = _build_hook_text(len(story_presentations))
    hook_headline = safe_top_level("hook.headline", hook_headline_raw)
    hook_sub = safe_top_level("hook.subheadline", hook_sub_raw)

    closing_headline_raw, closing_sub_raw = _build_closing_text()
    closing_headline = safe_top_level("closing.headline", closing_headline_raw)
    closing_sub = safe_top_level("closing.subheadline", closing_sub_raw)

    blocked = [label for label, r in safety_log if r.status is SafetyStatus.BLOCKED]
    if blocked:
        return _empty_presentation(
            session_date, as_of, data, status=PresentationStatus.CONTENT_SAFETY_BLOCKED,
            warnings=upstream_warnings + [f"content safety BLOCKED field(s): {blocked}; "
                                         "refusing to publish this presentation"])
    sanitized = [label for label, r in safety_log if r.status is SafetyStatus.SANITIZED]
    content_safety_status = (SafetyStatus.SANITIZED.value if sanitized else SafetyStatus.SAFE.value)

    # No MARKET_CONTEXT scene: DailyRadarResult carries no validated general-market fact this
    # module could honestly display (packet spec section 9 - "do not invent general market
    # commentary merely to fill the scene"). This module never emits that role today.
    scenes: list = []
    order = 0
    scenes.append(RadarPresentationScene(
        scene_order=order, role=SceneRole.HOOK.value, duration_seconds=HOOK_DURATION_SECONDS,
        headline=hook_headline, subheadline=hook_sub))
    order += 1
    for sp in story_presentations:
        sp.scene_order = order
        scenes.append(RadarPresentationScene(
            scene_order=order, role=SceneRole.STORY.value, duration_seconds=sp.duration_seconds,
            headline=sp.headline, story=sp))
        order += 1
    scenes.append(RadarPresentationScene(
        scene_order=order, role=SceneRole.CLOSING.value, duration_seconds=CLOSING_DURATION_SECONDS,
        headline=closing_headline, subheadline=closing_sub))

    estimated_duration = sum(s.duration_seconds for s in scenes)
    qa = _run_qa(story_presentations, stories_data, estimated_duration,
                content_safety_ok=not blocked)

    warnings = list(upstream_warnings)
    if qa.duration_target_not_reached_due_to_story_count:
        warnings.append(
            f"duration_target_not_reached_due_to_story_count: estimated "
            f"{estimated_duration}s with {len(story_presentations)} stor(y/ies), target range "
            f"{MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS}s")

    return RadarPresentation(
        presentation_id=presentation_id(session_date), presentation_version=RADAR_PRESENTATION_VERSION,
        session_date=session_date, generated_at=as_of.isoformat(),
        format=PresentationFormat.MARKET_RADAR_SHORT.value, aspect_ratio=ASPECT_RATIO,
        target_duration_seconds=TARGET_DURATION_SECONDS,
        estimated_duration_seconds=estimated_duration, headline=hook_headline,
        subheadline=hook_sub, scenes=scenes, story_count=len(story_presentations),
        source_selector_version=data.get("selector_version"),
        source_pipeline_status=pipeline_status, content_safety_status=content_safety_status,
        status=PresentationStatus.OK.value, qa=qa.to_dict(), warnings=warnings)


def build_radar_presentation_from_file(path: str, *,
                                       as_of: dt.datetime | None = None) -> RadarPresentation:
    """Thin file-loading wrapper - loads `daily_radar_<date>.json` and calls the SAME
    `build_radar_presentation` a live in-process `DailyRadarResult` would go through (packet
    spec section 5: no second planning implementation)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    presentation = build_radar_presentation(data, as_of=as_of)
    presentation.source_radar_artifact = path
    return presentation


def save_presentation(presentation: RadarPresentation, out_dir: str | None = None) -> str:
    """One deterministic path per session (packet spec sections 39/40) - a rerun overwrites the
    SAME file with logically identical content, never `_v2`/`_final`."""
    directory = os.path.join(out_dir or config.OUT_DIR, "radar", "presentation")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"radar_presentation_{presentation.session_date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(presentation.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    return path


__all__ = ["build_radar_presentation", "build_radar_presentation_from_file", "save_presentation"]

"""Deterministic event-novelty classification (Phase 4.2 Packet 5.4A).

Answers, for every composite Radar candidate: "what changed about this candidate since its
previous valid Radar state?" - never "which stock is better". No candidate is ever deleted,
reordered, scored, or ranked here; `radar.composite`'s raw output is read and never mutated.
This is metadata layered on top of it (`radar.models.CandidateNovelty`), so a later editorial
layer can distinguish a stock that just became interesting from one that has quietly stayed
interesting for a week, without this module making that editorial call itself.

Two hard boundaries, mirrored from every other `radar/` module:

* **No acquisition.** The only input is an already-built, chronologically-ordered sequence of
  `(session_date, [StockRadarCandidate, ...])` pairs - typically `radar.composite.
  build_candidates` output across several sessions. Nothing here calls a provider, a website,
  a model, or `market_history.db`/`market_ohlcv.db`.
* **No mutation.** Every `StockRadarCandidate` is read and never written; `classify_history`
  builds fresh `CandidateNovelty` records, one per input candidate, in the same order.

Deterministic, not generated: given the same candidate sequence, `classify_history` produces
byte-identical output - no LLM, no randomness, no current-wall-clock dependence.

## Comparison baseline and lookback

`NoveltyThresholds.lookback_sessions` (default 5, `radar.thresholds`) is editorial-state
MEMORY, not a trading signal or a detector threshold. For a candidate at session index `i`
(the caller's sessions list, which MUST already be consecutive valid trading sessions on the
canonical spine - see `radar.session_alignment`), the comparison baseline is that symbol's most
recent PRIOR candidate appearance at index `j < i` with `i - j <= lookback_sessions`. Because
only sessions where the symbol was ACTUALLY a candidate are recorded, a session where it simply
wasn't a candidate consumes no lookback budget by itself - the gap example in the packet spec
(candidate Monday, not-candidate Tue/Wed, candidate again Thursday, all within 5 trading
sessions) compares Thursday directly against Monday. A symbol whose last candidate appearance
lies MORE than `lookback_sessions` positions back is treated exactly as if it had never
appeared: `NEW_CANDIDATE`, not a transition from stale state.

Because `i`/`j` are POSITIONS in the caller's already-consecutive-trading-session list rather
than calendar dates, a holiday or weekend between two sessions never shows up here at all (the
caller's session list skips it entirely) and can never consume lookback budget - matching
`radar.session_alignment`'s "trading sessions, not calendar days" rule.

## Per-type rules

* `NEW_CANDIDATE` - no prior candidate within the lookback. `previous_candidate_date`/
  `sessions_since_previous_candidate` are both `None`.
* `NEW_EVIDENCE_FAMILY` - `active_families` gained at least one member versus the baseline.
  Losing a family alone (`lost_families`) is recorded for diagnostics but never triggers
  novelty by itself (packet spec section 7).
* `NEW_TECHNICAL_EVENT` - the candidate's OWN structural evidence (`reason_codes` prefixed
  `STRUCTURE_` - the same meaningful events `radar.composite` already decided activate the
  STRUCTURE family, never `TechnicalStructure.events` unfiltered, which can include
  `RANGE_COMPRESSION` - a state, not a transition) gained a technical event type it did not
  carry at the baseline. Only NEWLY appearing types count; a persistent event is never
  re-reported as new every session it remains true.
* `PERSISTENCE_TRANSITION` - `relative_strength.persistence_state` differs from the baseline
  AND both values are available (never inferred across a missing reading).
* `DIRECTION_TRANSITION` - `direction_compatibility` differs from the baseline.
* `ATTENTION_ESCALATION` - baseline was `NOTABLE` and current is `HIGH_INTEREST`, strictly in
  that direction only; `HIGH_INTEREST` -> `NOTABLE` is never escalation (packet spec section 11
  - no numeric score is introduced to express this, just the explicit two-value ordering).
* `MULTIPLE_CHANGES` - two or more of the above five triggers fired simultaneously. Every
  individual trigger's reason still lands in `change_reason_codes`, never collapsed away.
* `CONTINUATION` - a prior candidate exists within the lookback and NONE of the five triggers
  fired - the candidate's state, by every field this module compares, is materially unchanged.

Every classification's `reason` is a deterministic sentence built only from the fields already
on `CandidateNovelty` - no template an LLM fills in, no recommendation language.
"""
from __future__ import annotations

import datetime as dt

from .models import AttentionLevel, CandidateNovelty, NoveltyType
from .thresholds import DEFAULT_NOVELTY_THRESHOLDS, NoveltyThresholds

_STRUCTURE_PREFIX = "STRUCTURE_"


def _technical_event_types(candidate) -> set:
    """The candidate's own MEANINGFUL structural evidence, read from its already-public
    `reason_codes` (never `TechnicalStructure.events` unfiltered) - exactly the events that
    activated its STRUCTURE family, so novelty tracks the same evidence a reader of the
    candidate already sees, not raw detector internals."""
    return {code[len(_STRUCTURE_PREFIX):] for code in candidate.reason_codes
           if code.startswith(_STRUCTURE_PREFIX)}


def _persistence_value(candidate) -> str | None:
    if candidate.relative_strength is None or candidate.relative_strength.persistence_state is None:
        return None
    return candidate.relative_strength.persistence_state.value


def _direction_value(candidate) -> str | None:
    return candidate.direction_compatibility.value if candidate.direction_compatibility else None


def _attention_value(candidate) -> str | None:
    return candidate.attention_level.value if candidate.attention_level else None


def _build_changes(prev_candidate, candidate) -> tuple[list, list, list, dict]:
    """Compare `candidate` against `prev_candidate`. Returns `(triggers, codes, clauses,
    fields)` - `fields` carries every previous/current value `CandidateNovelty` records,
    independent of which triggers fired."""
    families_prev, families_curr = set(prev_candidate.active_families), set(candidate.active_families)
    tech_prev, tech_curr = _technical_event_types(prev_candidate), _technical_event_types(candidate)
    persistence_prev, persistence_curr = _persistence_value(prev_candidate), _persistence_value(candidate)
    direction_prev, direction_curr = _direction_value(prev_candidate), _direction_value(candidate)
    attention_prev, attention_curr = _attention_value(prev_candidate), _attention_value(candidate)

    new_families = sorted(families_curr - families_prev)
    lost_families = sorted(families_prev - families_curr)
    new_tech = sorted(tech_curr - tech_prev)
    persistence_transition = (persistence_prev is not None and persistence_curr is not None
                              and persistence_prev != persistence_curr)
    direction_transition = (direction_prev is not None and direction_curr is not None
                            and direction_prev != direction_curr)
    attention_escalation = (attention_prev == AttentionLevel.NOTABLE.value
                            and attention_curr == AttentionLevel.HIGH_INTEREST.value)

    triggers, codes, clauses = [], [], []
    if new_families:
        triggers.append(NoveltyType.NEW_EVIDENCE_FAMILY)
        codes.extend(f"NEW_FAMILY_{f}" for f in new_families)
        clauses.append("new evidence family: " + ", ".join(new_families))
    if new_tech:
        triggers.append(NoveltyType.NEW_TECHNICAL_EVENT)
        codes.extend(f"NEW_TECHNICAL_EVENT_{ev}" for ev in new_tech)
        clauses.append("new technical event: " + ", ".join(new_tech))
    if persistence_transition:
        triggers.append(NoveltyType.PERSISTENCE_TRANSITION)
        codes.append(f"PERSISTENCE_TRANSITION_{persistence_prev}_TO_{persistence_curr}")
        clauses.append(f"persistence {persistence_prev} -> {persistence_curr}")
    if direction_transition:
        triggers.append(NoveltyType.DIRECTION_TRANSITION)
        codes.append(f"DIRECTION_TRANSITION_{direction_prev}_TO_{direction_curr}")
        clauses.append(f"direction {direction_prev} -> {direction_curr}")
    if attention_escalation:
        triggers.append(NoveltyType.ATTENTION_ESCALATION)
        codes.append("ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST")
        clauses.append("attention NOTABLE -> HIGH_INTEREST")

    fields = {
        "new_families": new_families, "lost_families": lost_families, "new_technical_events": new_tech,
        "previous_persistence": persistence_prev, "current_persistence": persistence_curr,
        "previous_direction": direction_prev, "current_direction": direction_curr,
        "previous_attention": attention_prev, "current_attention": attention_curr,
    }
    return triggers, codes, clauses, fields


def _classify_one(session_date: dt.date, prev_date: dt.date | None, sessions_since: int | None,
                  prev_candidate, candidate) -> CandidateNovelty:
    instrument = candidate.instrument
    if prev_candidate is None:
        return CandidateNovelty(
            instrument=instrument, session_date=session_date, novelty_type=NoveltyType.NEW_CANDIDATE,
            previous_candidate_date=None, sessions_since_previous_candidate=None,
            current_persistence=_persistence_value(candidate), current_direction=_direction_value(candidate),
            current_attention=_attention_value(candidate), change_reason_codes=["NEW_CANDIDATE"],
            reason=f"{instrument}: no prior Radar candidate within the lookback window.")

    triggers, codes, clauses, fields = _build_changes(prev_candidate, candidate)

    if not triggers:
        novelty_type = NoveltyType.CONTINUATION
        codes = ["CONTINUATION"]
        reason = (f"{instrument}: unchanged from its candidate state {sessions_since} session(s) "
                 f"ago ({prev_date}).")
    elif len(triggers) == 1:
        novelty_type = triggers[0]
        reason = f"{instrument}: {clauses[0]}."
    else:
        novelty_type = NoveltyType.MULTIPLE_CHANGES
        reason = f"{instrument}: " + "; ".join(clauses) + "."

    return CandidateNovelty(
        instrument=instrument, session_date=session_date, novelty_type=novelty_type,
        previous_candidate_date=prev_date, sessions_since_previous_candidate=sessions_since,
        change_reason_codes=codes, reason=reason, **fields)


def classify_history(sessions: list, *,
                     thresholds: NoveltyThresholds = DEFAULT_NOVELTY_THRESHOLDS) -> dict:
    """Classify novelty for every candidate across an ordered `sessions` sequence.

    `sessions`: `[(session_date, [StockRadarCandidate, ...]), ...]`, already sorted oldest-first
    and already consecutive valid trading sessions on the canonical spine (the caller's
    responsibility - this function trusts list-index adjacency as trading-session adjacency,
    exactly as `radar.historical_validation.select_session_dates` already guarantees for its own
    20-session sample). A gap in this list (a session simply omitted, e.g. because the whole
    session's data was unusable) is NOT the same as a holiday and WILL shift lookback math for
    that gap's width - callers must pass a truly consecutive spine slice.

    Returns `{session_date: [CandidateNovelty, ...]}`, one entry per session in the same
    candidate order as that session's input list. Nothing is mutated; nothing is dropped.
    """
    occurrences: dict = {}          # symbol -> [(session_index, StockRadarCandidate), ...]
    result: dict = {}
    for i, (session_date, candidates) in enumerate(sessions):
        session_results = []
        for candidate in candidates:
            history = occurrences.get(candidate.instrument, [])
            prev_index, prev_candidate, prev_date, sessions_since = None, None, None, None
            if history:
                cand_index, cand_obj = history[-1]
                if i - cand_index <= thresholds.lookback_sessions:
                    prev_index, prev_candidate = cand_index, cand_obj
                    prev_date = sessions[cand_index][0]
                    sessions_since = i - cand_index
            session_results.append(_classify_one(session_date, prev_date, sessions_since,
                                                  prev_candidate, candidate))
        result[session_date] = session_results
        for candidate in candidates:
            occurrences.setdefault(candidate.instrument, []).append((i, candidate))
    return result


__all__ = ["classify_history"]

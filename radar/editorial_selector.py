"""Production editorial selector (Phase 4.2 Packet 5.4C).

Freezes the tested `HYBRID_RESERVED_DIVERSITY` (Policy F) behavior from Packets 5.4B/5.4B.1/
5.4B.2 as the production selection layer:

    Detectors -> Composite -> Novelty -> EDITORIAL SELECTOR (this module) -> Editorial Selection
                                                                            -> Publication/video (later)

Deliberately NOT a call into `radar.editorial_policy_hybrid.select_policy_f` - that module
stays frozen as historical-validation infrastructure (packet spec section 28); this is an
independent implementation of the same rules against the REAL typed `StockRadarCandidate`/
`CandidateNovelty` objects the rest of the Radar pipeline actually produces, verified to match
Policy F's own 60-session validation output via
`tests/test_editorial_selector.py::test_matches_policy_f_over_60_session_dataset` (packet spec
section 29 - 100% logical equivalence, or this packet stops).

## Input boundary (packet spec section 3)

`select_session` takes only: a session date, a list of `(StockRadarCandidate, CandidateNovelty)`
pairs already produced elsewhere (this module fetches no market data, runs no detector,
computes no novelty), and `recently_selected` - a plain `{instrument: last_selected_date}` dict
the CALLER already loaded from persistence (`storage.editorial_repository.EditorialStore.
get_prior_selections`, one bounded query, never one per candidate). Neither the candidate nor
the novelty object is ever written to - every field this module reads off them is read-only.

## Frozen rules (packet spec section 2 - do not retune)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from storage.editorial_models import EditorialSelection, SelectionLifecycle

EDITORIAL_SELECTOR_VERSION = "1.0"

MAX_STORIES_PER_SESSION = 5
PUBLICATION_COOLDOWN_SESSIONS = 3
MAX_RESERVED_3FAMILY_SLOTS = 2

_CONTINUATION = "CONTINUATION"
_STATE_CHANGE_TYPES = frozenset({
    "MULTIPLE_CHANGES", "NEW_EVIDENCE_FAMILY", "NEW_TECHNICAL_EVENT",
    "PERSISTENCE_TRANSITION", "DIRECTION_TRANSITION", "ATTENTION_ESCALATION",
})

_BUCKET_LABELS = {0: "STATE_CHANGE_3_FAMILY", 1: "STATE_CHANGE_2_FAMILY",
                  2: "NEW_CANDIDATE_3_FAMILY", 3: "NEW_CANDIDATE_2_FAMILY"}
_RESERVED_BUCKET_STATE_CHANGE = "RESERVED_3FAMILY_STATE_CHANGE"
_RESERVED_BUCKET_NEW_CANDIDATE = "RESERVED_3FAMILY_NEW_CANDIDATE"

_STRUCTURE_PREFIX = "STRUCTURE_"


def make_selection_id(session_date: dt.date, instrument: str,
                      selector_version: str = EDITORIAL_SELECTOR_VERSION) -> str:
    """Deterministic selection identity (packet spec section 12) - `(session_date, instrument,
    selector_version)` alone, never a random UUID. Re-running the selector against identical
    inputs reproduces the exact same id, which is what makes `EditorialStore.save_selections`
    idempotent."""
    return f"{session_date.isoformat()}:{instrument}:{selector_version}"


# ------------------------------------------------------------------ read-only normalization
@dataclass(frozen=True)
class _Normalized:
    """A read-only view over one `(StockRadarCandidate, CandidateNovelty)` pair - the ONLY
    place this module reads those objects. Nothing downstream touches them again."""
    instrument: str
    novelty_type: str
    independent_signal_count: int
    active_families: tuple
    direction_compatibility: str | None
    attention_level: str | None
    technical_event_types: frozenset
    change_reason_codes: tuple
    reason_codes: tuple
    novelty_reason: str


def _normalize(candidate, novelty) -> _Normalized:
    technical_events = frozenset(
        code[len(_STRUCTURE_PREFIX):] for code in candidate.reason_codes
        if code.startswith(_STRUCTURE_PREFIX))
    return _Normalized(
        instrument=candidate.instrument, novelty_type=novelty.novelty_type.value,
        independent_signal_count=candidate.independent_signal_count,
        active_families=tuple(candidate.active_families),
        direction_compatibility=(candidate.direction_compatibility.value
                                 if candidate.direction_compatibility else None),
        attention_level=candidate.attention_level.value if candidate.attention_level else None,
        technical_event_types=technical_events,
        change_reason_codes=tuple(novelty.change_reason_codes),
        reason_codes=tuple(candidate.reason_codes), novelty_reason=novelty.reason)


def eligible_pool(candidate_novelty_pairs: list) -> list:
    """`novelty_type != CONTINUATION` (packet spec section 4) - the shared starting pool."""
    return [_normalize(c, n) for c, n in candidate_novelty_pairs if n.novelty_type.value != _CONTINUATION]


# ------------------------------------------------------------------ ordering
def _family_bucket(nc: _Normalized) -> int:
    return 0 if nc.independent_signal_count == 3 else 1


def _bucket_group(nc: _Normalized) -> int:
    is_state_change = nc.novelty_type in _STATE_CHANGE_TYPES
    fam3 = _family_bucket(nc) == 0
    if is_state_change and fam3:
        return 0
    if is_state_change and not fam3:
        return 1
    if fam3:
        return 2
    return 3


def _base_order(pool: list) -> list:
    return sorted(pool, key=lambda nc: (_bucket_group(nc), nc.instrument))


def _reserved_key(nc: _Normalized) -> tuple:
    is_state_change = nc.novelty_type in _STATE_CHANGE_TYPES
    return (0 if is_state_change else 1, nc.instrument)


def _reserved_bucket(nc: _Normalized) -> str:
    return _RESERVED_BUCKET_STATE_CHANGE if nc.novelty_type in _STATE_CHANGE_TYPES else _RESERVED_BUCKET_NEW_CANDIDATE


# ------------------------------------------------------------------ cooldown
def _override_triggers(nc: _Normalized) -> list:
    codes = nc.change_reason_codes
    triggers = []
    if any(c.startswith("ATTENTION_ESCALATION") for c in codes):
        triggers.append("ATTENTION_ESCALATION")
    if any(c.startswith("NEW_FAMILY_") for c in codes):
        triggers.append("NEW_EVIDENCE_FAMILY")
    if any(c.startswith("DIRECTION_TRANSITION_") for c in codes):
        triggers.append("DIRECTION_TRANSITION")
    return triggers


def _apply_cooldown(ordered: list, recently_selected: dict) -> tuple[list, list]:
    """`recently_selected` must already be restricted to the prior `PUBLICATION_COOLDOWN_
    SESSIONS` valid trading sessions STRICTLY BEFORE the current session (the caller's
    responsibility - `EditorialStore.get_prior_selections` already guarantees this with an
    exclusive upper bound, so a rerun for the SAME session never suppresses its own candidates -
    packet spec sections 7/18). Being 3-family is never itself an override (section 8/13)."""
    eligible, suppressed = [], []
    for nc in ordered:
        if nc.instrument not in recently_selected:
            eligible.append((nc, "NOT_RECENTLY_PUBLISHED", None))
            continue
        triggers = _override_triggers(nc)
        if triggers:
            eligible.append((nc, "COOLDOWN_OVERRIDE_" + "_AND_".join(triggers), triggers[0]))
        else:
            suppressed.append(nc)
    return eligible, suppressed


# ------------------------------------------------------------------ reservation
def _reserve_3family(eligible: list, max_slots: int = MAX_RESERVED_3FAMILY_SLOTS) -> tuple[list, list]:
    """Reservation happens strictly AFTER cooldown (packet spec section 9/15) - `eligible` here
    has already had cooldown-suppressed candidates removed."""
    eligible_3family = [e for e in eligible if e[0].independent_signal_count == 3]
    reserved_count = min(max_slots, len(eligible_3family))
    if reserved_count == 0:
        return [], list(eligible)
    ordered_3family = sorted(eligible_3family, key=lambda e: _reserved_key(e[0]))
    reserved = ordered_3family[:reserved_count]
    reserved_symbols = {e[0].instrument for e in reserved}
    remaining = [e for e in eligible if e[0].instrument not in reserved_symbols]
    return reserved, remaining


# ------------------------------------------------------------------ reason text
def _selection_reason(nc: _Normalized, *, reserved: bool, diversity_role: str,
                      cooldown_status: str, cooldown_trigger: str | None) -> str:
    if reserved:
        base = "Reserved because three independent evidence families were active."
    elif diversity_role == "POSITIVE_SLOT":
        base = "Selected to provide an available positive-direction development."
    elif diversity_role == "NEGATIVE_SLOT":
        base = "Selected to provide an available negative-direction development."
    elif nc.novelty_type in _STATE_CHANGE_TYPES:
        base = f"Selected as a {nc.novelty_type.replace('_', ' ').lower()} development."
    else:
        base = "Selected as a new Radar candidate."

    if cooldown_status == "NOT_RECENTLY_PUBLISHED":
        return base
    if cooldown_trigger:
        return base + f" Cooldown overridden because of {cooldown_trigger.replace('_', ' ').lower()}."
    return base


def _build_selection(session_date: dt.date, nc: _Normalized, *, bucket: str, reserved: bool,
                     diversity_role: str, cooldown_status: str, cooldown_trigger: str | None,
                     selector_version: str, as_of: dt.datetime) -> EditorialSelection:
    return EditorialSelection(
        selection_id=make_selection_id(session_date, nc.instrument, selector_version),
        session_date=session_date, instrument=nc.instrument, novelty_type=nc.novelty_type,
        active_families=nc.active_families,
        independent_signal_count=nc.independent_signal_count,
        direction_compatibility=nc.direction_compatibility, attention_level=nc.attention_level,
        selection_bucket=bucket, reserved_3family=reserved, diversity_role=diversity_role,
        cooldown_status=cooldown_status, cooldown_override_reason=cooldown_trigger,
        reason_codes=nc.reason_codes,
        selection_reason=_selection_reason(nc, reserved=reserved, diversity_role=diversity_role,
                                           cooldown_status=cooldown_status,
                                           cooldown_trigger=cooldown_trigger),
        selector_version=selector_version, lifecycle_state=SelectionLifecycle.SELECTED.value,
        selected_at=as_of)


# ------------------------------------------------------------------ result object
@dataclass
class EditorialSelectionResult:
    """Packet spec section 23 - what a caller (a future Radar/video integration) reads after
    one selection run. `selected` is the ordered, capped (<=5) list of `EditorialSelection`;
    everything else is deterministic diagnostics (section 24), never advisory text."""
    session_date: dt.date
    selector_version: str
    selected: list = field(default_factory=list)
    eligible_before_cooldown: int = 0
    suppressed_by_cooldown: int = 0
    reservation_count: int = 0
    direction_availability: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    degraded: bool = False

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date.isoformat(), "selector_version": self.selector_version,
            "selected": [s.to_dict() for s in self.selected],
            "eligible_before_cooldown": self.eligible_before_cooldown,
            "suppressed_by_cooldown": self.suppressed_by_cooldown,
            "reservation_count": self.reservation_count,
            "direction_availability": dict(self.direction_availability),
            "diagnostics": dict(self.diagnostics), "warnings": list(self.warnings),
            "degraded": self.degraded,
        }


# ------------------------------------------------------------------ pure selection
def select_session(session_date: dt.date, candidate_novelty_pairs: list, recently_selected: dict,
                   *, selector_version: str = EDITORIAL_SELECTOR_VERSION,
                   as_of: dt.datetime | None = None) -> EditorialSelectionResult:
    """Pure - no I/O, no mutation of any input object. `recently_selected` must already be
    restricted to the prior cooldown window and MUST NOT include the current `session_date`
    itself (see `_apply_cooldown`'s docstring) - `EditorialStore.get_prior_selections` already
    guarantees both.
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    input_count = len(candidate_novelty_pairs)
    pool = eligible_pool(candidate_novelty_pairs)
    continuations_removed = input_count - len(pool)

    ordered = _base_order(pool)
    eligible, suppressed = _apply_cooldown(ordered, recently_selected)
    override_counts = {"ATTENTION_ESCALATION": 0, "NEW_EVIDENCE_FAMILY": 0, "DIRECTION_TRANSITION": 0}
    for _nc, status, _trig in eligible:
        if status.startswith("COOLDOWN_OVERRIDE_"):
            for name in override_counts:
                if name in status:
                    override_counts[name] += 1

    reserved, remaining = _reserve_3family(eligible)

    selections: list = []
    for nc, status, trig in reserved:
        selections.append(_build_selection(
            session_date, nc, bucket=_reserved_bucket(nc), reserved=True,
            diversity_role="NOT_APPLICABLE", cooldown_status=status, cooldown_trigger=trig,
            selector_version=selector_version, as_of=as_of))

    remaining_capacity = MAX_STORIES_PER_SESSION - len(selections)
    picked = {s.instrument for s in selections}

    positive_available = any(e[0].direction_compatibility == "ALIGNED_POSITIVE" for e in remaining)
    negative_available = any(e[0].direction_compatibility == "ALIGNED_NEGATIVE" for e in remaining)
    mixed_available = any(e[0].direction_compatibility not in ("ALIGNED_POSITIVE", "ALIGNED_NEGATIVE")
                          for e in remaining)

    fill: list = []
    if remaining_capacity > 0:
        pos = next((e for e in remaining if e[0].direction_compatibility == "ALIGNED_POSITIVE"
                   and e[0].instrument not in picked), None)
        if pos is not None:
            fill.append((*pos, "POSITIVE_SLOT"))
            picked.add(pos[0].instrument)

    if len(fill) < remaining_capacity:
        neg = next((e for e in remaining if e[0].direction_compatibility == "ALIGNED_NEGATIVE"
                   and e[0].instrument not in picked), None)
        if neg is not None:
            fill.append((*neg, "NEGATIVE_SLOT"))
            picked.add(neg[0].instrument)

    for nc, status, trig in remaining:
        if len(fill) >= remaining_capacity:
            break
        if nc.instrument in picked:
            continue
        fill.append((nc, status, trig, "BASE_ORDER"))
        picked.add(nc.instrument)

    for entry in fill[:remaining_capacity]:
        nc, status, trig, role = entry if len(entry) == 4 else (*entry, "BASE_ORDER")
        selections.append(_build_selection(
            session_date, nc, bucket=_BUCKET_LABELS[_bucket_group(nc)], reserved=False,
            diversity_role=role, cooldown_status=status, cooldown_trigger=trig,
            selector_version=selector_version, as_of=as_of))

    return EditorialSelectionResult(
        session_date=session_date, selector_version=selector_version, selected=selections,
        eligible_before_cooldown=len(pool), suppressed_by_cooldown=len(suppressed),
        reservation_count=len(reserved),
        direction_availability={"positive_available": positive_available,
                                "negative_available": negative_available,
                                "mixed_available": mixed_available},
        diagnostics={
            "input_candidates": input_count, "continuations_removed": continuations_removed,
            "cooldown_suppressed": len(suppressed), "cooldown_overrides": override_counts,
            "eligible_after_cooldown": len(eligible),
            "three_family_available": sum(1 for e in eligible if e[0].independent_signal_count == 3),
            "three_family_reserved": len(reserved),
            "positive_available": positive_available, "negative_available": negative_available,
            "mixed_available": mixed_available, "selected_count": len(selections),
        })


# ------------------------------------------------------------------ persistence-integrated entrypoint
def run_and_persist(session_date: dt.date, candidate_novelty_pairs: list, store, spine: list, *,
                    selector_version: str = EDITORIAL_SELECTOR_VERSION,
                    lookback_sessions: int = PUBLICATION_COOLDOWN_SESSIONS,
                    as_of: dt.datetime | None = None) -> EditorialSelectionResult:
    """The production integration boundary (packet spec section 25): loads prior publication
    history from `store` (one bounded query via `EditorialStore.get_prior_selections`), runs the
    pure `select_session`, persists the result (idempotent), and returns it.

    Persistence failure (packet spec section 26): if the history lookup itself fails - a locked
    file, a bad path, a corrupt database - this NEVER falls back to "assume no history" (that
    could re-select a symbol still inside its real cooldown, i.e. silent repetition, which this
    packet treats as worse than publishing nothing). It returns a `degraded=True` result with
    `selected=[]` and a warning instead, and persists nothing - a caller must treat a degraded
    result as "no safe selection available this run", not "zero eligible candidates".
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    try:
        recently_selected = store.get_prior_selections(session_date, spine, lookback_sessions)
    except Exception as exc:
        return EditorialSelectionResult(
            session_date=session_date, selector_version=selector_version, selected=[],
            warnings=[f"editorial publication history unavailable ({exc}); refusing to select "
                     "to avoid risking repeat publication - this is NOT the same as zero "
                     "eligible candidates"],
            degraded=True)

    result = select_session(session_date, candidate_novelty_pairs, recently_selected,
                            selector_version=selector_version, as_of=as_of)
    try:
        store.save_selections(result.selected)
    except Exception as exc:
        result.warnings.append(f"editorial selection persistence failed ({exc}); selection was "
                               "computed but NOT saved - cooldown state for this session will "
                               "not be visible to future runs")
        result.degraded = True
    return result


__all__ = ["EDITORIAL_SELECTOR_VERSION", "MAX_STORIES_PER_SESSION",
          "PUBLICATION_COOLDOWN_SESSIONS", "MAX_RESERVED_3FAMILY_SLOTS", "make_selection_id",
          "eligible_pool", "select_session", "EditorialSelectionResult", "run_and_persist"]

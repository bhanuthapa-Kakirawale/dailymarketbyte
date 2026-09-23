"""One editorial selection - a Radar novel development the production selector chose to
publish for a session (Phase 4.2 Packet 5.4C).

Deliberately NOT a copy of `radar.models.StockRadarCandidate`/`radar.models.CandidateNovelty`:
those describe what the Radar DETECTED; `EditorialSelection` describes the downstream editorial
DECISION about it (which bucket it was picked from, whether it used a reserved 3-family slot or
a diversity slot, what cooldown state let it through, and its lifecycle after selection). The
selector (`radar.editorial_selector`) builds these by reading the candidate/novelty objects and
never mutates them - see that module's own docstring for the full boundary.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class SelectionLifecycle(str, Enum):
    """A selection's downstream fate is NOT the same fact as having been selected (packet spec
    section 15) - the Radar can select 5 stories and video generation can still fail before any
    of them reach a viewer. `SELECTED` is written once by the selector itself;
    `RENDERED`/`PUBLISHED`/`FAILED` are later, separate updates to the SAME row via
    `storage.editorial_repository.EditorialStore.update_publication_state` - never a new row,
    since the underlying selection (why this symbol, on this session) never changes."""
    SELECTED = "SELECTED"
    RENDERED = "RENDERED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value)


def _parse_date(value) -> dt.date | None:
    if value is None or isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return dt.date.fromisoformat(value)


def _parse_dt(value) -> dt.datetime | None:
    if value is None or isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(value)


@dataclass(frozen=True)
class EditorialSelection:
    """One symbol's editorial-selection record for one session, under one selector version.

    `selection_id` is deterministic - built from `(session_date, instrument, selector_version)`
    alone (see `radar.editorial_selector.make_selection_id`), never a random UUID, so re-running
    the selector against identical inputs reproduces the exact same logical identity and a
    persistence write is naturally idempotent (packet spec sections 12/17).

    Frozen: a selection's OWN facts (why it was picked, from which bucket, under what cooldown
    state) never change after creation - only `lifecycle_state`/`updated_at` change, and those
    live as a separate in-place UPDATE on the stored row, not a mutation of this object.
    """
    selection_id: str
    session_date: dt.date
    instrument: str
    novelty_type: str
    active_families: tuple
    independent_signal_count: int
    direction_compatibility: str | None
    attention_level: str | None
    selection_bucket: str
    reserved_3family: bool
    diversity_role: str
    cooldown_status: str
    cooldown_override_reason: str | None
    reason_codes: tuple
    selection_reason: str
    selector_version: str
    lifecycle_state: str = SelectionLifecycle.SELECTED.value
    selected_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "selection_id": self.selection_id, "session_date": _iso(self.session_date),
            "instrument": self.instrument, "novelty_type": self.novelty_type,
            "active_families": list(self.active_families),
            "independent_signal_count": self.independent_signal_count,
            "direction_compatibility": self.direction_compatibility,
            "attention_level": self.attention_level, "selection_bucket": self.selection_bucket,
            "reserved_3family": self.reserved_3family, "diversity_role": self.diversity_role,
            "cooldown_status": self.cooldown_status,
            "cooldown_override_reason": self.cooldown_override_reason,
            "reason_codes": list(self.reason_codes), "selection_reason": self.selection_reason,
            "selector_version": self.selector_version, "lifecycle_state": self.lifecycle_state,
            "selected_at": _iso(self.selected_at), "updated_at": _iso(self.updated_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> EditorialSelection:
        return cls(
            selection_id=d["selection_id"], session_date=_parse_date(d["session_date"]),
            instrument=d["instrument"], novelty_type=d["novelty_type"],
            active_families=tuple(d.get("active_families") or []),
            independent_signal_count=d.get("independent_signal_count", 0),
            direction_compatibility=d.get("direction_compatibility"),
            attention_level=d.get("attention_level"),
            selection_bucket=d.get("selection_bucket", ""),
            reserved_3family=bool(d.get("reserved_3family")),
            diversity_role=d.get("diversity_role", "NOT_APPLICABLE"),
            cooldown_status=d.get("cooldown_status", ""),
            cooldown_override_reason=d.get("cooldown_override_reason"),
            reason_codes=tuple(d.get("reason_codes") or []),
            selection_reason=d.get("selection_reason", ""),
            selector_version=d.get("selector_version", ""),
            lifecycle_state=d.get("lifecycle_state", SelectionLifecycle.SELECTED.value),
            selected_at=_parse_dt(d.get("selected_at")), updated_at=_parse_dt(d.get("updated_at")),
            metadata=dict(d.get("metadata") or {}))


__all__ = ["EditorialSelection", "SelectionLifecycle"]

"""Candidate-history bootstrap and gap recovery (Phase 4.2 Packet 5.4E).

`radar.novelty.classify_history` needs `NoveltyThresholds.lookback_sessions` (5) prior valid
trading sessions of PERSISTED candidate state to classify today's candidates correctly. A fresh
deployment (empty `radar_candidate_history.db`) or one that missed several scheduled runs has no
way to get that history except by actually reconstructing it - this module does that
reconstruction, using the EXACT SAME production calculation path a live daily run uses
(`radar.daily_pipeline._run_detectors`), never a simplified stand-in detector.

Two hard boundaries (packet spec sections 5/20/21):

* **Candidate history only.** Never runs editorial selection, never writes
  `editorial_selections.db`, never writes a `daily_radar_<session_date>.json` artifact.
  Reconstructing what the Radar would have detected on a past session is not the same claim as
  "we published it" - this module makes only the first claim.
* **Chronological, always.** Oldest session first - see `backfill_candidate_history`'s own
  docstring for why (packet spec section 7).

## Session-level processing marker

A session that genuinely produced zero composite candidates must be recorded as PROCESSED, not
confused with "never attempted" - `storage.CandidateHistoryStore.mark_run`/`get_run_status`/
`get_complete_sessions` (the new `candidate_history_runs` table, packet spec section 11) is what
makes this distinction possible; candidate-row existence alone cannot (a zero-candidate session
inserts no rows). A session's COMPLETE marker is version-aware - keyed on
`(session_date, calculation_version)` - so a session already processed under an older
`calculation_version` is never silently treated as equivalent to one processed under the current
one (packet spec section 15); this packet does not implement recalculation/migration of
old-version history beyond making that distinction visible.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

import config
from storage.candidate_history_models import RunStatus
from storage.candidate_history_repository import CandidateHistoryStore
from storage.candidate_history_repository import default_db_path as candidate_history_db_path

from . import daily_pipeline as dp
from .models import CALCULATION_VERSION
from .thresholds import DEFAULT_NOVELTY_THRESHOLDS

# Candidate-history bootstrap depth is novelty's OWN lookback requirement, never a separately
# chosen number (packet spec section 8) - conflating this with OHLCV lookback (which
# `radar.ohlcv_service` already owns independently, ~55 sessions for range-compression) would
# backfill far more than novelty actually needs.
DEFAULT_BOOTSTRAP_DEPTH = DEFAULT_NOVELTY_THRESHOLDS.lookback_sessions

# A normal scheduled daily run must never silently replay months of history because of an
# unnoticed outage (packet spec section 17) - beyond this many missing prior sessions,
# `radar.daily_pipeline.run_daily_radar`'s auto-bootstrap preflight fails safely instead of
# backfilling. The explicit utility below has no such ceiling - an operator invoking it directly
# is presumed to have decided the size of the replay intentionally.
MAX_AUTO_BACKFILL_SESSIONS = 10


def find_missing_candidate_sessions(spine: list, target_session: dt.date, complete_sessions,
                                    required_sessions: int = DEFAULT_BOOTSTRAP_DEPTH) -> list:
    """Pure, offline, no I/O, no network (packet spec section 10).

    Every prior valid trading session on `spine` within `required_sessions` POSITIONS of
    `target_session` (never calendar-day arithmetic - weekends/holidays are simply never on
    `spine` to begin with, so they can never appear as "missing") that is NOT already in
    `complete_sessions` - a set of dates already marked `COMPLETE` under whichever
    `calculation_version` the caller cares about, from `storage.CandidateHistoryStore.
    get_complete_sessions`. Returned oldest-first. `target_session` itself is never included -
    this answers "what PRIOR history is missing", not "is today itself processed".

    `target_session` absent from `spine` returns `[]` - this function does not guess at a
    session it cannot place on the canonical calendar.
    """
    if target_session not in spine:
        return []
    idx = spine.index(target_session)
    prior_window = spine[max(0, idx - required_sessions):idx]
    complete = set(complete_sessions)
    return [d for d in prior_window if d not in complete]


@dataclass
class CandidateHistoryBackfillResult:
    """Structured diagnostics for one `backfill_candidate_history` call (packet spec section 19) -
    never prose-only."""
    requested_sessions: list = field(default_factory=list)
    processed_sessions: list = field(default_factory=list)
    already_complete_sessions: list = field(default_factory=list)
    failed_sessions: list = field(default_factory=list)
    candidate_rows_inserted: int = 0
    network_calls: int = 0
    cache_hits: int = 0
    warnings: list = field(default_factory=list)
    stage_timings: dict = field(default_factory=dict)
    calculation_version: str = CALCULATION_VERSION

    def to_dict(self) -> dict:
        return {
            "requested_sessions": [d.isoformat() for d in self.requested_sessions],
            "processed_sessions": [d.isoformat() for d in self.processed_sessions],
            "already_complete_sessions": [d.isoformat() for d in self.already_complete_sessions],
            "failed_sessions": [d.isoformat() for d in self.failed_sessions],
            "candidate_rows_inserted": self.candidate_rows_inserted,
            "network_calls": self.network_calls, "cache_hits": self.cache_hits,
            "warnings": list(self.warnings), "stage_timings": dict(self.stage_timings),
            "calculation_version": self.calculation_version,
        }


def _resolve_sessions(spine: list, sessions, target_session, start_session, end_session,
                      required_sessions: int, store: CandidateHistoryStore,
                      calculation_version: str) -> list:
    if sessions is not None:
        return sorted(sessions)
    if start_session is not None or end_session is not None:
        lo = start_session if start_session is not None else spine[0]
        hi = end_session if end_session is not None else spine[-1]
        return [d for d in spine if lo <= d <= hi]
    if target_session is not None:
        complete = store.get_complete_sessions(calculation_version, before=target_session)
        return find_missing_candidate_sessions(spine, target_session, complete, required_sessions)
    return []


def backfill_candidate_history(*, spine: list, universe: dict, benchmark_series: list,
                               sessions: list | None = None,
                               target_session: dt.date | None = None,
                               start_session: dt.date | None = None,
                               end_session: dt.date | None = None,
                               required_sessions: int = DEFAULT_BOOTSTRAP_DEPTH,
                               out_dir: str | None = None,
                               store: CandidateHistoryStore | None = None,
                               calculation_version: str = CALCULATION_VERSION,
                               as_of: dt.datetime | None = None) -> CandidateHistoryBackfillResult:
    """Reconstruct `radar_candidate_history.db` state for historical sessions.

    Uses the SAME production calculation path a live daily run uses
    (`radar.daily_pipeline._run_detectors` - packet spec section 6, "do not create a simplified
    backfill detector") for every session it processes, and ALWAYS chronologically, oldest
    session first (packet spec section 7) - never newest-to-oldest.

    Exactly one way to choose which sessions to process, in this priority order:
    1. `sessions`: an explicit, caller-supplied list (e.g. from `find_missing_candidate_sessions`) -
       used as given, sorted defensively.
    2. `start_session`/`end_session`: every spine session in that inclusive range (the "operator
       explicitly requested a range" case - allowed to exceed `MAX_AUTO_BACKFILL_SESSIONS`,
       unlike the automatic preflight in `radar.daily_pipeline.run_daily_radar`).
    3. `target_session` (+ `required_sessions`, default `DEFAULT_BOOTSTRAP_DEPTH`): compute the
       missing prior-session gap via `find_missing_candidate_sessions` internally - the
       cold-start/gap-recovery case.

    A session already marked `COMPLETE` under `calculation_version` is SKIPPED
    (`already_complete_sessions`), never reprocessed (packet spec section 14) - this function has
    no "force" option in this packet. Persistence order per session is candidates FIRST, then the
    `COMPLETE` marker (packet spec section 13) - a crash between the two leaves the session with
    NO marker at all (indistinguishable from "never attempted"), never a false `COMPLETE`; on
    retry, `save_candidates`'s own idempotent upsert means re-persisting is always safe.

    Never touches `editorial_selections.db` and never writes a `daily_radar_<date>.json`
    artifact (packet spec sections 20/21) - this reconstructs candidate history only.
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    out_dir = out_dir if out_dir is not None else config.OUT_DIR
    owns_store = False
    if store is None:
        store = CandidateHistoryStore(candidate_history_db_path(out_dir))
        owns_store = True

    try:
        target_sessions = _resolve_sessions(spine, sessions, target_session, start_session,
                                            end_session, required_sessions, store,
                                            calculation_version)
        result = CandidateHistoryBackfillResult(requested_sessions=list(target_sessions),
                                                calculation_version=calculation_version)

        for session_date in target_sessions:
            if store.is_session_complete(session_date, calculation_version):
                result.already_complete_sessions.append(session_date)
                continue

            if session_date not in spine or spine.index(session_date) == 0:
                result.failed_sessions.append(session_date)
                result.warnings.append(
                    f"{session_date}: not on the canonical spine, or no prior spine session "
                    "exists to determine prev_date - cannot backfill")
                store.mark_run(session_date, calculation_version, RunStatus.FAILED, 0, as_of)
                continue
            prev_date = spine[spine.index(session_date) - 1]

            try:
                detector_output = dp._run_detectors(
                    session_date, prev_date, universe, spine, benchmark_series,
                    out_dir=out_dir, as_of=as_of)
            except Exception as exc:
                result.failed_sessions.append(session_date)
                result.warnings.append(f"{session_date}: detector calculation failed ({exc})")
                store.mark_run(session_date, calculation_version, RunStatus.FAILED, 0, as_of)
                continue

            result.stage_timings[session_date.isoformat()] = detector_output.timings
            calls = detector_output.network_calls.get("universe_yahoo", 0)
            result.network_calls += calls
            if calls == 0:
                result.cache_hits += 1
            result.warnings.extend(f"{session_date}: {w}" for w in detector_output.warnings)

            candidates = detector_output.composite_snapshot.candidates
            try:
                # Candidates persisted BEFORE the COMPLETE marker - see docstring above.
                inserted = store.save_candidates(
                    session_date, [dp._to_stored_state(c) for c in candidates])
                store.mark_run(session_date, calculation_version, RunStatus.COMPLETE,
                               len(candidates), as_of)
            except Exception as exc:
                result.failed_sessions.append(session_date)
                result.warnings.append(f"{session_date}: candidate persistence failed ({exc})")
                try:
                    store.mark_run(session_date, calculation_version, RunStatus.FAILED,
                                   len(candidates), as_of)
                except Exception:
                    pass
                continue

            result.candidate_rows_inserted += inserted
            result.processed_sessions.append(session_date)

        return result
    finally:
        if owns_store:
            store.close()


__all__ = ["find_missing_candidate_sessions", "backfill_candidate_history",
          "CandidateHistoryBackfillResult", "DEFAULT_BOOTSTRAP_DEPTH",
          "MAX_AUTO_BACKFILL_SESSIONS"]

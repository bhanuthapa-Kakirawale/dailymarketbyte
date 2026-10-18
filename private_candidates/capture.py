"""Automatic, idempotent capture of the Trade Candidate Intelligence snapshot for the latest
final session - the integration point `intelligence_refresh` calls on both its normal evening
(PM) and catch-up (AM) invocations (docs/PRIVATE_TRADE_CANDIDATES.md "Point-in-time capture
contract", owner hardening pass).

Never raises past this module's own boundary: every function here returns a status dict, so a
candidate-capture failure can never propagate into (and corrupt) Radar/Regime/other
intelligence. Opening a Private Desk page never calls anything here - this module is reached
only from `intelligence_refresh`'s own orchestration (or a direct CLI/test call), never from a
page request.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

SUCCESS = "SUCCESS"
FAILED = "FAILED"
MISSING_INPUTS = "MISSING_INPUTS"
NOT_APPLICABLE = "NOT_APPLICABLE"


def _radar_run_marker_dates(repo, session: dt.date) -> list:
    """Every COMPLETE `candidate_history_runs.processed_at` recorded for `session`, parsed."""
    out = []
    for m in repo.radar_run_markers():
        if m.get("status") != "COMPLETE":
            continue
        try:
            sd = dt.date.fromisoformat(str(m["session_date"])[:10])
        except (TypeError, ValueError):
            continue
        if sd != session:
            continue
        processed_at = m.get("processed_at")
        if not processed_at:
            continue
        try:
            out.append(dt.datetime.fromisoformat(processed_at))
        except ValueError:
            continue
    return out


def resolve_capture_as_of(repo, session: dt.date, now: dt.datetime) -> dict:
    """`{"candidate_as_of", "capture_mode", "basis"}` for an AUTOMATIC capture of `session` at
    wall-clock `now`.

    Same-day (`now`'s own IST calendar date equals `session`): a genuinely live capture -
    `candidate_as_of = now`, `capture_mode = POINT_IN_TIME`.

    Later (an AM catch-up of a PM miss, or any later retry): POINT_IN_TIME is permitted ONLY
    when provenance proves it - Radar's own `candidate_history_runs` COMPLETE marker for
    `session` was itself `processed_at` on `session`'s own calendar day (never on a later day).
    When that holds, `candidate_as_of` is `session`'s end-of-day (23:59:59 IST): every
    subsystem read bounded by this cutoff can see nothing from a later calendar day BY
    CONSTRUCTION (the cutoff IS the proof, not a separate check) - this is the one place
    end-of-day is a legitimate boundary, because it is being used to materialize a packet that
    genuinely WOULD have been produced that evening, never to silently widen an explicit replay
    cutoff (see `service.py`'s module docstring for the defect this replaces).

    When Radar's own capture for `session` itself happened on a LATER day (or no COMPLETE
    marker is recorded at all for it), POINT_IN_TIME cannot be honestly claimed -
    `capture_mode = RECONSTRUCTED` (or `candidate_as_of = None` if nothing can be computed at
    all, signalled by `capture_mode = None`)."""
    from config import IST
    if now.astimezone(IST).date() == session:
        return {"candidate_as_of": now, "capture_mode": "POINT_IN_TIME",
               "basis": "live same-day capture"}

    end_of_day = dt.datetime.combine(session, dt.time(23, 59, 59), tzinfo=IST)
    markers = _radar_run_marker_dates(repo, session)
    if not markers:
        return {"candidate_as_of": None, "capture_mode": None,
               "basis": "no COMPLETE Radar run recorded for this session yet"}
    if max(m.astimezone(IST).date() for m in markers) <= session:
        return {"candidate_as_of": end_of_day, "capture_mode": "POINT_IN_TIME",
               "basis": "catch-up: Radar's own COMPLETE run for this session was processed on "
                       "the session's own calendar day - point-in-time provenance holds"}
    return {"candidate_as_of": end_of_day, "capture_mode": "RECONSTRUCTED",
           "basis": "catch-up: Radar's own COMPLETE run for this session was processed on a "
                   "LATER calendar day - point-in-time cannot be honestly claimed"}


_PROVENANCE_TIMESTAMPS = ("captured_at", "candidate_as_of")


def _content_equal(a, b) -> bool:
    """Compares every field EXCEPT the two provenance timestamps, which legitimately differ
    between two computations of otherwise-identical content (the whole point of idempotent
    capture: a repeated normal refresh must not generate a changing historical record just
    because the wall clock moved). Keeping the stored revision when nothing else changed also
    keeps the EARLIEST cutoff at which that content was knowable - the stronger PIT claim."""
    da, db = dataclasses.asdict(a), dataclasses.asdict(b)
    for key in _PROVENANCE_TIMESTAMPS:
        da.pop(key, None)
        db.pop(key, None)
    return da == db


def capture_latest_session(out_dir: str, session: dt.date, now: dt.datetime, *,
                           repo=None, policy=None) -> dict:
    """Builds (or reuses) the immutable Trade Candidate Intelligence snapshot for `session`,
    idempotently: a candidate whose latest stored revision already matches the freshly computed
    content is left untouched (`reused`); only a genuinely new/changed candidate gets a new
    revision (`written`). Never raises - every failure mode returns a status dict."""
    from . import service, store
    from .policy import DEFAULT_SCORING_POLICY

    from private_desk.db import SourceUnavailable

    policy = policy or DEFAULT_SCORING_POLICY
    if repo is None:
        from private_desk.repository import DeskRepository
        repo = DeskRepository(out_dir)

    try:
        stored = repo.candidates(session)
    except SourceUnavailable as exc:
        return {"status": NOT_APPLICABLE, "session": session.isoformat(),
               "reason": f"candidate history store unavailable: {exc}"}
    if not stored:
        return {"status": NOT_APPLICABLE, "session": session.isoformat(),
               "reason": "no Radar candidates recorded for this session"}

    resolved = resolve_capture_as_of(repo, session, now)
    if resolved["capture_mode"] is None:
        return {"status": MISSING_INPUTS, "session": session.isoformat(),
               "reason": resolved["basis"]}

    try:
        out = service.run(out_dir, session, repo=repo, policy=policy,
                          candidate_as_of=resolved["candidate_as_of"],
                          capture_mode=resolved["capture_mode"], captured_at=now)
    except Exception as exc:
        return {"status": FAILED, "session": session.isoformat(),
               "reason": f"{type(exc).__name__}: {exc}"}

    written, reused = [], []
    for a in out["assessments"]:
        existing = store.load_latest(out_dir, a.score_policy_version, session, a.symbol)
        if existing is not None and _content_equal(existing, a):
            reused.append(a.symbol)
            continue
        store.write_snapshot(out_dir, a)
        written.append(a.symbol)

    return {"status": SUCCESS, "session": session.isoformat(),
           "candidate_as_of": resolved["candidate_as_of"].isoformat(),
           "capture_mode": resolved["capture_mode"], "basis": resolved["basis"],
           "score_policy_version": out["score_policy_version"],
           "candidate_count": out["candidate_count"], "eligible_count": out["eligible_count"],
           "written": written, "reused": reused}


__all__ = ["resolve_capture_as_of", "capture_latest_session", "SUCCESS", "FAILED",
          "MISSING_INPUTS", "NOT_APPLICABLE"]

"""`private_candidates.capture`: the automatic, idempotent, point-in-time-honest capture
integration point `intelligence_refresh` calls (owner hardening pass, sections 1-2, 7-11)."""
import datetime as dt

import pytest

from conftest import SESSION
from private_candidates import capture, store
from test_private_desk import desk_out  # noqa: F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class _FakeRepo:
    """Minimal stand-in exposing only what `resolve_capture_as_of` reads."""
    def __init__(self, markers):
        self._markers = markers

    def radar_run_markers(self):
        return self._markers


def _marker(session_date, status, processed_at):
    return {"session_date": session_date.isoformat(), "status": status,
           "processed_at": processed_at}


def test_same_day_is_always_point_in_time_with_now_as_cutoff():
    repo = _FakeRepo([])
    now = dt.datetime(2026, 9, 18, 20, 0, tzinfo=IST)
    out = capture.resolve_capture_as_of(repo, SESSION, now)
    assert out["capture_mode"] == "POINT_IN_TIME"
    assert out["candidate_as_of"] == now


def test_catch_up_with_same_day_radar_marker_is_point_in_time_at_end_of_day():
    repo = _FakeRepo([_marker(SESSION, "COMPLETE", "2026-09-18T22:05:00+05:30")])
    now = dt.datetime(2026, 9, 19, 7, 0, tzinfo=IST)     # next morning
    out = capture.resolve_capture_as_of(repo, SESSION, now)
    assert out["capture_mode"] == "POINT_IN_TIME"
    assert out["candidate_as_of"] == dt.datetime.combine(SESSION, dt.time(23, 59, 59), tzinfo=IST)


def test_catch_up_with_late_radar_marker_is_reconstructed():
    """Radar's own COMPLETE run for the session was itself processed the NEXT day - point-in-
    time cannot be honestly claimed even though the candidate engine runs now."""
    repo = _FakeRepo([_marker(SESSION, "COMPLETE", "2026-09-19T06:50:00+05:30")])
    now = dt.datetime(2026, 9, 19, 7, 0, tzinfo=IST)
    out = capture.resolve_capture_as_of(repo, SESSION, now)
    assert out["capture_mode"] == "RECONSTRUCTED"


def test_catch_up_with_no_complete_marker_cannot_capture_at_all():
    repo = _FakeRepo([_marker(SESSION, "FAILED", "2026-09-18T22:05:00+05:30")])
    now = dt.datetime(2026, 9, 19, 7, 0, tzinfo=IST)
    out = capture.resolve_capture_as_of(repo, SESSION, now)
    assert out["capture_mode"] is None
    assert out["candidate_as_of"] is None


def test_no_stored_candidates_is_not_applicable(tmp_path):
    out_dir = str(tmp_path / "empty_out")
    now = dt.datetime(2026, 9, 18, 20, 0, tzinfo=IST)
    result = capture.capture_latest_session(out_dir, SESSION, now)
    assert result["status"] == capture.NOT_APPLICABLE


def test_live_capture_writes_one_snapshot_per_candidate_then_is_idempotent(desk_out):
    now = dt.datetime(2026, 9, 18, 20, 0, tzinfo=IST)
    first = capture.capture_latest_session(desk_out, SESSION, now)
    assert first["status"] == capture.SUCCESS
    assert first["capture_mode"] == "POINT_IN_TIME"
    assert first["written"]
    assert not first["reused"]

    second = capture.capture_latest_session(desk_out, SESSION, now)
    assert second["status"] == capture.SUCCESS
    assert not second["written"]
    assert set(second["reused"]) == set(first["written"])

    # idempotent: no new revision was created for any candidate
    for symbol in first["written"]:
        revisions_dir_count = len(store._existing_revisions(
            desk_out, first["score_policy_version"], SESSION.isoformat(), symbol))
        assert revisions_dir_count == 1


def test_a_genuinely_changed_candidate_writes_a_new_revision_not_a_silent_rewrite(desk_out):
    now = dt.datetime(2026, 9, 18, 20, 0, tzinfo=IST)
    first = capture.capture_latest_session(desk_out, SESSION, now)
    assert first["status"] == capture.SUCCESS
    symbol = first["written"][0]
    before = store.load_latest(desk_out, first["score_policy_version"], SESSION, symbol)

    # simulate a genuinely different capture under a NEW policy version - must coexist, not
    # overwrite v1.
    import dataclasses

    from private_candidates.policy import ScoringPolicy
    policy_v2 = dataclasses.replace(
        ScoringPolicy(), score_policy_version="trade-candidate-score-9.9-test")
    second = capture.capture_latest_session(desk_out, SESSION, now, policy=policy_v2)
    assert second["status"] == capture.SUCCESS
    assert second["score_policy_version"] == "trade-candidate-score-9.9-test"

    still_v1 = store.load_latest(desk_out, first["score_policy_version"], SESSION, symbol)
    assert still_v1 == before   # v1's snapshot is untouched by the v2 capture


def test_a_later_recompute_with_identical_content_is_reused_keeping_the_earliest_cutoff(desk_out):
    """A different wall clock alone (e.g. a second refresh the same evening) must not rewrite an
    unchanged record: `candidate_as_of` is provenance, like `captured_at`."""
    first_now = dt.datetime(2026, 9, 18, 22, 10, tzinfo=IST)
    later_now = dt.datetime(2026, 9, 18, 23, 0, tzinfo=IST)
    first = capture.capture_latest_session(desk_out, SESSION, first_now)
    second = capture.capture_latest_session(desk_out, SESSION, later_now)
    assert first["written"] and not second["written"]
    assert set(second["reused"]) == set(first["written"])
    for symbol in first["written"]:
        kept = store.load_latest(desk_out, first["score_policy_version"], SESSION, symbol)
        assert kept.candidate_as_of == first_now.isoformat()

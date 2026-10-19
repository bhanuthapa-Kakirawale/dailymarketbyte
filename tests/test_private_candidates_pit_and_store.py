"""Point-in-time safety (owner hardening pass) + the immutable snapshot store.

`candidate_as_of` is the authoritative cutoff: when a caller supplies it explicitly, every
subsystem read must honor EXACTLY that instant - never silently widened to end-of-day, and
never satisfied by "now" at test-run time. These tests always pass `candidate_as_of` explicitly
so they stay deterministic regardless of when they are actually run (SESSION, 2026-09-18, is in
the past relative to the real clock, so relying on a default "now" cutoff would make these
tests meaningless point-in-time checks, not a demonstration of honest PIT behaviour).
"""
import dataclasses
import datetime as dt
import json
import os

import pytest

from conftest import SESSION
from private_candidates import service, store
from test_private_desk import desk_out  # noqa: F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CANDIDATE_AS_OF = dt.datetime(2026, 9, 18, 22, 10, tzinfo=IST)   # "T3" throughout this module


def _assessment_by_symbol(out_dir, *, candidate_as_of=CANDIDATE_AS_OF):
    from private_desk.repository import DeskRepository
    repo = DeskRepository(out_dir)
    assessments = service.build_assessments(repo, SESSION, candidate_as_of=candidate_as_of)
    return {a.symbol: a for a in assessments}


def test_an_explicit_candidate_as_of_is_never_silently_widened_to_end_of_day(desk_out):
    """The defect this hardening pass fixes: an explicit, honest cutoff (T3 = 22:10) must
    never let in a revision retrieved at 23:30 just because both are "the same calendar day,
    before 23:59" (owner spec section 4, mandatory test)."""
    before = _assessment_by_symbol(desk_out)

    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision
    future_retrieved = MarketEvent(
        schema_version=SCHEMA_VERSION, family=EARNINGS, event_key="EARNINGS:SYMA:late-filing",
        symbol="SYMA", company="Synthetic A Ltd.", status="SCHEDULED", sub_type=None,
        data_as_of=(SESSION + dt.timedelta(days=1)).isoformat(),
        source_name="nse_corp_announcements", source_reference="https://example.invalid/x",
        facts=[], retrieved_at="2026-09-18T23:30:00+05:30",     # T4: same day, after T3, before 23:59
        first_retrieved_at="2026-09-18T23:30:00+05:30", status_capture=SUCCESS)
    write_revision(desk_out, future_retrieved)

    after = _assessment_by_symbol(desk_out)
    assert before["SYMA"].event_proximity == after["SYMA"].event_proximity == "NO_KNOWN_EVENT"
    assert after["SYMA"].sessions_to_event is None


def test_a_revision_retrieved_before_candidate_as_of_is_consumed(desk_out):
    """The other half of the same acceptance test: a revision retrieved BEFORE T3 (even late
    the same evening) legitimately IS visible - this is the real bug the end-of-day hack was
    trying (incorrectly) to fix."""
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision
    on_time = MarketEvent(
        schema_version=SCHEMA_VERSION, family=EARNINGS, event_key="EARNINGS:SYMA:on-time-filing",
        symbol="SYMA", company="Synthetic A Ltd.", status="SCHEDULED", sub_type=None,
        data_as_of=(SESSION + dt.timedelta(days=1)).isoformat(),
        source_name="nse_corp_announcements", source_reference="https://example.invalid/x",
        facts=[], retrieved_at="2026-09-18T22:05:00+05:30",     # before T3 (22:10)
        first_retrieved_at="2026-09-18T22:05:00+05:30", status_capture=SUCCESS)
    write_revision(desk_out, on_time)

    after = _assessment_by_symbol(desk_out)
    assert after["SYMA"].event_proximity == "EVENT_WITHIN_WINDOW"
    assert after["SYMA"].sessions_to_event is not None


def _write_market_structure_revision(out_dir, session, retrieved_at, syma_sector):
    """Hand-crafted artifact, written directly as an immutable `.revN.json` - mirrors
    `tests/test_market_structure.py`'s own convention of constructing revision files directly
    for point-in-time tests, without needing the full aggregation pipeline."""
    folder = os.path.join(out_dir, "market_structure")
    os.makedirs(folder, exist_ok=True)
    existing = [f for f in os.listdir(folder)
               if f.startswith(f"market_structure_{session.isoformat()}.rev")]
    n = len(existing) + 1
    path = os.path.join(folder, f"market_structure_{session.isoformat()}.rev{n}.json")
    constituents = {
        "SYMA": {"company": "Synthetic A Ltd.", "industry": "Healthcare", "sector": syma_sector},
        "SYMB": {"company": "Synthetic B Ltd.", "industry": "Financial Services",
                "sector": "Financials"},
    }
    artifact = {"snapshot": {"universe_source": {"retrieved_at": retrieved_at}},
               "universe": {"constituents": constituents}, "observations": []}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh)
    return path


def test_candidate_sees_the_supporting_artifact_retrieved_before_cutoff_never_the_one_after(
        desk_out):
    """The acceptance test for the cutoff architecture itself (owner spec section 16):
    T1 (Radar, already in `desk_out`) < T2 (supporting Market Structure revision) <
    T3 (candidate_as_of) < T4 (a later revision). The candidate must see T2's content and
    never T4's - proven by a sector value that differs only between the two revisions."""
    t2 = "2026-09-18T21:50:00+05:30"    # before T3
    t4 = "2026-09-18T23:30:00+05:30"    # after T3, same calendar day
    _write_market_structure_revision(desk_out, SESSION, t2, syma_sector="Healthcare")
    _write_market_structure_revision(desk_out, SESSION, t4, syma_sector="Diversified")

    assessments = _assessment_by_symbol(desk_out, candidate_as_of=CANDIDATE_AS_OF)   # T3 = 22:10
    assert assessments["SYMA"].sector == "Healthcare"       # T2's content
    assert assessments["SYMA"].sector != "Diversified"      # never T4's


def test_a_utc_cutoff_sees_exactly_what_the_same_instant_in_ist_sees(desk_out):
    """The stores compare cutoff STRINGS lexically against IST-offset `retrieved_at` values. A
    UTC cutoff for 22:10 IST ("16:40+00:00") used to sort before a 21:50 IST revision and hide
    it - the Desk's live view defaulted to a UTC clock, so that evening's sectors vanished."""
    _write_market_structure_revision(desk_out, SESSION, "2026-09-18T21:50:00+05:30",
                                     syma_sector="UTC-Probe")
    utc_cutoff = CANDIDATE_AS_OF.astimezone(dt.timezone.utc)
    assert _assessment_by_symbol(desk_out, candidate_as_of=utc_cutoff)["SYMA"].sector == "UTC-Probe"
    assert _assessment_by_symbol(desk_out)["SYMA"].sector == "UTC-Probe"


def test_without_an_override_capture_mode_is_point_in_time_only_for_a_genuinely_same_day_run(
        desk_out):
    """Default resolution (no explicit `candidate_as_of`/`capture_mode`): honest only when the
    caller's own clock falls on `session`'s own calendar day."""
    from private_desk.repository import DeskRepository
    repo = DeskRepository(desk_out)
    same_day = dt.datetime(2026, 9, 18, 20, 0, tzinfo=IST)
    later_day = dt.datetime(2026, 9, 19, 7, 0, tzinfo=IST)

    live = service.build_assessments(repo, SESSION, captured_at=same_day)
    assert live and all(a.capture_mode == "POINT_IN_TIME" for a in live)
    assert all(a.candidate_as_of == same_day.isoformat() for a in live)

    stale = service.build_assessments(repo, SESSION, captured_at=later_day)
    assert stale and all(a.capture_mode == "RECONSTRUCTED" for a in stale)


def _make_assessment(out_dir, symbol="SYMA"):
    return _assessment_by_symbol(out_dir)[symbol]


def test_write_snapshot_is_exclusive_create_never_overwrites(desk_out, tmp_path):
    out_dir = str(tmp_path / "store_out")
    a = _make_assessment(desk_out)
    a = dataclasses.replace(a, session_date=SESSION.isoformat())
    path1 = store.write_snapshot(out_dir, a)
    path2 = store.write_snapshot(out_dir, a)
    assert path1 != path2
    assert path1.endswith(".json") and not path1.endswith(".r2.json")
    assert path2.endswith(".r2.json")
    loaded = store.load_latest(out_dir, a.score_policy_version, SESSION, "SYMA")
    assert loaded == a


def test_snapshot_store_isolates_policy_versions(desk_out, tmp_path):
    out_dir = str(tmp_path / "store_out2")
    a_v1 = dataclasses.replace(_make_assessment(desk_out),
                               score_policy_version="trade-candidate-score-1.0")
    a_v2 = dataclasses.replace(a_v1, score_policy_version="trade-candidate-score-2.0")
    store.write_snapshot(out_dir, a_v1)
    store.write_snapshot(out_dir, a_v2)
    assert store.load_latest(out_dir, "trade-candidate-score-1.0", SESSION, "SYMA") == a_v1
    assert store.load_latest(out_dir, "trade-candidate-score-2.0", SESSION, "SYMA") == a_v2
    # rewriting v1 after v2 exists never touches v2's file
    store.write_snapshot(out_dir, a_v1)
    assert store.load_latest(out_dir, "trade-candidate-score-2.0", SESSION, "SYMA") == a_v2

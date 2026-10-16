"""End-to-end: run the engine against the SAME synthetic output tree `test_private_desk.py`
uses (real store classes, real detector chain), proving the wiring - never hand-written
detector expectations.
"""
import datetime as dt

import pytest

from conftest import SESSION
from private_candidates import service
from private_candidates.models import ResearchPriority
from test_private_desk import desk_out  # noqa: F401  (reused fixture, house convention)


@pytest.fixture
def repo(desk_out):
    from private_desk.repository import DeskRepository
    return DeskRepository(desk_out)


def test_build_assessments_covers_every_stored_candidate(repo):
    stored = repo.candidates(SESSION)
    assert stored, "fixture must produce at least one Radar candidate for SESSION"
    assessments = service.build_assessments(repo, SESSION)
    assert {a.symbol for a in assessments} == {c.instrument for c in stored}
    for a in assessments:
        assert a.research_priority in {p.value for p in ResearchPriority}
        assert a.score_validation_status == "PROVISIONAL"
        assert a.score_max == 100.0
        assert 0.0 <= a.score_total <= 100.0 + 1e-9
        assert a.components and len(a.components) == 10


def test_run_produces_raw_ranking_and_shortlist_without_crashing(desk_out):
    out = service.run(desk_out, SESSION)
    assert out["session_date"] == SESSION.isoformat()
    ranked_symbols = {a.symbol for a in out["raw_ranking"]}
    shortlisted_symbols = {a.symbol for a in out["shortlist"]}
    assert shortlisted_symbols <= ranked_symbols
    assert out["candidate_count"] == len(out["assessments"])


def test_no_candidates_for_a_session_with_no_radar_history_returns_empty(repo):
    assessments = service.build_assessments(repo, dt.date(2020, 1, 2))
    assert assessments == []

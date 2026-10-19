"""End-to-end: run the engine against the SAME synthetic output tree `test_private_desk.py`
uses (real store classes, real detector chain), proving the wiring - never hand-written
detector expectations.
"""
import dataclasses
import datetime as dt

import pytest

from conftest import SESSION
from private_candidates import capture, service, store
from private_candidates.models import (DAY_MOVE_AGAINST_RADAR_DIRECTION, MIXED_DIRECTION_EVIDENCE,
                                       SCHEMA_VERSION, ResearchPriority)
from test_private_desk import client, desk_out, svc  # noqa: F401  (reused fixtures, house convention)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
AS_OF = dt.datetime(2026, 9, 18, 22, 10, tzinfo=IST)


def _with_directions(repo, monkeypatch, directions: dict) -> list:
    """Re-label the stored Radar candidates' `direction_compatibility` (reconcile compares
    reason codes only, so nothing else about the candidates changes)."""
    original = repo.candidates
    patched = [dataclasses.replace(c, direction_compatibility=directions.get(
        c.instrument, c.direction_compatibility)) for c in original(SESSION)]
    monkeypatch.setattr(repo, "candidates",
                        lambda session: patched if session == SESSION else original(session))
    return patched


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


def test_every_assessment_carries_the_radar_direction(repo):
    recorded = {c.instrument: c.direction_compatibility for c in repo.candidates(SESSION)}
    for a in service.build_assessments(repo, SESSION, candidate_as_of=AS_OF):
        assert a.radar_direction == recorded[a.symbol]
        assert a.schema_version == SCHEMA_VERSION


UPSIDE_REFERENCES = {"PRIOR_20_LOW", "BROKEN_PRIOR_20_HIGH", "BROKEN_PRIOR_50_HIGH"}
DOWNSIDE_REFERENCES = {"PRIOR_20_HIGH", "BROKEN_PRIOR_20_LOW", "BROKEN_PRIOR_50_LOW"}


def test_the_radar_direction_drives_the_components_and_a_contrary_day_is_flagged(
        repo, monkeypatch):
    """Policy 2.0: a pullback day in an upside setup is still an upside setup (CGPOWER on
    2026-10-16 was ALIGNED_NEGATIVE on a +3.4% day and used to be scored as an upside one)."""
    target = next(c for c in repo.candidates(SESSION) if c.price_change_pct)
    with_day = "ALIGNED_POSITIVE" if target.price_change_pct > 0 else "ALIGNED_NEGATIVE"
    against = "ALIGNED_NEGATIVE" if with_day == "ALIGNED_POSITIVE" else "ALIGNED_POSITIVE"

    def assess(direction):
        _with_directions(repo, monkeypatch, {target.instrument: direction})
        return {a.symbol: a for a in service.build_assessments(
            repo, SESSION, candidate_as_of=AS_OF)}[target.instrument]

    agreeing, contrary = assess(with_day), assess(against)
    assert DAY_MOVE_AGAINST_RADAR_DIRECTION not in agreeing.flags
    assert DAY_MOVE_AGAINST_RADAR_DIRECTION in contrary.flags
    expected = {"ALIGNED_POSITIVE": UPSIDE_REFERENCES, "ALIGNED_NEGATIVE": DOWNSIDE_REFERENCES}
    assert agreeing.structural_reference_kind in expected[with_day]
    assert contrary.structural_reference_kind in expected[against]


def test_mixed_direction_evidence_is_never_eligible(repo, monkeypatch):
    stored = repo.candidates(SESSION)
    _with_directions(repo, monkeypatch, {c.instrument: "MIXED" for c in stored})
    for a in service.build_assessments(repo, SESSION, candidate_as_of=AS_OF):
        assert a.research_priority != ResearchPriority.ELIGIBLE.value
        if a.research_priority != ResearchPriority.DATA_INCOMPLETE.value:
            assert MIXED_DIRECTION_EVIDENCE in a.flags


def _rows(closes, start=dt.date(2026, 8, 3)):
    from core.trading_calendar import SessionCalendar
    cal, days, d = SessionCalendar(), [], start
    while len(days) < len(closes):
        if cal.status(d) == "SESSION":
            days.append(d)
        d += dt.timedelta(days=1)
    return [{"date": day, "close": c} for day, c in zip(days, closes)]


def test_the_sector_series_excludes_the_stock_itself():
    """A big mover must not drag its own sector benchmark: with one flat peer the sector
    return is 0, however far the stock itself ran."""
    from private_candidates import context
    stock = _rows([100 * 1.01 ** i for i in range(25)])
    peer = _rows([50.0] * 25)
    bench = _rows([1000.0] * 25)
    session = stock[-1]["date"]
    rel = context.sector_relative_performance("RUNNER", "IT", {"RUNNER": stock, "PEER": peer},
                                              stock, session, bench)
    assert rel["sector_return_20d"] == pytest.approx(0.0)
    assert rel["sector_relative_20d_pp"] == pytest.approx(rel["stock_return_20d"])
    assert context.sector_relative_performance("RUNNER", "IT", {"RUNNER": stock}, stock,
                                               session, bench) is None   # no peers, no sector


def test_upside_view_ranks_only_upside_setups_and_keeps_every_assessment(repo, monkeypatch):
    stored = repo.candidates(SESSION)
    assert len(stored) >= 2
    upside = {stored[0].instrument}
    _with_directions(repo, monkeypatch, {c.instrument: "ALIGNED_POSITIVE" if c.instrument in upside
                                         else "ALIGNED_NEGATIVE" for c in stored})

    every = service.run(repo.out_dir, SESSION, repo=repo, candidate_as_of=AS_OF)
    view = service.run(repo.out_dir, SESSION, repo=repo, candidate_as_of=AS_OF,
                       view=service.UPSIDE_VIEW)
    assert {a.symbol for a in view["raw_ranking"]} <= upside
    assert {a.symbol for a in view["shortlist"]} <= upside
    assert not {a.symbol for a in view["outside_view"]} & upside
    assert ({a.symbol for a in view["raw_ranking"] + view["outside_view"]}
            == {a.symbol for a in every["raw_ranking"]})
    assert len(view["assessments"]) == len(every["assessments"]) == len(stored)
    assert view["direction_counts"] == {"UPSIDE": 1, "DOWNSIDE": len(stored) - 1, "MIXED": 0}
    assert every["outside_view"] == []


def test_run_from_store_reads_the_capture_instead_of_recomputing(desk_out):
    assert service.run_from_store(desk_out, SESSION) is None          # nothing captured yet
    capture.capture_latest_session(desk_out, SESSION, AS_OF)

    stored = service.run_from_store(desk_out, SESSION)
    live = service.run(desk_out, SESSION, candidate_as_of=AS_OF, captured_at=AS_OF)
    assert stored["source"] == service.STORED_SNAPSHOT
    assert live["source"] == service.LIVE_RECOMPUTE
    by_symbol = lambda run: {a.symbol: a for a in run["assessments"]}   # noqa: E731
    assert by_symbol(stored) == by_symbol(live)
    assert [a.symbol for a in stored["raw_ranking"]] == [a.symbol for a in live["raw_ranking"]]
    assert stored["captured_first"] == stored["captured_last"] == AS_OF.isoformat()

    one = stored["assessments"][0]
    assert service.stored_assessment(desk_out, SESSION, one.symbol.lower()) == one


def test_a_snapshot_from_an_older_schema_falls_back_to_a_live_recompute(desk_out):
    capture.capture_latest_session(desk_out, SESSION, AS_OF)
    one = service.run_from_store(desk_out, SESSION)["assessments"][0]
    store.write_snapshot(desk_out, dataclasses.replace(
        one, schema_version="private-trade-candidate-1.0", radar_direction=None))
    assert service.run_from_store(desk_out, SESSION) is None
    assert service.stored_assessment(desk_out, SESSION, one.symbol) is None


def test_desk_defaults_to_the_upside_view_and_names_its_source(client, desk_out):
    html = client.get("/trade-candidates").text
    assert "upside setups only" in html
    assert "live re-evaluation" in html                                # nothing captured yet

    capture.capture_latest_session(desk_out, SESSION, AS_OF)
    assert "stored evening snapshot" in client.get("/trade-candidates").text
    assert "live re-evaluation" in client.get("/trade-candidates?live=1").text
    assert "upside setups only" not in client.get("/trade-candidates?view=ALL").text
    symbol = service.run_from_store(desk_out, SESSION)["assessments"][0].symbol
    assert "Stored snapshot recorded" in client.get(f"/trade-candidates/{symbol}").text
    assert "Live re-evaluation" in client.get(f"/trade-candidates/{symbol}?live=1").text

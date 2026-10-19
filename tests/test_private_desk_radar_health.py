"""Desk-only observability for the 2026-10-19 forensics finding: Radar can complete
(`pipeline_status=SUCCESS`, a COMPLETE candidate-history marker) while acquiring 0 usable
symbols - a data-acquisition shortfall, not a confirmed quiet session. Recurred on ~45% of the
last 11 trading-day Radar runs (2026-10-05/06/08/12/19), each time exactly 0/200 against a full
200/200 on every genuine quiet/active day. `private_candidates`/Policy 2.0 were not at fault
(they correctly report NOT_APPLICABLE when Radar recorded zero candidates) and are untouched by
this change; only `private_desk/` learns to show the distinction. See
docs/PRIVATE_TRADE_CANDIDATES.md and the 2026-10-19 forensics in this session's plan notes.
"""
import datetime as dt
import json
import os

import pytest

from conftest import SESSION
from private_desk.services import radar_health
from test_private_desk import client, desk_out, svc  # noqa: F401  (reused fixtures)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
GAP_SESSION = dt.date(2026, 9, 19)
QUIET_SESSION = dt.date(2026, 9, 20)


# ------------------------------------------------------------------ pure function
def test_coverage_gap_is_true_only_for_a_completed_run_with_a_real_shortfall():
    full = {"pipeline_status": "SUCCESS", "universe_requested": 200, "universe_usable": 200}
    zero = {"pipeline_status": "SUCCESS", "universe_requested": 200, "universe_usable": 0}
    partial = {"pipeline_status": "SUCCESS", "universe_requested": 200, "universe_usable": 150}
    not_success = {"pipeline_status": "FAILED", "universe_requested": 200, "universe_usable": 0}
    no_universe = {"pipeline_status": "SUCCESS", "universe_requested": 0, "universe_usable": 0}

    assert radar_health.coverage_gap(full) is False
    assert radar_health.coverage_gap(zero) is True
    assert radar_health.coverage_gap(partial) is True
    assert radar_health.coverage_gap(not_success) is False
    assert radar_health.coverage_gap(no_universe) is False
    assert radar_health.coverage_gap(None) is False


# ------------------------------------------------------------------ fixture helpers
def _write_radar_artifact(out_dir: str, session: dt.date, *, usable: int, requested: int = 200):
    with open(os.path.join(out_dir, "radar", f"daily_radar_{session.isoformat()}.json"),
             "w", encoding="utf-8") as fh:
        json.dump({"session_date": session.isoformat(), "pipeline_status": "SUCCESS",
                  "generated_at": dt.datetime.now(IST).isoformat(), "issues": [],
                  "issue_counts": {"INFO": 0}, "universe_requested": requested,
                  "universe_usable": usable}, fh)


def _mark_complete(out_dir: str, session: dt.date, *, candidate_count: int = 0):
    from storage.candidate_history_repository import CandidateHistoryStore, default_db_path
    store = CandidateHistoryStore(default_db_path(out_dir))
    try:
        store.mark_run(session, "1.0", "COMPLETE", candidate_count)
    finally:
        store.close()


@pytest.fixture
def gap_session(desk_out):
    """A session where Radar completed but acquired 0/200 usable symbols - the real
    2026-10-19 shape - and recorded zero candidates."""
    _write_radar_artifact(desk_out, GAP_SESSION, usable=0)
    _mark_complete(desk_out, GAP_SESSION, candidate_count=0)
    return desk_out


@pytest.fixture
def quiet_session(desk_out):
    """A session with FULL coverage and genuinely zero candidates - must never trigger the
    data-gap warning (no noisy false alarm on an ordinary quiet day)."""
    _write_radar_artifact(desk_out, QUIET_SESSION, usable=200)
    _mark_complete(desk_out, QUIET_SESSION, candidate_count=0)
    return desk_out


# ------------------------------------------------------------------ Trade Candidates page
def test_trade_candidates_page_shows_the_data_gap_warning(client, gap_session):
    html = client.get(f"/trade-candidates?session={GAP_SESSION.isoformat()}").text
    assert "RADAR DATA GAP" in html
    assert "0 / 200" in html
    assert "not</b> a confirmed quiet session" in html


def test_trade_candidates_page_stays_quiet_on_a_genuine_zero_candidate_day(client, quiet_session):
    html = client.get(f"/trade-candidates?session={QUIET_SESSION.isoformat()}").text
    assert "RADAR DATA GAP" not in html
    assert "0 Radar candidate(s)" in html


def test_trade_candidates_page_unaffected_for_a_session_with_real_candidates(client, svc):
    """The existing fixture's own artifact (5/6 usable) is a real shortfall by the rule above,
    but this session has real stored candidates, so the warning (gated on candidate_count==0)
    must never appear."""
    html = client.get(f"/trade-candidates?session={SESSION.isoformat()}").text
    assert "RADAR DATA GAP" not in html


# ------------------------------------------------------------------ Data Quality page
def test_quality_page_flags_the_data_gap(client, gap_session):
    html = client.get(f"/quality?session={GAP_SESSION.isoformat()}").text
    assert "DATA GAP" in html


def test_quality_page_does_not_flag_full_coverage(client, quiet_session):
    html = client.get(f"/quality?session={QUIET_SESSION.isoformat()}").text
    assert "DATA GAP" not in html


def test_quality_page_flags_the_existing_fixtures_own_partial_coverage(client, svc):
    """SESSION's own synthetic artifact is 5/6 usable - a real, if small, shortfall; the Data
    Quality page's own independent check should name it, distinct from the Trade Candidates
    page (which stays quiet there because real candidates exist for SESSION)."""
    html = client.get(f"/quality?session={SESSION.isoformat()}").text
    assert "DATA GAP" in html


# ------------------------------------------------------------------ isolation
def test_radar_health_never_imports_private_candidates_radar_or_report_job():
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "private_desk", "services", "radar_health.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    banned = ("private_candidates", "radar", "products.report_job", "report_job")
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            assert m.split(".")[0] not in banned, f"{path}: imports {m}"

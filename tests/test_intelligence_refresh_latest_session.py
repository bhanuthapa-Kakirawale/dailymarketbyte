"""intelligence_refresh.latest_session: ensure_latest only calls the real REPORT job when the
latest session genuinely hasn't succeeded yet today AND its canonical report artifact genuinely
exists; dry-run/no-session paths never call it at all - idempotency for this step depends on
that."""
from __future__ import annotations

import datetime as dt
import json
import os

from intelligence_refresh.latest_session import ensure_latest, inspect_latest
from test_pipeline import offline_pipeline  # noqa: F401  (fixture re-export)
from test_production_schedule import _report_job  # noqa: F401  (offline canonical-report builder)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FRI_EVENING = dt.datetime(2026, 10, 9, 19, 30, tzinfo=IST)   # 2026-10-09 is a Friday
MONDAY_MORNING = dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST)  # offline_pipeline's own edition time
FAR_FUTURE = dt.datetime(2030, 1, 1, 7, 40, tzinfo=IST)       # outside the calendar's coverage


def _boom(**kwargs):
    raise AssertionError("report_job_fn must not be called")


def test_no_prior_run_calls_report_job_fn_once(tmp_path):
    calls = []

    def fake(*, now, calendar):
        calls.append(now)
        return {"run_status": "SUCCESS", "target_session": "2026-10-09", "run_id": "r1"}

    result = ensure_latest(str(tmp_path), FRI_EVENING, report_job_fn=fake)
    assert result["action"] == "RAN"
    assert result["report_job_run_status"] == "SUCCESS"
    assert len(calls) == 1


def test_dry_run_never_calls_report_job_fn(tmp_path):
    result = ensure_latest(str(tmp_path), FRI_EVENING, dry_run=True, report_job_fn=_boom)
    assert result["action"] == "WOULD_RUN"


def test_already_succeeded_today_skips_without_calling_report_job_fn(offline_pipeline, tmp_path):
    # A run-record file alone is no longer enough: the canonical report must genuinely exist.
    _report_job()   # actually builds + persists Friday 2026-09-18's canonical report

    result = ensure_latest(str(tmp_path), MONDAY_MORNING, report_job_fn=_boom)
    assert result["action"] == "SKIPPED"
    assert result["reason"] == "ALREADY_RUN"
    assert result["canonical_report"]["status"] == "COMPLETE"


def test_stale_success_record_with_missing_artifact_still_calls_report_job_fn(tmp_path):
    # The exact confirmed gap: a run-record file says SUCCESS but no canonical report was ever
    # actually persisted (or it was later lost) - the self-heal must not trust the record alone.
    session = "2026-10-09"
    folder = os.path.join(str(tmp_path), "report_jobs", session)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report_job_r1.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_status": "SUCCESS", "target_session": session, "run_id": "r1"}, fh)

    calls = []
    result = ensure_latest(str(tmp_path), FRI_EVENING,
                           report_job_fn=lambda **kw: (calls.append(1) or
                                                      {"run_status": "SUCCESS",
                                                       "target_session": session,
                                                       "run_id": "r2"}))
    assert result["action"] == "RAN"
    assert result["rebuilt_stale_record"] is True
    assert len(calls) == 1


def test_report_job_fn_exception_is_recovered_not_propagated(tmp_path):
    session = "2026-10-09"
    folder = os.path.join(str(tmp_path), "report_jobs", session)
    os.makedirs(folder, exist_ok=True)
    record_path = os.path.join(folder, "report_job_r1.json")

    def fake(*, now, calendar):
        # mirrors run_report_job's own behaviour: write a FAILED/EXCEPTION record, then raise.
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump({"run_status": "FAILED", "stage": "EXCEPTION", "target_session": session,
                      "run_id": "r1", "failure_reason": "RuntimeError: boom"}, fh)
        raise RuntimeError("boom")

    result = ensure_latest(str(tmp_path), FRI_EVENING, report_job_fn=fake)
    assert result["action"] == "RAN"
    assert result["report_job_run_status"] == "FAILED"
    assert "RuntimeError: boom" in result["exception"]
    assert result["report_job_record_path"] == f"report_jobs/{session}/report_job_r1.json"


def test_report_job_fn_exception_with_no_record_file_defaults_failed(tmp_path):
    def fake(*, now, calendar):
        raise RuntimeError("boom, no record written")

    result = ensure_latest(str(tmp_path), FRI_EVENING, report_job_fn=fake)
    assert result["action"] == "RAN"
    assert result["report_job_run_status"] == "FAILED"
    assert result["report_job_record_path"] is None
    assert "boom, no record written" in result["exception"]


def test_canonical_report_complete_after_successful_build(offline_pipeline, tmp_path):
    _report_job()
    result = ensure_latest(str(tmp_path), MONDAY_MORNING, report_job_fn=_boom)
    assert result["canonical_report"]["status"] == "COMPLETE"
    assert result["canonical_report"]["expected_session"] == "2026-09-18"


def test_session_unknown_includes_unknown_canonical_report_block(tmp_path):
    result = ensure_latest(str(tmp_path), FAR_FUTURE, report_job_fn=_boom)
    assert result["action"] == "SKIPPED"
    assert result["reason"] == "SESSION_UNKNOWN"
    assert result["canonical_report"] == {"expected_session": None, "status": "UNKNOWN"}


def test_monday_evening_missed_entirely_tuesday_morning_resolves_and_builds_it(tmp_path):
    # Primary acceptance scenario: Monday's evening REPORT never ran (laptop was off), Tuesday
    # 06:40 must resolve to Monday and attempt to build it - never Tuesday itself (unfinished).
    tuesday_morning = dt.datetime(2026, 9, 22, 6, 40, tzinfo=IST)
    calls = []

    def fake(*, now, calendar):
        calls.append(now)
        return {"run_status": "SUCCESS", "target_session": "2026-09-21", "run_id": "r1"}

    result = ensure_latest(str(tmp_path), tuesday_morning, report_job_fn=fake)
    assert result["action"] == "RAN"
    assert result["session"] == "2026-09-21"
    assert len(calls) == 1


def test_a_blocked_prior_run_is_retried_not_skipped(tmp_path):
    session = "2026-10-09"
    folder = os.path.join(str(tmp_path), "report_jobs", session)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report_job_r1.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_status": "BLOCKED", "target_session": session, "run_id": "r1"}, fh)

    calls = []
    result = ensure_latest(str(tmp_path), FRI_EVENING,
                           report_job_fn=lambda **kw: (calls.append(1) or
                                                      {"run_status": "SUCCESS",
                                                       "target_session": session,
                                                       "run_id": "r2"}))
    assert result["action"] == "RAN"
    assert len(calls) == 1


def test_inspect_latest_is_read_only(tmp_path):
    out = inspect_latest(str(tmp_path), FRI_EVENING)
    assert out["already_run"] is False
    assert out["session"] == "2026-10-09"

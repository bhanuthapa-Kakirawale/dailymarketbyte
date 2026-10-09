"""intelligence_refresh.latest_session: ensure_latest only calls the real REPORT job when the
latest session genuinely hasn't succeeded yet today; dry-run/no-session paths never call it at
all - idempotency for this step depends on that."""
from __future__ import annotations

import datetime as dt

from intelligence_refresh.latest_session import ensure_latest, inspect_latest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FRI_EVENING = dt.datetime(2026, 10, 9, 19, 30, tzinfo=IST)   # 2026-10-09 is a Friday


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


def test_already_succeeded_today_skips_without_calling_report_job_fn(tmp_path):
    import json
    import os
    session = "2026-10-09"
    folder = os.path.join(str(tmp_path), "report_jobs", session)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report_job_r1.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_status": "SUCCESS", "target_session": session, "run_id": "r1"}, fh)

    result = ensure_latest(str(tmp_path), FRI_EVENING, report_job_fn=_boom)
    assert result["action"] == "SKIPPED"
    assert result["reason"] == "ALREADY_RUN"


def test_a_blocked_prior_run_is_retried_not_skipped(tmp_path):
    import json
    import os
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

"""shadow_scheduler.status: MISSED/PENDING/NOT_EXPECTED/UNKNOWN with an injected clock +
calendar (no wall-clock-dependent test anywhere here), the HEALTHY/ATTENTION/BROKEN
aggregation, and that `overall_status`/`job_status` never write anything - even when the only
`open()` available raises on write mode, status still succeeds (a behavioral companion to the
static AST check in test_shadow_scheduler_security.py)."""
from __future__ import annotations

import builtins
import datetime as dt
import json
import os

from shadow_scheduler import config, records, status
from shadow_scheduler.models import ATTENTION, BROKEN, HEALTHY, MISSED, NOT_EXPECTED, OK, \
    PENDING, UNKNOWN

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class _FixedCalendar:
    def __init__(self, is_session):
        self._is_session = is_session

    def is_session(self, d):
        return self._is_session


def test_pending_before_scheduled_time_on_a_trading_day(tmp_path):
    now_ist = dt.datetime(2026, 10, 9, 6, 0, tzinfo=IST)   # before 06:40
    spec = config.JOB_REGISTRY["intelligence_am"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == PENDING


def test_missed_after_scheduled_time_with_no_record_on_a_trading_day(tmp_path):
    now_ist = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)   # well after 06:40
    spec = config.JOB_REGISTRY["intelligence_am"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == MISSED


def test_ok_after_scheduled_time_when_todays_record_exists(tmp_path):
    out_dir = str(tmp_path)
    from shadow_scheduler.models import COMPLETED, RunRecord
    rec = RunRecord(job="intelligence_am", run_id="r1",
                    started_at="2026-10-09T01:10:00+00:00", runner_status=COMPLETED,
                    child_exit_code=0)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    spec = config.JOB_REGISTRY["intelligence_am"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == OK


def test_not_expected_on_a_non_trading_day(tmp_path):
    now_ist = dt.datetime(2026, 10, 10, 8, 0, tzinfo=IST)   # Saturday, say
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(False))
    assert row["missed_state"] == NOT_EXPECTED


def test_unknown_is_never_asserted_as_missed_or_not_expected(tmp_path):
    now_ist = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    spec = config.JOB_REGISTRY["post"]
    row = status.job_status(str(tmp_path), str(tmp_path), spec, now_ist, _FixedCalendar(None))
    assert row["missed_state"] == UNKNOWN
    assert row["missed_state"] not in (MISSED, NOT_EXPECTED)


def test_overall_healthy_when_nothing_missed_and_business_is_ok(tmp_path):
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    now_ist = dt.datetime(2026, 10, 9, 6, 0, tzinfo=IST)   # before every job's scheduled time
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == HEALTHY


def test_overall_attention_on_business_blocked(tmp_path):
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    pre_latest_dir = os.path.join(business_root, "production_runs", "PRE")
    os.makedirs(pre_latest_dir, exist_ok=True)
    with open(os.path.join(pre_latest_dir, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump({"orchestrator_status": "BLOCKED", "started_at": "2026-10-09T01:35:00+00:00"},
                 fh)
    # A business result only ever appears today because the scheduler's OWN run record for
    # this job is also from today - the runner always writes its own record before invoking
    # the child that eventually updates the business latest.json.
    from shadow_scheduler.models import CHILD_NONZERO, RunRecord
    # PK-OPS-1: an in-window (07:05 IST) PRE run, observed afterwards (07:30) - a record invoked
    # after `now`, or outside PRE's own window, is never today's scheduled execution.
    records.write_record(out_dir, RunRecord(job="pre", run_id="r1",
                                            started_at="2026-10-09T01:35:00+00:00",
                                            runner_status=CHILD_NONZERO, child_exit_code=20))
    now_ist = dt.datetime(2026, 10, 9, 7, 30, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == ATTENTION


def test_overall_attention_on_a_missed_job(tmp_path):
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    now_ist = dt.datetime(2026, 10, 9, 23, 0, tzinfo=IST)   # after every job's scheduled time
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == ATTENTION
    assert any(row["missed_state"] == MISSED for row in s["jobs"].values())


def test_overall_broken_on_a_runner_infra_failure(tmp_path):
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    from shadow_scheduler.models import FAILED, RunRecord
    rec = RunRecord(job="post", run_id="r1", started_at="2026-10-09T01:00:00+00:00",
                    runner_status=FAILED, error="boom")
    records.write_record(out_dir, rec)
    # PK-OPS-1: observed after the (06:30 IST) invocation - an infra FAILED record is BROKEN
    # whichever occurrence it is attributed to.
    now_ist = dt.datetime(2026, 10, 9, 6, 45, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == BROKEN


def test_overall_status_never_writes_anything(tmp_path, monkeypatch):
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    real_open = builtins.open

    def guarded_open(path, mode="r", *args, **kwargs):
        if "w" in mode or "a" in mode or "x" in mode or "+" in mode:
            raise AssertionError(f"status.py attempted to open {path!r} for writing")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(status, "open", guarded_open, raising=False)
    monkeypatch.setattr(records, "open", guarded_open, raising=False)
    now_ist = dt.datetime(2026, 10, 9, 6, 0, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] in (HEALTHY, ATTENTION, BROKEN)


# --- dependency ordering (PK-E follow-up) ---------------------------------------------------

def test_job_status_exposes_the_dependency_fields_from_the_scheduler_record(tmp_path):
    from shadow_scheduler.models import COMPLETED, RunRecord
    out_dir = str(tmp_path)
    rec = RunRecord(job="pre", run_id="r1", started_at="2026-10-09T01:30:00+00:00",
                    runner_status=COMPLETED, dependency_job="intelligence_am",
                    dependency_status="SATISFIED", dependency_wait_seconds=12.5)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(True))
    assert row["dependency_job"] == "intelligence_am"
    assert row["dependency_status"] == "SATISFIED"
    assert row["dependency_wait_seconds"] == 12.5


def test_waiting_dependency_is_shown_but_does_not_push_overall_away_from_healthy(tmp_path):
    """An in-flight WAITING_DEPENDENCY checkpoint is not itself unhealthy - same treatment the
    ordinary RUNNING checkpoint already gets."""
    from shadow_scheduler.models import WAITING_DEPENDENCY, RunRecord
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    rec = RunRecord(job="pre", run_id="r1", started_at="2026-10-09T01:35:00+00:00",
                    runner_status=WAITING_DEPENDENCY, dependency_job="intelligence_am")
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 7, 36, tzinfo=IST)   # inside PRE's window
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == HEALTHY
    assert s["jobs"]["pre"]["runner_status"] == WAITING_DEPENDENCY


def test_dependency_failed_pushes_overall_to_attention_not_broken(tmp_path):
    from shadow_scheduler.models import DEPENDENCY_FAILED, RunRecord
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    rec = RunRecord(job="pre", run_id="r1", started_at="2026-10-09T01:35:00+00:00",
                    runner_status=DEPENDENCY_FAILED, dependency_job="intelligence_am",
                    dependency_status="FAILED", error="dependency failed")
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 7, 36, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == ATTENTION
    assert s["overall"] != BROKEN


def test_render_human_shows_dependency_failed_status_and_counts_it(tmp_path):
    from shadow_scheduler.models import DEPENDENCY_FAILED, RunRecord
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    rec = RunRecord(job="post", run_id="r1", started_at="2026-10-09T14:00:00+00:00",
                    runner_status=DEPENDENCY_FAILED)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 20, 0, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    text = status.render_human(s)
    assert "DEPENDENCY_FAILED" in text
    assert "Dependency failures: 1" in text


def test_render_human_contains_every_job_and_the_overall_verdict(tmp_path):
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    now_ist = dt.datetime(2026, 10, 9, 6, 0, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    text = status.render_human(s)
    for label in ("Intelligence AM", "PRE", "Intelligence PM", "POST"):
        assert label in text
    assert "AUTOMATION HEALTH" in text


# --- TODAY vs HISTORICAL (PK-E power/status hardening, 2026-10-16) -------------------------
# A prior day's terminal scheduler/business record must never be read as today's operational
# state - this is exactly the defect that let shadow status show yesterday evening's
# Intelligence PM/POST as COMPLETED at 07:30 this morning, before today's 19:15/19:30 runs.

def _write_latest_business(business_root, edition_dir, status_value, started_at_utc):
    d = os.path.join(business_root, *edition_dir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump({"orchestrator_status": status_value, "started_at": started_at_utc}, fh)


def test_a_yesterday_evening_completed_run_shows_as_pending_this_morning(tmp_path):
    """Case A: yesterday's PM COMPLETED + today 07:30 -> Intelligence PM must be PENDING
    (or WAITING/MISSED per the window), never COMPLETED carried over from yesterday."""
    out_dir = str(tmp_path / "ss")
    from shadow_scheduler.models import COMPLETED, RunRecord
    rec = RunRecord(job="intelligence_pm", run_id="r1",
                    started_at="2026-10-08T13:45:00+00:00",   # 2026-10-08 19:15 IST
                    runner_status=COMPLETED, child_exit_code=0)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 7, 30, tzinfo=IST)   # next morning, well before 19:15
    spec = config.JOB_REGISTRY["intelligence_pm"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(True))
    assert row["runner_status"] is None
    assert row["missed_state"] == PENDING
    assert row["last_run"]["runner_status"] == COMPLETED
    assert row["last_run"]["date"] == "2026-10-08"


def test_yesterdays_post_success_is_not_shown_as_todays_business_result(tmp_path):
    """Case B: yesterday's POST SUCCESS must not appear as today's business result."""
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    _write_latest_business(business_root, ("production_runs", "POST"), "SUCCESS",
                           "2026-10-08T14:47:00+00:00")   # yesterday 20:17 IST
    now_ist = dt.datetime(2026, 10, 9, 7, 30, tzinfo=IST)
    spec = config.JOB_REGISTRY["post"]
    row = status.job_status(out_dir, business_root, spec, now_ist, _FixedCalendar(True))
    assert row["business_status"] is None
    assert row["last_run"]["business_status"] == "SUCCESS"


def test_todays_am_run_displays_its_actual_run_time(tmp_path):
    """Case C: today's AM run must show its own IST run time via `ran_at` / render_human's
    `ran=`, not a historical placeholder."""
    out_dir = str(tmp_path / "ss")
    from shadow_scheduler.models import COMPLETED, RunRecord
    rec = RunRecord(job="intelligence_am", run_id="r1",
                    started_at="2026-10-09T01:54:00+00:00",   # 07:24 IST
                    runner_status=COMPLETED, child_exit_code=0)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 8, 0, tzinfo=IST)
    spec = config.JOB_REGISTRY["intelligence_am"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(True))
    assert row["ran_at"] == "07:24"
    s = {"jobs": {"intelligence_am": row, "pre": row, "intelligence_pm": row, "post": row},
        "overall": HEALTHY}
    text = status.render_human(s)
    assert "ran=07:24" in text


def test_todays_waiting_dependency_is_the_correct_state(tmp_path):
    """Case D: today's PRE WAITING_DEPENDENCY checkpoint must be shown as such, not folded
    into a historical record."""
    from shadow_scheduler.models import WAITING_DEPENDENCY, RunRecord
    out_dir = str(tmp_path)
    rec = RunRecord(job="pre", run_id="r1", started_at="2026-10-09T01:35:00+00:00",
                    runner_status=WAITING_DEPENDENCY, dependency_job="intelligence_am")
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 7, 36, tzinfo=IST)
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(True))
    assert row["runner_status"] == WAITING_DEPENDENCY


def test_missed_after_todays_deadline_with_no_invocation_at_all(tmp_path):
    """Case E: after today's deadline with no run today (even if a much older record exists for
    a different day), the job must be MISSED."""
    out_dir = str(tmp_path)
    from shadow_scheduler.models import COMPLETED, RunRecord
    rec = RunRecord(job="post", run_id="r1", started_at="2026-10-01T14:00:00+00:00",
                    runner_status=COMPLETED, child_exit_code=0)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 9, 21, 0, tzinfo=IST)   # well after POST's window
    spec = config.JOB_REGISTRY["post"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(True))
    assert row["missed_state"] == MISSED
    assert row["runner_status"] is None


def test_weekend_holiday_is_not_expected_regardless_of_old_records(tmp_path):
    """Case F: a non-trading day is NOT_EXPECTED even if an old (any-day) record exists."""
    out_dir = str(tmp_path)
    from shadow_scheduler.models import COMPLETED, RunRecord
    rec = RunRecord(job="pre", run_id="r1", started_at="2026-10-08T01:30:00+00:00",
                    runner_status=COMPLETED, child_exit_code=0)
    records.write_record(out_dir, rec)
    now_ist = dt.datetime(2026, 10, 10, 8, 0, tzinfo=IST)   # Saturday
    spec = config.JOB_REGISTRY["pre"]
    row = status.job_status(out_dir, out_dir, spec, now_ist, _FixedCalendar(False))
    assert row["missed_state"] == NOT_EXPECTED
    assert row["runner_status"] is None


def test_overall_status_not_pushed_to_attention_by_a_stale_business_record(tmp_path):
    """07:30 IST, PM/POST still PENDING, but yesterday's POST business result was BLOCKED -
    automation health must stay HEALTHY, not ATTENTION, until today's own run exists."""
    out_dir = str(tmp_path / "ss")
    business_root = str(tmp_path / "out")
    _write_latest_business(business_root, ("production_runs", "POST"), "BLOCKED",
                           "2026-10-08T14:00:00+00:00")   # yesterday
    now_ist = dt.datetime(2026, 10, 9, 7, 30, tzinfo=IST)
    s = status.overall_status(out_dir, business_root=business_root, now=now_ist,
                              calendar=_FixedCalendar(True))
    assert s["overall"] == HEALTHY

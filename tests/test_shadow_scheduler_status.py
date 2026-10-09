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
        json.dump({"orchestrator_status": "BLOCKED"}, fh)
    now_ist = dt.datetime(2026, 10, 9, 6, 0, tzinfo=IST)
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
    now_ist = dt.datetime(2026, 10, 9, 6, 0, tzinfo=IST)
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

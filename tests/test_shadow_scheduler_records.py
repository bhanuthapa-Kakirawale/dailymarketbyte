"""shadow_scheduler.records: write order (immutable run file before the job's latest.json),
log path/dir naming, and that a rerun never touches an earlier immutable file - same pattern
as production_orchestrator/intelligence_refresh's own manifest.py."""
from __future__ import annotations

import datetime as dt
import os

from shadow_scheduler import records
from shadow_scheduler.models import COMPLETED, RunRecord

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _record(run_id, job="post", status=COMPLETED):
    return RunRecord(job=job, run_id=run_id, started_at="2026-10-09T01:00:00+00:00",
                     completed_at="2026-10-09T01:05:00+00:00", duration=300.0,
                     hostname="h", pid=1, repo_path="D:\\repo",
                     python_executable="D:\\repo\\venv\\Scripts\\python.exe",
                     child_command=["x.bat"], child_exit_code=0, runner_status=status,
                     log_path="logs/2026-10-09/post_010000.log")


def test_write_record_writes_run_file_before_latest(tmp_path, monkeypatch):
    order = []
    real_replace = os.replace

    def spy_replace(src, dst):
        order.append(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(records.os, "replace", spy_replace)
    rec = _record("run-1")
    records.write_record(str(tmp_path), rec)
    assert len(order) == 2
    assert order[0].endswith("run-1.json")
    assert order[1].endswith(os.path.join("latest", "post.json"))


def test_read_latest_after_write_points_at_a_file_that_exists(tmp_path):
    rec = _record("run-1")
    path = records.write_record(str(tmp_path), rec)
    assert os.path.exists(path)
    latest = records.read_latest(str(tmp_path), "post")
    assert latest["run_id"] == "run-1"
    full_path = os.path.join(str(tmp_path), latest["path"])
    assert os.path.exists(full_path)


def test_rerun_with_different_run_id_never_touches_earlier_immutable_file(tmp_path):
    rec1 = _record("run-1")
    path1 = records.write_record(str(tmp_path), rec1)
    with open(path1, encoding="utf-8") as fh:
        original = fh.read()

    rec2 = _record("run-2")
    path2 = records.write_record(str(tmp_path), rec2)
    assert path1 != path2
    with open(path1, encoding="utf-8") as fh:
        assert fh.read() == original
    latest = records.read_latest(str(tmp_path), "post")
    assert latest["run_id"] == "run-2"


def test_each_job_has_its_own_latest_pointer(tmp_path):
    records.write_record(str(tmp_path), _record("run-pre", job="pre"))
    records.write_record(str(tmp_path), _record("run-post", job="post"))
    assert records.read_latest(str(tmp_path), "pre")["run_id"] == "run-pre"
    assert records.read_latest(str(tmp_path), "post")["run_id"] == "run-post"


def test_read_latest_missing_job_is_none(tmp_path):
    assert records.read_latest(str(tmp_path), "pre") is None


def test_list_records_skips_unreadable_files(tmp_path):
    records.write_record(str(tmp_path), _record("run-1"))
    bad_path = os.path.join(records.runs_dir(str(tmp_path)), "run-bad.json")
    os.makedirs(os.path.dirname(bad_path), exist_ok=True)
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    found = records.list_records(str(tmp_path))
    assert len(found) == 1
    assert found[0]["run_id"] == "run-1"


def test_new_run_id_is_sortable_and_unique():
    now = dt.datetime(2026, 10, 9, 1, 0, 0, tzinfo=UTC)
    a = records.new_run_id(now)
    b = records.new_run_id(now)
    assert a.startswith("20261009T010000Z_")
    assert a != b


def test_log_path_uses_ist_calendar_date_and_time():
    started_ist = dt.datetime(2026, 10, 9, 6, 40, 1, tzinfo=IST)
    rel = records.log_path("ignored", "intelligence_am", started_ist)
    assert rel == os.path.join("logs", "2026-10-09", "intelligence_am_064001.log")


def test_write_record_refuses_production_path_in_test_context(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "RUN_CONTEXT", "test", raising=False)
    monkeypatch.setenv("DMB_RUN_CONTEXT", "test")
    from operations.run_context import ProductionPathError
    import pytest
    prod_path = os.path.join(config.PRODUCTION_OUT_DIR, "shadow_scheduler")
    with pytest.raises(ProductionPathError):
        records._atomic_write(os.path.join(prod_path, "runs", "x.json"), {"a": 1},
                              "shadow scheduler run record")

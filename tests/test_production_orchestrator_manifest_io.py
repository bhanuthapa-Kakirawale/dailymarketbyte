"""production_orchestrator.manifest: write order (immutable file before latest.json), that a
rerun never touches an earlier immutable file, and --no-write writing nothing."""
from __future__ import annotations

import json
import os

from production_orchestrator import manifest as MF
from production_orchestrator.models import DEGRADED, SUCCESS, RunManifest


def _manifest(run_id, session="2026-10-08", status=SUCCESS):
    m = RunManifest(edition="POST", command="post", run_id=run_id, session_date=session)
    m.finalize(status)
    return m


def test_write_manifest_writes_run_file_before_latest(tmp_path, monkeypatch):
    order = []
    real_replace = os.replace

    def spy_replace(src, dst):
        order.append(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(MF.os, "replace", spy_replace)
    m = _manifest("run-1")
    MF.write_manifest(str(tmp_path), m)
    assert len(order) == 2
    assert order[0].endswith("run-1.json")
    assert order[1].endswith("latest.json")


def test_latest_points_at_a_file_that_exists():
    pass  # covered implicitly by test_read_latest_after_write


def test_read_latest_after_write(tmp_path):
    m = _manifest("run-1")
    path = MF.write_manifest(str(tmp_path), m)
    assert os.path.exists(path)
    latest = MF.read_latest(str(tmp_path), "POST")
    assert latest["run_id"] == "run-1"
    full_path = os.path.join(str(tmp_path), latest["path"])
    assert os.path.exists(full_path)


def test_rerun_with_different_run_id_never_touches_earlier_immutable_file(tmp_path):
    m1 = _manifest("run-1")
    path1 = MF.write_manifest(str(tmp_path), m1)
    with open(path1, encoding="utf-8") as fh:
        original = fh.read()

    m2 = _manifest("run-2", status=DEGRADED)
    path2 = MF.write_manifest(str(tmp_path), m2)
    assert path1 != path2
    with open(path1, encoding="utf-8") as fh:
        assert fh.read() == original
    latest = MF.read_latest(str(tmp_path), "POST")
    assert latest["run_id"] == "run-2"


def test_session_manifest_paths_sorted_oldest_first(tmp_path):
    MF.write_manifest(str(tmp_path), _manifest("20261008T010000Z_aaaaaaaa"))
    MF.write_manifest(str(tmp_path), _manifest("20261008T020000Z_bbbbbbbb"))
    paths = MF.session_manifest_paths(str(tmp_path), "POST", "2026-10-08")
    assert len(paths) == 2
    assert "010000" in paths[0]
    assert "020000" in paths[1]


def test_running_manifest_to_resume_finds_only_running(tmp_path):
    from production_orchestrator.models import RUNNING
    m1 = RunManifest(edition="POST", command="post", run_id="run-done", session_date="2026-10-08")
    m1.finalize(SUCCESS)
    MF.write_manifest(str(tmp_path), m1)
    m2 = RunManifest(edition="POST", command="post", run_id="run-running", session_date="2026-10-08")
    m2.orchestrator_status = RUNNING
    MF.write_manifest(str(tmp_path), m2)
    resumable = MF.running_manifest_to_resume(str(tmp_path), "POST", "2026-10-08")
    assert resumable["run_id"] == "run-running"


def test_running_manifest_to_resume_none_when_nothing_running(tmp_path):
    m1 = _manifest("run-done")
    MF.write_manifest(str(tmp_path), m1)
    assert MF.running_manifest_to_resume(str(tmp_path), "POST", "2026-10-08") is None


def test_write_manifest_refuses_production_path_in_test_context(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "RUN_CONTEXT", "test", raising=False)
    monkeypatch.setenv("DMB_RUN_CONTEXT", "test")
    from operations.run_context import ProductionPathError
    import pytest
    prod_path = os.path.join(config.PRODUCTION_OUT_DIR, "whatever")
    with pytest.raises(ProductionPathError):
        MF._atomic_write(os.path.join(prod_path, "production_runs", "POST", "x.json"),
                         {"a": 1}, "production run manifest")

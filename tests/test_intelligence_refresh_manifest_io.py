"""intelligence_refresh.manifest: atomic write ordering (run file before latest.json),
running_manifest_to_resume selection."""
from __future__ import annotations

import os

from intelligence_refresh import manifest, models


def test_write_manifest_writes_run_file_then_latest(tmp_path):
    out_dir = str(tmp_path)
    man = models.RunManifest(run_id="r1", orchestrator_status=models.SUCCESS)
    path = manifest.write_manifest(out_dir, man)
    assert os.path.exists(path)
    assert os.path.exists(manifest.latest_path(out_dir))
    latest = manifest.read_latest(out_dir)
    assert latest["run_id"] == "r1"


def test_running_manifest_to_resume_finds_the_newest_running_one(tmp_path):
    out_dir = str(tmp_path)
    m1 = models.RunManifest(run_id="r1", started_at="2026-10-01T00:00:00+00:00",
                            orchestrator_status="RUNNING")
    m2 = models.RunManifest(run_id="r2", started_at="2026-10-02T00:00:00+00:00",
                            orchestrator_status="RUNNING")
    manifest.write_manifest(out_dir, m1)
    manifest.write_manifest(out_dir, m2)
    leftover = manifest.running_manifest_to_resume(out_dir)
    assert leftover["run_id"] == "r2"


def test_terminal_manifest_is_not_offered_for_resume(tmp_path):
    out_dir = str(tmp_path)
    m1 = models.RunManifest(run_id="r1", orchestrator_status=models.SUCCESS)
    manifest.write_manifest(out_dir, m1)
    assert manifest.running_manifest_to_resume(out_dir) is None


def test_read_latest_missing_file_returns_none(tmp_path):
    assert manifest.read_latest(str(tmp_path)) is None

"""production_orchestrator.status: read-only, no side effects, never acquires/renders/evaluates."""
from __future__ import annotations

from production_orchestrator import manifest as MF
from production_orchestrator import status as ST
from production_orchestrator.models import SUCCESS, RunManifest


def test_status_no_run_yet(tmp_path):
    s = ST.status(str(tmp_path))
    assert s == {"PRE": None, "POST": None}


def test_status_reflects_latest_pointer(tmp_path):
    m = RunManifest(edition="POST", command="post", run_id="run-1", session_date="2026-10-08")
    m.finalize(SUCCESS)
    MF.write_manifest(str(tmp_path), m)
    s = ST.status(str(tmp_path))
    assert s["PRE"] is None
    assert s["POST"]["run_id"] == "run-1"
    assert s["POST"]["orchestrator_status"] == SUCCESS


def test_status_never_writes_anything(tmp_path):
    before = sorted(tmp_path.rglob("*"))
    ST.status(str(tmp_path))
    after = sorted(tmp_path.rglob("*"))
    assert before == after == []

"""private_desk.services.production_runs: the PK-D panel is read-only JSON, never a fetch, never
a write, and is wired into services.quality.data_quality()'s own dict."""
from __future__ import annotations

import json
import os

from private_desk.repository import DeskRepository
from private_desk.services.production_runs import production_orchestrator_status


def test_no_run_yet(tmp_path):
    rows = production_orchestrator_status(DeskRepository(str(tmp_path)))
    assert {r["edition"] for r in rows} == {"PRE", "POST"}
    assert all(r["status"] == "NO_RUN" for r in rows)


def test_unreadable_latest_json(tmp_path):
    folder = tmp_path / "production_runs" / "POST"
    folder.mkdir(parents=True)
    (folder / "latest.json").write_text("{not json", encoding="utf-8")
    rows = production_orchestrator_status(DeskRepository(str(tmp_path)))
    post = next(r for r in rows if r["edition"] == "POST")
    assert post["status"] == "UNREADABLE"


def test_populated_latest_json_is_reported_verbatim(tmp_path):
    folder = tmp_path / "production_runs" / "POST"
    folder.mkdir(parents=True)
    payload = {"orchestrator_status": "DEGRADED", "readiness_status_preflight": "DEGRADED",
              "readiness_status_post_render": "READY", "session_date": "2026-10-08",
              "run_id": "run-1", "started_at": "t0", "completed_at": "t1",
              "blocking_reasons": [], "warnings": ["w1"]}
    (folder / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    rows = production_orchestrator_status(DeskRepository(str(tmp_path)))
    post = next(r for r in rows if r["edition"] == "POST")
    assert post["status"] == "DEGRADED"
    assert post["readiness_preflight"] == "DEGRADED"
    assert post["readiness_post_render"] == "READY"
    assert post["run_id"] == "run-1"
    assert post["warnings"] == ["w1"]


def test_never_writes_anything(tmp_path):
    folder = tmp_path / "production_runs" / "POST"
    folder.mkdir(parents=True)
    (folder / "latest.json").write_text(json.dumps({"orchestrator_status": "SUCCESS"}),
                                        encoding="utf-8")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    production_orchestrator_status(DeskRepository(str(tmp_path)))
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert before == after


def test_data_quality_includes_production_orchestrator_key(tmp_path):
    from private_desk.services.quality import data_quality
    out = data_quality(DeskRepository(str(tmp_path)), None, None, {})
    assert "production_orchestrator" in out
    assert {r["edition"] for r in out["production_orchestrator"]} == {"PRE", "POST"}

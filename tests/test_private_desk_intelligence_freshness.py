"""private_desk.services.intelligence_freshness: the Data Quality panel is read-only JSON,
never a fetch, never a write, and is wired into services.quality.data_quality()'s own dict -
mirrors tests/test_production_orchestrator_private_desk.py's style exactly."""
from __future__ import annotations

import json

from private_desk.repository import DeskRepository
from private_desk.services.intelligence_freshness import intelligence_refresh_status


def test_no_run_yet(tmp_path):
    out = intelligence_refresh_status(DeskRepository(str(tmp_path)))
    assert out["status"] == "NO_RUN"


def test_unreadable_latest_json(tmp_path):
    folder = tmp_path / "intelligence_refresh"
    folder.mkdir(parents=True)
    (folder / "latest.json").write_text("{not json", encoding="utf-8")
    out = intelligence_refresh_status(DeskRepository(str(tmp_path)))
    assert out["status"] == "UNREADABLE"


def test_populated_latest_json_is_reported_verbatim(tmp_path):
    folder = tmp_path / "intelligence_refresh"
    folder.mkdir(parents=True)
    payload = {"orchestrator_status": "SUCCESS", "run_id": "r1", "started_at": "t0",
              "completed_at": "t1", "latest_session_result": {"session": "2026-10-09"},
              "summary": {"sessions_audited": 5}, "blocking_reasons": [], "warnings": ["w1"]}
    (folder / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    out = intelligence_refresh_status(DeskRepository(str(tmp_path)))
    assert out["status"] == "SUCCESS"
    assert out["run_id"] == "r1"
    assert out["summary"]["sessions_audited"] == 5
    assert out["warnings"] == ["w1"]


def test_never_writes_anything(tmp_path):
    folder = tmp_path / "intelligence_refresh"
    folder.mkdir(parents=True)
    (folder / "latest.json").write_text(json.dumps({"orchestrator_status": "SUCCESS"}),
                                        encoding="utf-8")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    intelligence_refresh_status(DeskRepository(str(tmp_path)))
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert before == after


def test_data_quality_includes_intelligence_refresh_key(tmp_path):
    from private_desk.services.quality import data_quality
    out = data_quality(DeskRepository(str(tmp_path)), None, None, {})
    assert "intelligence_refresh" in out
    assert out["intelligence_refresh"]["status"] == "NO_RUN"

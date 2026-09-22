"""Canonical history is immutable once persisted.

A report states what the market did. Video QA, the final content scan and the upload result
state what happened when we tried to publish it. The first must never be rewritten because of
the second - otherwise "what did the 22 Sep report say?" becomes unanswerable the moment
anything downstream goes wrong.

These tests exercise the real production orchestration (`main.run`) against the stubbed
pipeline from test_pipeline.py, so they prove the behaviour of the shipped code path rather
than of a re-implementation.
"""
import datetime as dt
import hashlib
import json
import os

import pytest

from conftest import build_test_report
from test_pipeline import _Args, _failing_video_qa, offline_pipeline    # noqa: F401

import main
from core import MarketReport
from storage import MarketHistory


def _db(tmp_path) -> str:
    return str(tmp_path / "data" / "market_history.db")


def _canonical_snapshot(db_path: str) -> dict:
    """Every canonical row, in a form that compares exactly. Operational tables excluded."""
    with MarketHistory(db_path) as history:
        snapshot = {}
        for table in ("reports", "facts", "observations", "validation_results",
                      "catalysts", "events"):
            rows = history.conn.execute(f"SELECT * FROM {table}").fetchall()
            snapshot[table] = [dict(r) for r in rows]
        return snapshot


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _report_json(tmp_path) -> str:
    reports = tmp_path / "reports"
    return str(next(reports.iterdir()))


# --------------------------------------------------------------------- Test A
def test_production_never_replaces_canonical_history(offline_pipeline, monkeypatch, tmp_path):
    """The normal path calls save_report once, without the destructive replace flag."""
    calls = []
    original = MarketHistory.save_report

    def _spy(self, report, artifact_path=None, replace=False, is_demo=None):
        calls.append({"report_id": report.report_id, "replace": replace})
        return original(self, report, artifact_path=artifact_path, replace=replace,
                        is_demo=is_demo)

    monkeypatch.setattr(MarketHistory, "save_report", _spy)
    main.run(_Args())

    assert calls, "the canonical report should have been persisted"
    assert all(c["replace"] is False for c in calls), \
        f"production must never replace canonical history: {calls}"
    assert len(calls) == 1, f"the canonical report is written exactly once per run: {calls}"


def test_orchestration_has_no_refresh_path():
    """Guards against the helper coming back: no production route passes replace=True.

    Checked by parsing the module rather than grepping it, so the explanatory comment about
    why replacement is absent cannot itself trip the assertion.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(main))
    replacing = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "replace" and isinstance(kw.value, ast.Constant) and kw.value.value is True
    ]
    assert not replacing, "main.py must never call a persistence API with replace=True"
    assert not hasattr(main, "_refresh_history")


# --------------------------------------------------------------------- Test B
def test_video_qa_failure_cannot_mutate_canonical_history(offline_pipeline, monkeypatch,
                                                          tmp_path):
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))
    json_before = _sha256(_report_json(tmp_path))

    monkeypatch.setattr(main, "check_video", _failing_video_qa)
    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("must not publish when video QA fails"))
    main.run(_Args(upload=True))

    assert _canonical_snapshot(_db(tmp_path)) == before, \
        "video QA is an operational verdict and must not touch canonical rows"
    assert _sha256(_report_json(tmp_path)) == json_before

    with MarketHistory(_db(tmp_path)) as history:
        run = history.get_publication_runs()[0]
        assert run.failure_stage == "VIDEO_QA"
        assert run.video_qa_status == "FAIL"
        assert run.publication_status == "BLOCKED"


# --------------------------------------------------------------------- Test C
def test_final_content_qa_failure_cannot_mutate_canonical_history(offline_pipeline, monkeypatch,
                                                                  tmp_path):
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))
    json_before = _sha256(_report_json(tmp_path))

    real_metadata = main.build_metadata

    def _unsafe_metadata(*a, **k):
        meta = real_metadata(*a, **k)
        meta["title"] = "Top stocks to buy tomorrow #shorts"
        return meta

    monkeypatch.setattr(main, "build_metadata", _unsafe_metadata)
    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("must not publish when content QA fails"))
    main.run(_Args(upload=True))

    after = _canonical_snapshot(_db(tmp_path))
    assert after == before, "the final publication scan must not rewrite canonical history"
    assert after["reports"][0]["content_safety_json"] == before["reports"][0]["content_safety_json"]
    assert _sha256(_report_json(tmp_path)) == json_before

    with MarketHistory(_db(tmp_path)) as history:
        run = history.get_publication_runs()[0]
        assert run.failure_stage == "CONTENT_QA"
        assert run.content_qa_status == "FAILED"
        assert run.publication_status == "BLOCKED"


# --------------------------------------------------------------------- Test D
def test_upload_result_is_operational_only(offline_pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "publish", lambda out, meta, d: "vid123")
    main.run(_Args())                                     # local run, nothing published
    before = _canonical_snapshot(_db(tmp_path))
    json_before = _sha256(_report_json(tmp_path))

    main.run(_Args(upload=True))                          # same session, now published

    assert _canonical_snapshot(_db(tmp_path)) == before, \
        "a YouTube response is not market intelligence"
    assert _sha256(_report_json(tmp_path)) == json_before

    with MarketHistory(_db(tmp_path)) as history:
        runs = history.get_publication_runs()
        published = next(r for r in runs if r.publication_status == "PUBLISHED")
        assert published.youtube_video_id == "vid123"


# --------------------------------------------------------------------- Test E
def test_rerun_is_idempotent_without_replacement(offline_pipeline, tmp_path):
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))

    main.run(_Args())
    after = _canonical_snapshot(_db(tmp_path))

    assert after == before, "a rerun must not delete and reinsert canonical rows"
    assert len(after["reports"]) == 1
    with MarketHistory(_db(tmp_path)) as history:
        assert len(history.get_publication_runs()) == 2, "runs are operational and append"


def test_rerun_does_not_change_row_creation_timestamps(offline_pipeline, tmp_path):
    """The sharpest check that no delete/reinsert happened: created_at would be refreshed."""
    main.run(_Args())
    with MarketHistory(_db(tmp_path)) as history:
        created_before = history.get_report("20260921_PRE_MARKET").created_at

    main.run(_Args())
    with MarketHistory(_db(tmp_path)) as history:
        assert history.get_report("20260921_PRE_MARKET").created_at == created_before


# --------------------------------------------------------------------- Test F
def test_report_json_is_not_rewritten_after_persistence(offline_pipeline, tmp_path,
                                                        monkeypatch):
    """Capture the artifact the instant it is persisted, then let the rest of the run finish."""
    captured = {}
    original = main.persist_report

    def _capture(report, artifact_path, demo=False, history=None):
        outcome = original(report, artifact_path, demo=demo, history=history)
        captured["path"] = artifact_path
        captured["sha"] = _sha256(artifact_path)
        captured["bytes"] = open(artifact_path, "rb").read()
        return outcome

    monkeypatch.setattr(main, "persist_report", _capture)
    main.run(_Args())

    assert captured, "persistence should have run"
    assert _sha256(captured["path"]) == captured["sha"]
    assert open(captured["path"], "rb").read() == captured["bytes"]


def test_canonical_json_holds_no_final_scan_verdict(offline_pipeline, tmp_path):
    """The final publication scan is operational, so it never enters the canonical report."""
    main.run(_Args())
    payload = json.loads(open(_report_json(tmp_path), encoding="utf-8").read())
    safety = payload["content_safety"]
    assert safety["stage"] == "PRE_REPORT_SANITISATION"
    assert "final_scan" not in safety


# --------------------------------------------------------------------- Test G
def test_failed_run_preserves_json_and_writes_qa_artifact(offline_pipeline, monkeypatch,
                                                          tmp_path):
    main.run(_Args())
    json_before = _sha256(_report_json(tmp_path))

    monkeypatch.setattr(main, "check_video", _failing_video_qa)
    main.run(_Args(upload=True))

    assert os.path.exists(_report_json(tmp_path))
    assert _sha256(_report_json(tmp_path)) == json_before
    qa_files = os.listdir(tmp_path / "qa")
    assert qa_files, "the QA artifact is written even when QA fails"

    payload = json.loads((tmp_path / "qa" / qa_files[0]).read_text(encoding="utf-8"))
    assert payload["overall_status"] == "FAIL"
    assert payload["blocking_issues"]
    assert payload["report_json_artifact"], "the QA artifact points back at the report"


def test_qa_artifact_carries_the_final_content_scan(offline_pipeline, tmp_path):
    """Stage B lives here rather than in the report."""
    main.run(_Args())
    qa_files = os.listdir(tmp_path / "qa")
    payload = json.loads((tmp_path / "qa" / qa_files[0]).read_text(encoding="utf-8"))

    assert payload["final_content_qa"]["stage"] == "FINAL_PUBLICATION_SCAN"
    assert payload["final_content_qa"]["passed"] is True
    assert payload["content_safety"]["stage"] == "PRE_REPORT_SANITISATION"


# --------------------------------------------------------------------- cross-run immutability
def test_rerun_leaves_canonical_json_bytes_untouched(offline_pipeline, tmp_path):
    """Immutability holds across runs, not only within one: a second execution of the same
    report must not regenerate the artifact SQLite points at."""
    main.run(_Args())
    path = _report_json(tmp_path)
    sha_before, bytes_before = _sha256(path), open(path, "rb").read()

    main.run(_Args())

    assert _sha256(path) == sha_before
    assert open(path, "rb").read() == bytes_before
    assert len(os.listdir(tmp_path / "reports")) == 1, "no second canonical artifact appears"


def test_rerun_leaves_created_at_and_canonical_rows_untouched(offline_pipeline, tmp_path):
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))
    with MarketHistory(_db(tmp_path)) as history:
        created_before = history.get_report("20260921_PRE_MARKET").created_at

    main.run(_Args())

    after = _canonical_snapshot(_db(tmp_path))
    assert after == before
    assert after["facts"] == before["facts"]
    assert after["observations"] == before["observations"]
    with MarketHistory(_db(tmp_path)) as history:
        assert history.get_report("20260921_PRE_MARKET").created_at == created_before


def test_rerun_appends_a_second_publication_run(offline_pipeline, tmp_path):
    main.run(_Args())
    main.run(_Args())
    with MarketHistory(_db(tmp_path)) as history:
        runs = history.get_publication_runs()
        assert len(runs) == 2
        assert all(r.report_id == "20260921_PRE_MARKET" for r in runs)
        assert all(r.publication_status == "NOT_ATTEMPTED" for r in runs)


def test_rerun_renders_from_the_persisted_report_not_fresh_provider_data(
        offline_pipeline, monkeypatch, tmp_path, market_dict):
    """The sharpest form of the guarantee: if the providers now disagree with the stored
    report, the rerun renders the stored one. Otherwise the video and the canonical record
    of the same session could tell different stories."""
    main.run(_Args())
    original_vix = market_dict["vix"]

    # A field the legacy Nifty cross-check does not police, so the divergence survives
    # collection and would reach the report if the rerun regenerated one.
    moved = dict(market_dict, vix=99.9)
    monkeypatch.setattr(main.market, "get_market", lambda: dict(moved))
    main.run(_Args())

    nifty_scene = next(s for s in offline_pipeline["scenes"] if type(s).__name__ == "NiftyScene")
    assert nifty_scene.m["vix"] == original_vix, \
        "the rerun must render the canonical report, not newly fetched data"
    assert nifty_scene.m["vix"] != 99.9

    report = MarketReport.from_json(open(_report_json(tmp_path), encoding="utf-8").read())
    assert report.nifty["india_vix"] == original_vix


# --------------------------------------------------------------------- unusable canonical record
def test_missing_canonical_artifact_fails_safely(offline_pipeline, monkeypatch, tmp_path):
    """History says the report exists but the file is gone: stop, rather than silently
    recreating canonical history that may not match what was originally published."""
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))
    os.remove(_report_json(tmp_path))

    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("must not publish with a missing artifact"))
    assert main.run(_Args(upload=True)) is None

    assert _canonical_snapshot(_db(tmp_path)) == before, "canonical rows must not be rewritten"
    assert os.listdir(tmp_path / "reports") == [], \
        "the artifact must not be silently recreated"
    with MarketHistory(_db(tmp_path)) as history:
        run = history.get_publication_runs()[0]
        assert run.failure_stage == "CANONICAL_ARTIFACT"
        assert run.publication_status == "BLOCKED"


def test_corrupt_canonical_artifact_fails_safely(offline_pipeline, monkeypatch, tmp_path):
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))
    path = _report_json(tmp_path)
    open(path, "w", encoding="utf-8").write("{ this is not valid json")

    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("must not publish with a corrupt artifact"))
    assert main.run(_Args(upload=True)) is None

    assert _canonical_snapshot(_db(tmp_path)) == before
    assert open(path, encoding="utf-8").read() == "{ this is not valid json", \
        "the corrupt file is left exactly as found, for diagnosis"
    with MarketHistory(_db(tmp_path)) as history:
        assert history.get_publication_runs()[0].failure_stage == "CANONICAL_ARTIFACT"


def test_report_id_mismatch_in_artifact_fails_safely(offline_pipeline, monkeypatch, tmp_path):
    """The file exists and parses, but describes a different report - the index and the
    artifact have diverged, which is exactly when guessing is most dangerous."""
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))
    path = _report_json(tmp_path)
    payload = json.loads(open(path, encoding="utf-8").read())
    payload["report_id"] = "20990101_PRE_MARKET"
    open(path, "w", encoding="utf-8").write(json.dumps(payload))

    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("must not publish on a report_id mismatch"))
    assert main.run(_Args(upload=True)) is None

    assert _canonical_snapshot(_db(tmp_path)) == before
    with MarketHistory(_db(tmp_path)) as history:
        run = history.get_publication_runs()[0]
        assert run.failure_stage == "CANONICAL_ARTIFACT"
        assert "report_id" in (run.failure_reason or "")


def test_unreadable_canonical_artifact_fails_safely(offline_pipeline, monkeypatch, tmp_path):
    main.run(_Args())
    before = _canonical_snapshot(_db(tmp_path))

    real_open = open

    def _deny(path, *a, **k):
        if str(path).endswith(".json") and "reports" in str(path):
            raise PermissionError("artifact is not readable")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _deny)
    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("must not publish with an unreadable artifact"))
    assert main.run(_Args(upload=True)) is None
    monkeypatch.undo()

    assert _canonical_snapshot(_db(tmp_path)) == before


# --------------------------------------------------------------------- administrative path
def test_administrative_replacement_still_works_when_invoked_deliberately(tmp_path, market_dict):
    """The recovery tool remains available - it is simply never part of orchestration."""
    report = build_test_report(market_dict)
    with MarketHistory(str(tmp_path / "admin.db")) as history:
        assert history.save_report(report, artifact_path="a.json") is True
        assert history.save_report(report, artifact_path="a.json") is False
        assert history.save_report(report, artifact_path="b.json", replace=True) is True

        assert history.conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
        assert history.get_report(report.report_id).json_artifact_path == "b.json"


def test_replace_is_documented_as_administrative():
    """A future reader must not mistake it for an ordinary write."""
    doc = MarketHistory.save_report.__doc__
    assert "ADMINISTRATIVE" in doc
    assert "No production code path calls it" in doc

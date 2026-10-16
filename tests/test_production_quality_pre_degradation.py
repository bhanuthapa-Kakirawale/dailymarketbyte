"""production_quality.pre_degradation: deterministic grouping/counting of real PK-C PRE
readiness snapshots, read-only (zero network, zero write, zero render), plus the one
MARKET_EVENTS-missing cross-reference against REPORT-job run records.
"""
from __future__ import annotations

import ast
import builtins
import glob
import json
import os

from production_quality import pre_degradation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "production_quality")

BANNED_MODULES = {
    "upload", "video", "daily_video", "ffmpeg", "imageio_ffmpeg", "private_desk",
    "radar", "market_regime", "market_structure", "editorial", "hooks",
    "institutional_flows", "market_events", "news", "market", "yfinance",
    "requests", "urllib", "googleapiclient", "readiness",
}


def _write_readiness(out_dir, edition_date, overall_status, checks, blocking=None,
                     warnings=None, source_session=None, cutoff=None):
    d = os.path.join(out_dir, "readiness")
    os.makedirs(d, exist_ok=True)
    payload = {
        "schema": "dmb.readiness/1", "edition": "PRE", "overall_status": overall_status,
        "blocking_reasons": blocking or [], "warnings": warnings or [],
        "source_session": source_session, "cutoff": cutoff, "checks": checks,
    }
    with open(os.path.join(d, f"readiness_PRE_{edition_date}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _check(check_id, status, source_health=None, observed=None):
    return {"check_id": check_id, "status": status, "source_health": source_health,
           "observed": observed}


# --------------------------------------------------------------------------- security/safety

def test_no_module_imports_a_banned_module():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not mods & BANNED_MODULES, (path, mods & BANNED_MODULES)


def test_no_module_contains_a_write_mode_open_or_os_replace():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "os.replace" not in src
        assert '"w"' not in src and "'w'" not in src
        assert '"wb"' not in src and "'wb'" not in src


def test_analyze_never_writes_even_with_a_write_guarded_open(tmp_path, monkeypatch):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-16", "READY", [_check("SESSION", "PASS")])
    real_open = builtins.open

    def guarded_open(path, mode="r", *args, **kwargs):
        if "w" in mode or "a" in mode or "x" in mode or "+" in mode:
            raise AssertionError(f"production_quality attempted to open {path!r} for writing")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(pre_degradation, "open", guarded_open, raising=False)
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert report.runs_analyzed == 1


# --------------------------------------------------------------------------- grouping/counting

def test_counts_statuses_and_groups_causes_deterministically(tmp_path):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-14", "READY", [_check("SESSION", "PASS")])
    _write_readiness(out_dir, "2026-10-13", "DEGRADED", [
        _check("INSTITUTIONAL_FLOW", "WARN", "STALE", {
            "NSE": {"health": "HEALTHY"},
            "CDSL": {"health": "STALE"},
            "NSDL": {"health": "HEALTHY"},
        }),
        _check("MARKET_STRUCTURE", "WARN", "MISSING"),
    ])
    _write_readiness(out_dir, "2026-10-12", "BLOCKED", [
        _check("CANONICAL_REPORT", "FAIL", None, "PREVIOUS_SESSION_MISSING"),
        # collateral SKIP from the same root cause - must NOT be counted as its own cause
        _check("EDITORIAL_PLAN", "SKIP"),
    ])

    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert report.runs_analyzed == 3
    assert report.sessions == ["2026-10-14", "2026-10-13", "2026-10-12"]
    assert report.status_counts == {"READY": 1, "DEGRADED": 1, "BLOCKED": 1}
    assert report.cause_counts["INSTITUTIONAL_FLOW.CDSL:STALE"] == 1
    assert "INSTITUTIONAL_FLOW.NSE:STALE" not in report.cause_counts
    assert report.cause_counts["MARKET_STRUCTURE:MISSING"] == 1
    assert report.cause_counts["CANONICAL_REPORT:PREVIOUS_SESSION_MISSING"] == 1
    assert "EDITORIAL_PLAN:SKIP" not in report.cause_counts
    assert report.cause_dates["INSTITUTIONAL_FLOW.CDSL:STALE"] == ["2026-10-13"]


def test_recurring_cause_across_sessions_accumulates_dates(tmp_path):
    out_dir = str(tmp_path)
    for d in ("2026-10-10", "2026-10-09", "2026-10-08"):
        _write_readiness(out_dir, d, "DEGRADED", [
            _check("INSTITUTIONAL_FLOW", "WARN", "STALE", {"CDSL": {"health": "STALE"}}),
        ])
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert report.cause_counts["INSTITUTIONAL_FLOW.CDSL:STALE"] == 3
    assert report.cause_dates["INSTITUTIONAL_FLOW.CDSL:STALE"] == \
        ["2026-10-10", "2026-10-09", "2026-10-08"]


def test_runs_limit_keeps_only_the_newest_n_sessions(tmp_path):
    out_dir = str(tmp_path)
    for d in ("2026-10-10", "2026-10-09", "2026-10-08", "2026-10-07", "2026-10-06"):
        _write_readiness(out_dir, d, "READY", [_check("SESSION", "PASS")])
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=2)
    assert report.runs_analyzed == 2
    assert report.sessions == ["2026-10-10", "2026-10-09"]


def test_fewer_than_requested_runs_available_uses_all_of_them(tmp_path):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-10", "READY", [_check("SESSION", "PASS")])
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert report.runs_analyzed == 1


def test_unrelated_single_occurrence_warning_is_counted_but_not_conflated(tmp_path):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-11", "DEGRADED", [
        _check("GLOBAL_CUES", "WARN", "STALE"),
    ])
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert report.cause_counts == {"GLOBAL_CUES:STALE": 1}


# --------------------------------------------------------------------------- MARKET_EVENTS race

def _write_report_job_run(out_dir, session, filename, run_status, started_at, completed_at,
                          has_market_events):
    d = os.path.join(out_dir, "report_jobs", session)
    os.makedirs(d, exist_ok=True)
    payload = {"run_status": run_status, "started_at": started_at, "completed_at": completed_at,
              "details": ({"market_events": {"market_events_status": "CAPTURED"}}
                          if has_market_events else {})}
    with open(os.path.join(d, filename), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def test_market_events_missing_cross_references_report_job_runs(tmp_path):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-14", "DEGRADED", [
        _check("MARKET_EVENTS.EARNINGS", "WARN", "MISSING"),
    ], source_session="2026-10-13", cutoff="2026-10-14T07:03:20+05:30")
    _write_report_job_run(out_dir, "2026-10-13", "report_job_run_1.json", "FAILED",
                          "2026-10-13T13:45:03+00:00", None, False)
    _write_report_job_run(out_dir, "2026-10-13", "report_job_run_2.json", "DEGRADED",
                          "2026-10-14T01:33:35+00:00", "2026-10-14T01:36:39+00:00", True)

    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert len(report.market_events_missing_sessions) == 1
    entry = report.market_events_missing_sessions[0]
    assert entry["pre_session"] == "2026-10-14"
    assert entry["source_session"] == "2026-10-13"
    runs = entry["report_job_runs"]
    assert len(runs) == 2
    assert runs[0]["run_status"] == "FAILED" and runs[0]["has_market_events"] is False
    assert runs[1]["run_status"] == "DEGRADED" and runs[1]["has_market_events"] is True


def test_market_events_source_failure_is_not_treated_as_the_missing_race(tmp_path):
    """A genuine fetch failure (SOURCE_FAILURE) is a different cause from MISSING (no record
    at all) and must not trigger the REPORT-job cross-reference."""
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-14", "DEGRADED", [
        _check("MARKET_EVENTS.EARNINGS", "WARN", "SOURCE_FAILURE"),
    ], source_session="2026-10-13")
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    assert report.market_events_missing_sessions == []
    assert report.cause_counts == {"MARKET_EVENTS.EARNINGS:SOURCE_FAILURE": 1}


def test_render_human_includes_counts_and_cross_reference(tmp_path):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-14", "DEGRADED", [
        _check("MARKET_EVENTS.EARNINGS", "WARN", "MISSING"),
    ], source_session="2026-10-13")
    _write_report_job_run(out_dir, "2026-10-13", "report_job_run_1.json", "FAILED",
                          "2026-10-13T13:45:03+00:00", None, False)
    report = pre_degradation.analyze_pre_degradation(out_dir, runs=20)
    text = pre_degradation.render_human(report)
    assert "PRE DEGRADATION AUDIT" in text
    assert "DEGRADED" in text
    assert "MARKET_EVENTS.EARNINGS:MISSING" in text
    assert "run_status=FAILED" in text


# --------------------------------------------------------------------------- CLI

def test_cli_pre_degradation_json_output(tmp_path, capsys):
    out_dir = str(tmp_path)
    _write_readiness(out_dir, "2026-10-14", "READY", [_check("SESSION", "PASS")])
    from production_quality.__main__ import main
    code = main(["pre-degradation", "--out-dir", out_dir, "--runs", "5", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["runs_analyzed"] == 1
    assert payload["status_counts"] == {"READY": 1}

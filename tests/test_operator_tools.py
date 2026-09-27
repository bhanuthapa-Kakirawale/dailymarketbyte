"""Operator tooling for the local manual trial: the Windows scripts, the read-only daily check,
and the development-artifact cleanup (dry run by default, protected paths never touched).
Fully offline."""
import glob
import os
import re
import sqlite3

import pytest

import main
from operations import daily_check
from operations.dev_cleanup import (PROTECTED_OUTPUT, execute, plan, protected_reason,
                                    referenced_artifacts)
from test_pipeline import _Args, offline_pipeline  # noqa: F401  (fixture re-export)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _commands(path):
    """The lines a shell actually executes (comments and echo lines removed)."""
    out = []
    for ln in open(path, encoding="utf-8").read().splitlines():
        s = ln.strip()
        if not s or s.lower().startswith(("rem", "::", "#", "echo", "@echo")):
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- scripts
SCRIPTS = sorted(glob.glob(os.path.join(ROOT, "scripts", "*.bat"))) + [
    os.path.join(ROOT, "run_daily.bat"), os.path.join(ROOT, "run_daily.sh")]


@pytest.mark.parametrize("path", SCRIPTS, ids=os.path.basename)
def test_no_default_script_uploads(path):
    for cmd in _commands(path):
        assert "--upload" not in cmd, (path, cmd)
        assert "upload.py" not in cmd, (path, cmd)


def test_scripts_call_the_canonical_entry_points_and_fail_on_error():
    expected = {"run_evening.bat": ("python main.py --mode report", "daily_check evening"),
                "run_morning_pre.bat": ("python main.py --mode premarket --shadow",
                                        "daily_check pre"),
                "run_morning_post.bat": ("python main.py", "daily_check post"),
                "check_daily_run.bat": ("python -m operations.daily_check all",),
                "check_setup.bat": ("python -m operations.daily_check setup",)}
    for name, needles in expected.items():
        cmds = _commands(os.path.join(ROOT, "scripts", name))
        for n in needles:
            assert any(c == n or c.startswith(n + " ") or n in c for c in cmds), (name, n)
        text = open(os.path.join(ROOT, "scripts", name), encoding="utf-8").read()
        assert 'call "%~dp0_env.bat" || exit /b 1' in text
        if name.startswith("run_"):
            assert "exit /b %RC%" in text, name                  # the job's failure is the exit
    post = _commands(os.path.join(ROOT, "scripts", "run_morning_post.bat"))
    assert "python main.py" in post                              # exactly - no flags appended
    raw = open(os.path.join(ROOT, "scripts", "run_morning_post.bat"), "rb").read()
    assert b"\r\n" in raw, "batch files are CRLF"


def test_scripts_do_not_print_secrets():
    for path in SCRIPTS:
        text = open(path, encoding="utf-8").read()
        assert not re.search(r"echo[^\n]*%(GEMINI|YT_|GOOGLE|DMB_GCS)", text, re.I), path
        assert "type .env" not in text.lower()


# --------------------------------------------------------------------------- daily check
def test_audit_verdict_separates_rights_block_from_content_failure():
    assert daily_check.audit_verdict("BLOCK", ["publication_rights"]) == "RIGHTS_BLOCK_ONLY"
    assert daily_check.audit_verdict("BLOCK", ["publication_rights", "language"]) == "CONTENT_FAILURE"
    assert daily_check.audit_verdict("BLOCK", ["displayed_claims"]) == "CONTENT_FAILURE"
    assert daily_check.audit_verdict("PASS", []) == "PASS"


def test_daily_check_on_real_offline_runs(offline_pipeline, tmp_path, capsys):
    from test_production_schedule import _report_job
    _report_job()
    assert daily_check.check_evening(str(tmp_path)) == daily_check.OK
    out = capsys.readouterr().out
    assert "session                      2026-09-18" in out and "VERDICT: ACCEPT" in out
    main.run(_Args())
    assert daily_check.check_post(str(tmp_path)) == daily_check.OK
    out = capsys.readouterr().out
    assert "RIGHTS_BLOCK_ONLY" in out and "rights BLOCK is expected" in out
    assert "NOT_ATTEMPTED" in out                                 # nothing was uploaded
    assert daily_check.check_pre(str(tmp_path)) == daily_check.NOTHING


def test_daily_check_stops_on_a_content_failure(tmp_path, capsys):
    run = tmp_path / "post" / "2026-09-21"
    run.mkdir(parents=True)
    import json
    (run / "production_manifest.json").write_text(json.dumps({
        "session_date": "2026-09-18", "product": "POST_UNIFIED", "scenes": [],
        "qa": {"video_qa": "PASS", "frames_qa": {"passed": True}, "content_qa": "SAFE"},
        "publication_audit": {"final": "BLOCK",
                              "failed_checks": ["publication_rights", "displayed_claims"]}}))
    assert daily_check.check_post(str(tmp_path)) == daily_check.STOP
    assert "CONTENT_FAILURE" in capsys.readouterr().out


def test_setup_check_never_prints_secret_values(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "sekret-value-12345")
    daily_check.check_setup(str(tmp_path))
    out = capsys.readouterr().out
    assert "sekret-value-12345" not in out and "set (value hidden)" in out


# --------------------------------------------------------------------------- cleanup
def _tree(root):
    files = {
        "main.py": "x", "tests/fixtures/sample.json": "{}", "docs/X.md": "#", ".env": "K=V",
        "token.json": "{}", "data/official_events.json": "{}",
        "adapters/__pycache__/m.cpython-313.pyc": "c", ".pytest_cache/v/cache": "c",
        "venv/Lib/__pycache__/x.pyc": "c",
        "output/data/market_history.db": "",
        "output/reports/premarket_2026-09-23.json": "{}",
        "output/radar/daily_radar_2026-09-21.json": "{}",
        "output/radar_phase2/story.mp4": "m", "output/radar_validation/report.md": "#",
        "output/official_snapshots/2026-09-25/fno_ban_snapshot.json": "{}",
        "output/market_structure/market_structure_2026-09-24.json": "{}",
        "output/post/2026-09-26/freeze_frames/00_hook.png": "p",
        "output/pre_shadow/2026-09-28/full_pre_2026-09-28.mp4": "m",
        "output/public_intelligence_v1/real/post.mp4": "m",
        "output/hook_previews/a/hook.mp4": "m", "output/hook_previews/a/hook_plan.json": "{}",
        "output/hook_previews/a/old.db": "",
        "output/pre_phase1/x/contact.png": "p",
        "output/bg_music_0.wav": "w", "output/nifty_chart_2026-09-23_0.png": "p",
        "output/daily_byte_2026-09-23_DEMO.mp4": "m", "output/daily_byte_2026-09-25.mp4": "m",
        "output/render.log": "l", "output/frames/f.png": "p",
    }
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = sqlite3.connect(str(root / "output" / "data" / "market_history.db"))
    db.execute("create table publication_runs (artifact_path text, mode text)")
    db.execute("create table reports (json_artifact_path text)")
    db.execute("insert into publication_runs values (?, 'POST_MARKET')",
               (str(root / "output" / "daily_byte_2026-09-25.mp4"),))
    db.execute("insert into publication_runs values (?, 'DEMO')",
               (str(root / "output" / "daily_byte_2026-09-23_DEMO.mp4"),))
    db.commit()
    db.close()
    return root


def _rels(root, cands):
    return {os.path.relpath(c.path, root).replace(os.sep, "/") for c in cands}


def test_cleanup_plans_only_disposable_artifacts(tmp_path):
    root = _tree(tmp_path / "proj")
    cands, _ = plan(str(root))
    assert _rels(root, cands) == {
        "adapters/__pycache__", ".pytest_cache", "output/hook_previews/a/hook.mp4",
        "output/pre_phase1/x/contact.png", "output/bg_music_0.wav",
        "output/nifty_chart_2026-09-23_0.png", "output/daily_byte_2026-09-23_DEMO.mp4",
        "output/frames/f.png"}


@pytest.mark.parametrize("rel", [
    ".env", "token.json", "main.py", "tests/fixtures/sample.json", "docs/X.md",
    "data/official_events.json", "venv/Lib/__pycache__/x.pyc", "output/data/market_history.db",
    "output/reports/premarket_2026-09-23.json", "output/radar/daily_radar_2026-09-21.json",
    "output/radar_phase2/story.mp4", "output/radar_validation/report.md",
    "output/official_snapshots/2026-09-25/fno_ban_snapshot.json",
    "output/market_structure/market_structure_2026-09-24.json",
    "output/post/2026-09-26/freeze_frames/00_hook.png",
    "output/pre_shadow/2026-09-28/full_pre_2026-09-28.mp4",
    "output/public_intelligence_v1/real/post.mp4", "output/hook_previews/a/old.db",
    "output/hook_previews/a/hook_plan.json", "output/daily_byte_2026-09-25.mp4",
    "output/render.log"])
def test_protected_paths_are_never_candidates(tmp_path, rel):
    root = _tree(tmp_path / "proj")
    cands, _ = plan(str(root))
    assert rel not in _rels(root, cands)
    refs = referenced_artifacts(str(root))
    if not rel.endswith(("hook_plan.json", "render.log")):      # kept by rule, not by deny-list
        assert protected_reason(str(root), str(root / rel), refs) is not None, rel


def test_cleanup_is_a_dry_run_until_execute(tmp_path):
    root = _tree(tmp_path / "proj")
    before = sorted(p for p in glob.glob(str(root / "**" / "*"), recursive=True))
    cands, _ = plan(str(root))
    assert sorted(glob.glob(str(root / "**" / "*"), recursive=True)) == before
    res = execute(str(root), cands)
    assert res["removed"] == len(cands) and res["freed_bytes"] > 0
    assert (root / ".env").exists() and (root / "output" / "data" / "market_history.db").exists()
    assert (root / "output" / "hook_previews" / "a" / "hook_plan.json").exists()
    assert not (root / "output" / "hook_previews" / "a" / "hook.mp4").exists()


def test_cleanup_never_follows_a_symlink_outside_the_project(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.mp4").write_text("m")
    root = _tree(tmp_path / "proj")
    link = root / "output" / "hook_previews" / "linked"
    try:
        os.symlink(str(outside), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra privileges on this Windows account")
    cands, _ = plan(str(root))
    execute(str(root), cands)
    assert (outside / "precious.mp4").exists()
    assert protected_reason(str(root), str(link)) is not None


def test_real_repository_plan_touches_no_protected_state():
    """Read-only check on THIS checkout: every planned deletion is media or a cache."""
    cands, _ = plan(ROOT)
    for c in cands:
        rel = os.path.relpath(c.path, ROOT).replace(os.sep, "/")
        assert protected_reason(ROOT, c.path, referenced_artifacts(ROOT)) is None
        assert not rel.endswith((".db", ".py", ".json", ".md", ".env")), rel
        if rel.startswith("output/"):
            sub = rel[len("output/"):]
            assert not any(sub == p or sub.startswith(p + "/") for p in PROTECTED_OUTPUT), rel
            assert not sub.startswith("radar"), rel

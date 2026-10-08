"""Isolated local test runs (docs/TESTING_GUIDE.md): the test context can never write production
state, open a production database, use remote state or upload; test runs live only under
output/test_runs/; their cleanup can touch nothing else; the normal daily scripts are unchanged.
"""
import datetime as dt
import glob
import hashlib
import os
import subprocess
import sys

import pytest

import config
from operations import run_context, test_run
from operations.run_context import ProductionPathError, UploadDisabledError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD = os.path.join(ROOT, "output")
TESTS = os.path.join(PROD, "test_runs")


@pytest.fixture
def test_context(monkeypatch):
    monkeypatch.setenv("DMB_RUN_CONTEXT", "test")


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _child(code: str, **env):
    e = dict(os.environ, PYTHONIOENCODING="utf-8", **env)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True,
                          text=True, timeout=300)


# --------------------------------------------------------------------------- the guards
def test_writes_into_production_are_refused_in_a_test_run(test_context):
    for p in (os.path.join(PROD, "data", "market_history.db"),
              os.path.join(PROD, "reports", "premarket_2026-09-28.json"),
              os.path.join(PROD, "radar", "daily_radar_2026-09-25.json"),
              os.path.join(PROD, "market_structure", "x.json"),
              os.path.join(PROD, "official_snapshots", "2026-09-25"),
              os.path.join(PROD, "institutional_flows", "NSE", "x.json"),
              os.path.join(PROD, "private_radar", "x.png"),
              os.path.join(TESTS, "..", "data", "market_history.db")):        # traversal
        with pytest.raises(ProductionPathError):
            run_context.guard_write(p, "probe")
    run_context.guard_write(os.path.join(TESTS, "run1", "data", "market_history.db"))
    run_context.guard_write(os.path.join(ROOT, "..", "elsewhere", "x"))       # not production


def test_guards_are_no_ops_outside_a_test_run(monkeypatch):
    monkeypatch.delenv("DMB_RUN_CONTEXT", raising=False)
    run_context.guard_write(os.path.join(PROD, "data", "market_history.db"))
    run_context.guard_upload()


def test_production_databases_cannot_be_opened_in_a_test_run(test_context):
    from storage import MarketHistory
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.editorial_repository import EditorialStore
    from storage.ohlcv_repository import OHLCVStore
    probe = os.path.join(PROD, "data", "_isolation_probe.db")
    for cls in (MarketHistory, CandidateHistoryStore, EditorialStore, OHLCVStore):
        with pytest.raises(ProductionPathError):
            cls(probe)
    assert not os.path.exists(probe), "refused before the file was created"


def test_config_refuses_a_test_run_whose_output_root_is_production():
    bad = _child("import config", DMB_RUN_CONTEXT="test", DAILY_BYTE_OUT=PROD)
    assert bad.returncode != 0 and "TEST CONTEXT" in bad.stderr
    nested = _child("import config", DMB_RUN_CONTEXT="test",
                    DAILY_BYTE_OUT=os.path.join(TESTS, "a", "b"))
    assert nested.returncode != 0
    unknown = _child("import config", DMB_RUN_CONTEXT="staging")
    assert unknown.returncode != 0
    ok = _child("import config; print(config.OUT_DIR)", DMB_RUN_CONTEXT="test",
                DAILY_BYTE_OUT=os.path.join(TESTS, "probe_run"))
    assert ok.returncode == 0, ok.stderr


def test_remote_or_production_state_is_refused_in_a_test_run():
    from state import StateStoreError, from_env
    with pytest.raises(StateStoreError):
        from_env("x", {"DMB_RUN_CONTEXT": "test", "DMB_STATE_BACKEND": "gcs",
                       "DMB_GCS_BUCKET": "b"})
    with pytest.raises(StateStoreError):
        from_env("x", {"DMB_RUN_CONTEXT": "test", "DMB_STATE_DIR": os.path.join(PROD, "data")})


# --------------------------------------------------------------------------- upload hard block
def test_upload_is_hard_disabled_in_a_test_run(test_context, monkeypatch):
    import upload
    monkeypatch.setattr(upload, "get_service",
                        lambda: pytest.fail("no YouTube client may be created in a test run"))
    with pytest.raises(UploadDisabledError, match="UPLOAD_DISABLED_TEST_CONTEXT"):
        upload.upload("x.mp4", {}, {"final": "PASS"})


def test_main_refuses_upload_in_a_test_run_before_doing_anything():
    run = os.path.join(TESTS, "upload_probe")
    res = subprocess.run([sys.executable, "main.py", "--upload"], cwd=ROOT,
                         env=dict(os.environ, DMB_RUN_CONTEXT="test", DAILY_BYTE_OUT=run,
                                  PYTHONIOENCODING="utf-8"),
                         capture_output=True, text=True, timeout=300)
    assert res.returncode == 2 and "UPLOAD_DISABLED_TEST_CONTEXT" in res.stdout
    assert not os.path.exists(run), "nothing ran - not even the output folder was created"


def test_test_runner_refuses_upload(capsys):
    assert test_run.main(["full", "--upload"]) == 2
    assert "UPLOAD_DISABLED_TEST_CONTEXT" in capsys.readouterr().out


# --------------------------------------------------------------------------- steps
def test_steps_run_the_canonical_entry_points_never_upload():
    s = dt.date(2026, 9, 24)
    for step in ("REPORT", "PRE", "POST"):
        for mode in ("live", "replay", "fixture"):
            cmd = test_run.step_command(step, mode, s)
            assert "--upload" not in cmd
    assert test_run.step_command("REPORT", "replay", s) == [
        "--mode", "report", "--session-date", "2026-09-24", "--skip-radar"]
    assert test_run.step_command("PRE", "replay", s) == [
        "--mode", "premarket", "--shadow", "--session-date", "2026-09-25"]
    assert test_run.step_command("POST", "replay", s) == ["--session-date", "2026-09-24"]
    assert test_run.step_command("POST", "fixture", s) == ["--demo"]
    assert test_run.step_command("REPORT", "live", s) == ["--mode", "report"]


def test_pre_without_test_state_fails_clearly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "TEST_RUNS_DIR", str(tmp_path / "test_runs"))
    assert test_run.main(["pre"]) == 2
    out = capsys.readouterr().out
    assert "TEST STATE MISSING" in out and "never falls back to production" in out


def test_a_fixture_report_run_leaves_production_untouched(monkeypatch):
    """A real test-context subprocess (the canonical REPORT job, synthetic data): everything
    lands in the run folder; production databases are byte-identical afterwards."""
    dbs = sorted(glob.glob(os.path.join(PROD, "data", "*.db")))
    before = {p: (_sha(p), os.path.getmtime(p)) for p in dbs}
    run_dir, m = test_run.new_run("fixture", run_id="pytest_isolation_probe")
    try:
        res = test_run.run_step(run_dir, "REPORT", "fixture", None)
        assert res["exit_code"] == 0 and res["status"] == "SUCCESS", res
        assert res["production_state_modified"] is False
        assert os.path.exists(os.path.join(run_dir, "data", "market_history.db"))
        assert glob.glob(os.path.join(run_dir, "reports", "*.json"))
        after = {p: (_sha(p), os.path.getmtime(p)) for p in dbs}
        assert after == before, "production databases changed"
    finally:
        test_run.execute_clean(test_run.plan_clean("pytest_isolation_probe"))
    assert not os.path.exists(run_dir)


# --------------------------------------------------------------------------- WAL sidecars
def _wal_db(path):
    import sqlite3
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=wal")
    con.execute("create table reports (session_date text, is_demo int, report_type text)")
    con.execute("insert into reports values ('2026-09-24', 0, 'PRE_MARKET')")
    con.commit()
    con.close()                         # the last writer checkpoints and removes the sidecars
    assert sorted(os.listdir(path.parent)) == [path.name]


def test_reading_a_wal_database_creates_no_file_next_to_it(tmp_path):
    """Found by the isolation proof: in WAL mode even a mode=ro connection creates -wal/-shm
    next to the database. Production must be read without leaving anything behind."""
    from storage.readonly import connect_readonly, copy_database
    db = tmp_path / "prod" / "market_history.db"
    db.parent.mkdir()
    _wal_db(db)
    con = connect_readonly(str(db))
    assert con.execute("select count(*) from reports").fetchone()[0] == 1
    con.close()
    copy_database(str(db), str(tmp_path / "run" / "market_history.db"))
    assert sorted(os.listdir(db.parent)) == ["market_history.db"]


def test_seeding_a_run_leaves_production_byte_identical(monkeypatch, tmp_path):
    prod = tmp_path / "output"
    (prod / "data").mkdir(parents=True)
    _wal_db(prod / "data" / "market_history.db")
    (prod / "reports").mkdir()
    (prod / "reports" / "premarket_2026-09-25.json").write_text('{"session_date": "2026-09-24"}')
    monkeypatch.setattr(config, "PRODUCTION_OUT_DIR", str(prod))
    monkeypatch.setattr(config, "TEST_RUNS_DIR", str(prod / "test_runs"))
    before = test_run.fingerprint()
    run_dir, m = test_run.new_run("replay", run_id="seed_probe")
    assert m["seed_proof"]["production_state_modified"] is False, m["seed_proof"]
    assert test_run.compare(before, test_run.fingerprint())["production_state_modified"] is False
    assert os.path.exists(os.path.join(run_dir, "data", "market_history.db"))
    assert test_run._latest_canonical_session(run_dir) == dt.date(2026, 9, 24)


# --------------------------------------------------------------------------- cleanup
@pytest.fixture
def runs(monkeypatch, tmp_path):
    root = tmp_path / "output" / "test_runs"
    for rid in ("r1", "r2", "old"):
        (root / rid / "data").mkdir(parents=True)
        (root / rid / "data" / "h.db").write_bytes(b"x" * 10)
        (root / rid / "manifest.json").write_text("{}")
    prod = tmp_path / "output" / "data"
    prod.mkdir(parents=True)
    (prod / "market_history.db").write_bytes(b"prod")
    old = root / "old"
    past = dt.datetime.now().timestamp() - 30 * 86400
    os.utime(old, (past, past))
    monkeypatch.setattr(config, "TEST_RUNS_DIR", str(root))
    monkeypatch.setattr(config, "PRODUCTION_OUT_DIR", str(tmp_path / "output"))
    return root, prod


def test_cleanup_dry_run_deletes_nothing(runs):
    root, prod = runs
    plan = test_run.plan_clean()
    assert sorted(os.path.basename(p) for p, _, _ in plan) == ["old", "r1", "r2"]
    assert all(files == 2 for _, files, _ in plan)
    assert sorted(os.listdir(root)) == ["old", "r1", "r2"]


def test_cleanup_single_run_all_runs_and_age_filter(runs):
    root, prod = runs
    test_run.execute_clean(test_run.plan_clean("r1"))
    assert sorted(os.listdir(root)) == ["old", "r2"]
    assert [os.path.basename(p) for p, _, _ in test_run.plan_clean(older_than_days=7)] == ["old"]
    test_run.execute_clean(test_run.plan_clean())
    assert os.listdir(root) == []
    assert (prod / "market_history.db").read_bytes() == b"prod", "production untouched"


@pytest.mark.parametrize("bad", ["..", "../data", "..\\data", "r1/../../data", "/etc", "C:\\x", ""])
def test_cleanup_rejects_traversal_and_arbitrary_paths(runs, bad):
    with pytest.raises(ValueError):
        test_run.plan_clean(bad)


def test_cleanup_never_follows_a_symlink(runs, tmp_path):
    root, prod = runs
    try:
        os.symlink(str(prod), str(root / "linked"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra privileges on this Windows account")
    plan = test_run.plan_clean()
    assert "linked" not in [os.path.basename(p) for p, _, _ in plan]
    test_run.execute_clean(plan)
    assert (prod / "market_history.db").exists()
    with pytest.raises(ValueError):
        test_run.plan_clean("linked")


def test_clean_script_is_a_dry_run_by_default(runs, capsys):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import clean_test_artifacts
    root, _ = runs
    assert clean_test_artifacts.main([]) == 0
    assert "Nothing deleted" in capsys.readouterr().out
    assert sorted(os.listdir(root)) == ["old", "r1", "r2"]
    assert clean_test_artifacts.main(["--run-id", "../data"]) == 2


# --------------------------------------------------------------------------- scripts
TEST_SCRIPTS = ["test_evening.bat", "test_pre.bat", "test_post.bat", "test_full_cycle.bat",
                "check_test_run.bat", "clean_test_artifacts.bat"]


@pytest.mark.parametrize("name", TEST_SCRIPTS)
def test_test_scripts_call_the_python_entry_point_only(name):
    text = open(os.path.join(ROOT, "scripts", name), encoding="utf-8").read()
    cmds = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().lower().startswith(("rem", "@echo"))]
    runs_python = [c for c in cmds if c.startswith("python ")]
    assert len(runs_python) == 1, cmds
    assert "operations.test_run" in runs_python[0] or "clean_test_artifacts.py" in runs_python[0]
    assert "--upload" not in text


@pytest.mark.parametrize("name", ["run_evening.bat", "run_morning_pre.bat",
                                  "run_morning_post.bat", "check_daily_run.bat"])
def test_normal_daily_scripts_are_unchanged(name):
    """The normal flow is byte-for-byte the committed operator scripts (e741289)."""
    committed = subprocess.run(["git", "show", f"e741289:scripts/{name}"], cwd=ROOT,
                               capture_output=True, timeout=60)
    if committed.returncode != 0:
        pytest.skip("git history not available")
    norm = lambda b: b.replace(b"\r\n", b"\n")                         # noqa: E731
    current = norm(open(os.path.join(ROOT, "scripts", name), "rb").read())
    if name == "run_morning_pre.bat":
        # PK-C (owner-requested) inserted ONE block: the production readiness gate before the
        # render (docs/PRODUCTION_READINESS.md). Everything else stays byte-for-byte e741289.
        start = current.index(b"REM Production readiness gate")
        end = current.index(b"echo.\npython main.py --mode premarket --shadow")
        current = current[:start] + current[end + len(b"echo.\n"):]
    assert current == norm(committed.stdout)


# --------------------------------------------------------------------------- claims fix
def test_numbered_briefing_ordinals_are_declared_list_positions():
    from publication.claims import check_claims, hook_claims
    sheet = type("S", (), {"facts": [], "beats": [], "fact": lambda self, i: None})()
    texts = {"hero_strings.0": "NIFTY 50", "hero_strings.2": "1", "hero_strings.5": "2",
             "hero_strings.8": "3"}
    claims = hook_claims("00_dynamic_hook", sheet, lambda f: ((), (), None), texts)
    assert not check_claims({"00_dynamic_hook": texts}, claims)
    # anything that is not exactly 1..n still needs a fact
    bad = {"hero_strings.2": "1", "hero_strings.5": "3"}
    assert check_claims({"x": bad}, hook_claims("x", sheet, lambda f: ((), (), None), bad))

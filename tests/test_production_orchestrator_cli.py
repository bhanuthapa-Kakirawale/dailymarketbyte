"""production_orchestrator.__main__: argv parsing, exit-code mapping, --json output, status
dispatch - using injected run_pre_fn/run_post_fn stubs so no real orchestration runs."""
from __future__ import annotations

import json

from production_orchestrator import __main__ as CLI
from production_orchestrator.models import BLOCKED, SUCCESS, RunManifest


def _manifest(status=SUCCESS, edition="POST"):
    m = RunManifest(edition=edition, command=edition.lower(), run_id="r1",
                    session_date="2026-10-08")
    m.finalize(status)
    return m


def test_post_success_exit_code_zero(capsys):
    rc = CLI.main(["post"], run_post_fn=lambda **kw: _manifest(SUCCESS))
    assert rc == 0
    assert "SUCCESS" in capsys.readouterr().out


def test_post_blocked_exit_code_twenty(capsys):
    rc = CLI.main(["post"], run_post_fn=lambda **kw: _manifest(BLOCKED))
    assert rc == 20


def test_json_flag_prints_manifest_dict_only(capsys):
    rc = CLI.main(["post", "--json"], run_post_fn=lambda **kw: _manifest(SUCCESS))
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["orchestrator_status"] == SUCCESS
    assert rc == 0


def test_pre_dispatches_to_run_pre_fn_not_run_post_fn():
    calls = {"pre": 0, "post": 0}

    def pre_fn(**kw):
        calls["pre"] += 1
        return _manifest(SUCCESS, edition="PRE")

    def post_fn(**kw):
        calls["post"] += 1
        return _manifest(SUCCESS)

    CLI.main(["pre"], run_pre_fn=pre_fn, run_post_fn=post_fn)
    assert calls == {"pre": 1, "post": 0}


def test_cli_passes_session_date_and_intent_through(tmp_path):
    seen = {}

    def post_fn(**kw):
        seen.update(kw)
        return _manifest(SUCCESS)

    CLI.main(["post", "--session-date", "2026-10-08", "--intent", "publish", "--force"],
            run_post_fn=post_fn)
    assert str(seen["session_date"]) == "2026-10-08"
    assert seen["intent"] == "PUBLISH"
    assert seen["force"] is True


def test_exception_from_runner_maps_to_exit_error(capsys):
    def boom(**kw):
        raise RuntimeError("kaboom")
    rc = CLI.main(["post"], run_post_fn=boom)
    assert rc == 30
    assert "kaboom" in capsys.readouterr().out


def test_status_command_never_calls_run_pre_or_run_post_fn(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))
    calls = {"n": 0}

    def should_not_run(**kw):
        calls["n"] += 1
        return _manifest(SUCCESS)

    rc = CLI.main(["status"], run_pre_fn=should_not_run, run_post_fn=should_not_run)
    assert calls["n"] == 0
    assert rc == 0


def test_status_json(tmp_path, monkeypatch, capsys):
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))
    rc = CLI.main(["status", "--json"])
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d == {"PRE": None, "POST": None}
    assert rc == 0

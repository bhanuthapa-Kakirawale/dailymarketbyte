"""python -m shadow_scheduler {run|status}: thin argparse wiring and exit-code propagation."""
from __future__ import annotations

from shadow_scheduler import __main__ as cli
from shadow_scheduler.models import BROKEN, HEALTHY


def test_build_parser_restricts_run_to_the_four_known_jobs():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "post"])
    assert args.command == "run"
    assert args.job == "post"


def test_build_parser_rejects_an_unknown_job():
    import pytest
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "not_a_job"])


def test_main_run_delegates_to_runner_and_propagates_exit_code(monkeypatch):
    calls = []

    def fake_run_job(job, *, cli_argv=None):
        calls.append((job, cli_argv))
        return 20

    monkeypatch.setattr(cli.runner, "run_job", fake_run_job)
    exit_code = cli.main(["run", "pre"])
    assert exit_code == 20
    assert calls[0][0] == "pre"


def test_main_status_json_exits_zero_when_healthy(monkeypatch, capsys):
    monkeypatch.setattr(cli.status, "overall_status",
                        lambda: {"jobs": {}, "overall": HEALTHY, "generated_at": "x"})
    exit_code = cli.main(["status", "--json"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"overall": "HEALTHY"' in out


def test_main_status_exits_nonzero_when_broken(monkeypatch):
    monkeypatch.setattr(cli.status, "overall_status",
                        lambda: {"jobs": {}, "overall": BROKEN, "generated_at": "x"})
    exit_code = cli.main(["status", "--json"])
    assert exit_code == 1

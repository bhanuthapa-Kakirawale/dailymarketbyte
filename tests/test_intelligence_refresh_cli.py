"""intelligence_refresh.__main__: CLI flags map to the documented effect, and exit codes match
the orchestrator_status -> {0, 20, 30} contract."""
from __future__ import annotations

import datetime as dt

from intelligence_refresh import models
from intelligence_refresh.__main__ import build_parser, main


def test_refresh_subcommand_parses_all_flags():
    args = build_parser().parse_args(["refresh", "--days", "7", "--from", "2026-09-01",
                                      "--to", "2026-09-30", "--resume", "--dry-run",
                                      "--no-network", "--json"])
    assert args.cmd == "refresh"
    assert args.days == 7
    assert args.from_date == dt.date(2026, 9, 1)
    assert args.to_date == dt.date(2026, 9, 30)
    assert args.resume and args.dry_run and args.no_network and args.json


def test_status_subcommand_parses():
    args = build_parser().parse_args(["status", "--json"])
    assert args.cmd == "status"
    assert args.json


def test_main_refresh_exit_code_reflects_orchestrator_status(tmp_path, monkeypatch, capsys):
    man = models.RunManifest(run_id="r1", orchestrator_status=models.BLOCKED)
    monkeypatch.setattr("intelligence_refresh.__main__.run_refresh", lambda **k: man)
    code = main(["refresh"])
    assert code == 20


def test_main_status_exit_code_reflects_run_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("intelligence_refresh.__main__.run_status",
                        lambda **k: (0, "VERDICT: OK"))
    code = main(["status"])
    assert code == 0

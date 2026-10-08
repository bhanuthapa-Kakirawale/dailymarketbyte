"""The single evening command (scripts\\run_evening_full.bat -> operations.evening_full):
REPORT before POST, a fatal REPORT stops the POST, optional degradations and the shadow-trial
rights block do not, one REPORT + one POST per invocation, the POST describes the session the
REPORT built, never an upload, normal output tree only, weekend/rerun safety. Fully offline.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

import main
from conftest import IST
from operations import evening_full as EF
from test_pipeline import _Args, offline_pipeline  # noqa: F401  (fixture re-export)
from test_production_schedule import EVENING, _no_live_acquisition, _report_job  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRI = "2026-09-18"
SATURDAY_EVENING = dt.datetime(2026, 9, 19, 19, 30, tzinfo=IST)


# --------------------------------------------------------------------------- stub artifacts
def _record(out, session=FRI, status="SUCCESS", n=1, **details):
    folder = os.path.join(out, "report_jobs", session or "unresolved")
    os.makedirs(folder, exist_ok=True)
    det = {"report_id": f"rep-{session}", "report_source": "BUILT",
           "radar": {"status": "BUILT", "pipeline_status": "OK"},
           "market_structure_snapshot": "ms.json", "exchange_snapshot_status": "SUCCESS",
           "fno_status": "SUCCESS", "asm_status": "SUCCESS", "gsm_status": "SUCCESS",
           "ipo_snapshot_status": "NO_DATA", "degradations": []}
    det.update(details)
    rec = {"run_id": n, "run_status": status, "target_session": session, "details": det}
    if status not in EF.ACCEPTABLE_REPORT:
        rec.update(failure_stage="DATA_QA", failure_reason="Nifty close disagrees")
    with open(os.path.join(folder, f"report_job_{n}.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


def _manifest(out, tag=FRI, session=FRI, report_id=None, source="REUSED_CANONICAL",
              failed=("publication_rights",), content="SAFE"):
    folder = os.path.join(out, "post", tag)
    os.makedirs(folder, exist_ok=True)
    m = {"session_date": session, "report_id": report_id or f"rep-{session}",
         "report_source": source, "publication_profile": "PUBLIC_UNREGISTERED",
         "duration": 52.4, "video": os.path.join(out, f"daily_byte_{tag}.mp4"),
         "qa": {"video_qa": "PASS", "frames_qa": {"passed": True}, "content_qa": content},
         "publication_audit": {"final": "BLOCK" if failed else "PASS",
                               "failed_checks": list(failed)},
         "optional_sections": {"exchange_watch": {"code": "CHANGE_UNKNOWN"}},
         "upload": "NOT_ATTEMPTED (no --upload); an upload would be refused: publication_rights"}
    with open(os.path.join(folder, "production_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(m, fh)


class Runner:
    """Records every canonical command; writes the artifacts the real command would."""

    def __init__(self, out, report=None, post=None, rc=None):
        self.out, self.calls = out, []
        self.report = report if report is not None else (lambda o, n: _record(o, n=n))
        self.post = post if post is not None else (lambda o: _manifest(o))
        self.rc = rc or {}

    def __call__(self, args):
        self.calls.append(list(args))
        if args == EF.REPORT_ARGS:
            self.report(self.out, len(self.calls))
            return self.rc.get("REPORT", 0)
        if args == EF.POST_ARGS:
            self.post(self.out)
            return self.rc.get("POST", 0)
        raise AssertionError(f"unexpected command {args}")


def _ready_gate(session, as_of, out_dir, calendar=None):
    """The PK-C POST readiness gate, stubbed READY (its own behaviour: tests/test_readiness.py)."""
    from readiness import ReadinessResult
    return ReadinessResult(edition="POST", as_of=as_of)


def _run(tmp_path, runner, clock=EVENING, **kw):
    kw.setdefault("readiness_fn", _ready_gate)
    return EF.run_evening_full(clock=lambda: clock, runner=runner, out_dir=str(tmp_path), **kw)


# --------------------------------------------------------------------------- sequencing / gates
def test_report_runs_before_post_and_a_clean_evening_passes(tmp_path, capsys):
    r = Runner(str(tmp_path))
    res = _run(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]                      # once each, in order
    assert res["fatal"] is None and EF.verdict(res) == EF.PASS
    EF.print_summary(res)
    out = capsys.readouterr().out
    assert "VERDICT: PASS FOR SHADOW REVIEW" in out and "UPLOAD: NOT ATTEMPTED" in out
    for row in ("REPORT", "PRIVATE RADAR", "MARKET STRUCTURE", "EXCHANGE WATCH", "IPO WATCH",
                "POST_UNIFIED", "VIDEO QA", "CONTENT AUDIT", "PUBLICATION RIGHTS"):
        assert row in out
    assert "duration             52.4s" in out and "daily_byte_2026-09-18.mp4" in out


@pytest.mark.parametrize("report,rc", [
    (lambda o, n: _record(o, status="BLOCKED", n=n), 0),                 # data QA failed
    (lambda o, n: _record(o, status="FAILED", n=n), 0),
    (lambda o, n: None, 0),                                              # no record written
    (lambda o, n: _record(o, n=n), 1),                                   # crashed
    (lambda o, n: _record(o, session="2026-09-17", n=n), 0),             # wrong session
    (lambda o, n: _record(o, session=None, status="BLOCKED", n=n), 0),   # unresolved session
])
def test_a_fatal_report_stops_before_post(tmp_path, capsys, report, rc):
    r = Runner(str(tmp_path), report=report, rc={"REPORT": rc})
    res = _run(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS]                                   # POST never started
    assert res["fatal"] and EF.verdict(res) == EF.ATTENTION
    assert res["rows"]["POST_UNIFIED"][0] == "NOT RUN"
    assert not os.path.exists(tmp_path / "post")
    EF.print_summary(res)
    assert "VERDICT: ATTENTION REQUIRED" in capsys.readouterr().out


def test_an_old_record_is_not_mistaken_for_this_runs_report(tmp_path):
    _record(str(tmp_path), n=99)                                         # yesterday's success
    r = Runner(str(tmp_path), report=lambda o, n: None)                  # this run wrote nothing
    res = _run(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS] and "no run record" in res["fatal"]


def test_optional_degradations_still_render_the_post(tmp_path):
    def degraded(o, n):
        _record(o, status="DEGRADED", n=n, radar={"status": "FAILED", "error": "x"},
                market_structure_snapshot=None, exchange_snapshot_status="DEGRADED",
                fno_status="SOURCE_UNAVAILABLE", ipo_snapshot_status="SOURCE_UNAVAILABLE",
                degradations=[{"source": "OFFICIAL_SNAPSHOTS", "status": "DEGRADED"}])
    r = Runner(str(tmp_path), report=degraded)
    res = _run(tmp_path, r)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]
    assert EF.verdict(res) == EF.PASS
    for row in ("PRIVATE RADAR", "MARKET STRUCTURE", "EXCHANGE WATCH", "IPO WATCH"):
        assert res["rows"][row][0] == "DEGRADED"
    assert "exchange_watch: CHANGE_UNKNOWN" in res["notes"]              # first baseline day


def test_rights_block_only_is_not_a_video_failure(tmp_path):
    res = _run(tmp_path, Runner(str(tmp_path)))
    assert res["rows"]["POST_UNIFIED"][0] == "PASS"
    assert res["rows"]["VIDEO QA"][0] == "PASS" and res["rows"]["CONTENT AUDIT"][0] == "PASS"
    assert res["rows"]["PUBLICATION RIGHTS"][0] == "BLOCKED"
    assert "rights review only" in res["rows"]["PUBLICATION RIGHTS"][1]
    assert EF.verdict(res) == EF.PASS


@pytest.mark.parametrize("post", [
    lambda o: _manifest(o, failed=("publication_rights", "displayed_claims")),   # content
    lambda o: _manifest(o, content="BLOCKED"),                                   # final scan
    lambda o: _manifest(o, session="2026-09-17"),                                # other session
    lambda o: _manifest(o, report_id="rep-other"),                               # other report
    lambda o: _manifest(o, source="BUILT_INLINE"),                               # second build
    lambda o: _manifest(o, tag="replay_2026-09-18"),                             # not this run
    lambda o: None,                                                              # nothing rendered
])
def test_a_post_problem_needs_attention(tmp_path, post):
    res = _run(tmp_path, Runner(str(tmp_path), post=post))
    assert res["fatal"] and EF.verdict(res) == EF.ATTENTION


def test_a_post_crash_needs_attention(tmp_path):
    res = _run(tmp_path, Runner(str(tmp_path), rc={"POST": 1}))
    assert "exited with code 1" in res["fatal"]


# --------------------------------------------------------------------------- rerun / weekend
def test_rerun_does_not_render_a_second_post(tmp_path):
    first = Runner(str(tmp_path))
    _run(tmp_path, first)
    again = Runner(str(tmp_path), report=lambda o, n: _record(
        o, n=n + 10, report_source="ALREADY_BUILT", radar={"status": "ALREADY_BUILT"}))
    res = _run(tmp_path, again)
    assert again.calls == [EF.REPORT_ARGS]                               # POST not re-rendered
    assert "ALREADY RENDERED" in res["rows"]["POST_UNIFIED"][1]
    assert EF.verdict(res) == EF.PASS
    forced = Runner(str(tmp_path), report=lambda o, n: _record(o, n=n + 20))
    _run(tmp_path, forced, rerender_post=True)
    assert forced.calls == [EF.REPORT_ARGS, EF.POST_ARGS]


def test_weekend_uses_the_last_completed_session_and_says_so(tmp_path):
    _manifest(str(tmp_path))                                             # Friday evening's POST
    r = Runner(str(tmp_path), report=lambda o, n: _record(o, n=n + 5,
                                                          report_source="ALREADY_BUILT"))
    res = _run(tmp_path, r, clock=SATURDAY_EVENING)
    assert res["context"]["today_is_session"] is False
    assert res["context"]["session"] == FRI
    assert "NOT an NSE trading session" in res["context"]["note"]
    assert r.calls == [EF.REPORT_ARGS]                                   # no second Friday POST
    assert EF.verdict(res) == EF.PASS


def test_weekend_catch_up_when_the_session_has_no_post(tmp_path):
    r = Runner(str(tmp_path), post=lambda o: _manifest(o, tag="2026-09-19"))
    res = _run(tmp_path, r, clock=SATURDAY_EVENING)
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS]
    assert res["session"] == FRI and EF.verdict(res) == EF.PASS


def test_before_the_close_is_final_the_previous_session_is_named():
    ctx = EF.session_context(dt.datetime(2026, 9, 18, 15, 0, tzinfo=IST))
    assert ctx["session"] == "2026-09-17" and "not final yet" in ctx["note"]


# --------------------------------------------------------------------------- safety
def test_never_uploads(monkeypatch, capsys):
    assert "--upload" not in EF.REPORT_ARGS + EF.POST_ARGS
    monkeypatch.setattr(EF, "run_evening_full", lambda **k: pytest.fail("ran"))
    assert EF.main(["--upload"]) == EF.STOP
    assert "never uploads" in capsys.readouterr().out
    src = open(EF.__file__, encoding="utf-8").read()
    assert "import upload" not in src and "upload.upload" not in src


def test_refused_inside_a_test_run(monkeypatch, capsys):
    monkeypatch.setenv("DMB_RUN_CONTEXT", "test")
    monkeypatch.setattr(EF, "run_evening_full", lambda **k: pytest.fail("ran"))
    assert EF.main([]) == EF.STOP
    assert "test_full_cycle" in capsys.readouterr().out


def test_runs_the_canonical_commands_in_the_normal_output_tree(monkeypatch):
    import config
    seen = {}

    def call(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return 0
    monkeypatch.setattr(EF.subprocess, "call", call)
    assert EF.run_main(EF.REPORT_ARGS) == 0
    assert seen["cmd"][1:] == ["main.py", "--mode", "report"]
    assert seen["kw"] == {"cwd": config.BASE_DIR}                        # env inherited as-is
    src = open(EF.__file__, encoding="utf-8").read()
    assert "DAILY_BYTE_OUT" not in src and "DMB_RUN_CONTEXT" not in src


# --------------------------------------------------------------------------- end to end
def test_end_to_end_offline_evening(offline_pipeline, tmp_path, monkeypatch):
    """The real REPORT job and the real POST (network stubbed): one report, one Radar run,
    the POST reuses that report for the same session, rights block only -> PASS."""
    from test_production_schedule import _radar_stub, _reports
    monkeypatch.setattr(main, "now_ist", lambda: EVENING)
    radar_calls = []

    def runner(args):
        if args == EF.REPORT_ARGS:
            _report_job(radar_fn=_radar_stub(radar_calls))
        elif args == EF.POST_ARGS:
            a = _Args()
            a.no_fetch_public = True
            main.run(a)
        return 0

    res = _run(tmp_path, runner)
    assert res["fatal"] is None, res
    assert EF.verdict(res) == EF.PASS
    assert radar_calls == [dt.date(2026, 9, 18)]
    assert len(_reports(tmp_path)) == 1
    m = json.load(open(res["post_manifest"], encoding="utf-8"))
    assert m["session_date"] == FRI and m["report_source"] == "REUSED_CANONICAL"
    assert res["rows"]["PUBLICATION RIGHTS"][0] == "BLOCKED"
    assert m["upload"].startswith("NOT_ATTEMPTED")


# --------------------------------------------------------------------------- scripts
def _script(name):
    return open(os.path.join(ROOT, "scripts", name), encoding="utf-8").read()


def test_evening_full_script_runs_the_orchestrator_only():
    text = _script("run_evening_full.bat")
    cmds = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().lower().startswith(("rem", "@echo"))]
    assert [c for c in cmds if c.startswith("python ")] == ["python -m operations.evening_full %*"]
    assert 'call "%~dp0_env.bat" || exit /b 1' in text and "--upload" not in text
    assert b"\r\n" in open(os.path.join(ROOT, "scripts", "run_evening_full.bat"), "rb").read()


def test_the_orchestrator_runs_the_same_commands_as_the_low_level_scripts():
    assert "python main.py --mode report" in _script("run_evening.bat")
    assert "python main.py " + " ".join(EF.REPORT_ARGS) == "python main.py --mode report"
    post = _script("run_post.bat")
    cmds = [ln.strip() for ln in post.splitlines() if not ln.strip().lower().startswith("rem")]
    assert "python main.py %*" in cmds and "python -m operations.daily_check post" in cmds
    assert not any("--upload" in c for c in cmds)
    assert "exit /b %RC%" in post
    assert EF.POST_ARGS == ["--no-fetch-public"]                          # reads persisted state


@pytest.mark.parametrize("name", ["run_evening.bat", "run_morning_pre.bat",
                                  "run_morning_post.bat", "run_post.bat",
                                  "run_evening_full.bat", "check_daily_run.bat",
                                  "test_evening.bat", "test_pre.bat", "test_post.bat",
                                  "test_full_cycle.bat"])
def test_every_operator_script_exists(name):
    assert os.path.exists(os.path.join(ROOT, "scripts", name))

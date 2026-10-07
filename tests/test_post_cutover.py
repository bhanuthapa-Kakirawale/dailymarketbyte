"""Production POST cut-over: the scheduled POST renders POST_UNIFIED.

The scheduled job (.github/workflows/daily_byte.yml -> `python main.py [--upload]` ->
products.route -> main.run) and the review renderer (render_daily_market_byte.py) build the
Short through the same products.post_unified functions. The legacy video.py Short is kept but
nothing in production reaches it. Fully offline (`offline_pipeline` stubs every network
boundary and the MP4 encode; planner, storyboard, gates and audit run for real).
"""
import ast
import datetime as dt
import glob
import json
import os
import subprocess
import sys

import pytest

import main
from products import MODES
from products import post_unified as PU
from products import public_fixtures as PF
from test_pipeline import _Args, offline_pipeline  # noqa: F401  (fixture re-export)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRIDAY, THURSDAY, MONDAY = dt.date(2026, 9, 18), dt.date(2026, 9, 17), dt.date(2026, 9, 21)
EXPECTED_24_SEP = ["DYNAMIC_HOOK", "NIFTY", "SECTORS", "FLOWS", "STRUCTURE", "STRUCTURE",
                   "CLOSING"]


def _run_source() -> str:
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    return ast.get_source_segment(src, fn)


def _manifest(tmp_path) -> dict:
    paths = glob.glob(str(tmp_path / "post" / "*" / "production_manifest.json"))
    assert len(paths) == 1, paths
    return json.load(open(paths[0], encoding="utf-8"))


def _audit(tmp_path) -> dict:
    paths = glob.glob(str(tmp_path / "publication" / "*" / "publication_audit.json"))
    assert len(paths) == 1, paths
    return json.load(open(paths[0], encoding="utf-8"))


def _inject_intel(monkeypatch, **kw):
    import presentation.public_intelligence as pi
    intel = PF.intelligence(FRIDAY, THURSDAY, MONDAY, ipo_day=FRIDAY, **kw)
    monkeypatch.setattr(pi, "load_public_intelligence", lambda *a, **k: intel)
    return intel


@pytest.fixture
def no_legacy(monkeypatch):
    """Any call into the legacy video.py renderer fails the test."""
    def _boom(*a, **k):
        raise AssertionError("the legacy video.py renderer must not be reached in production")
    monkeypatch.setattr(main.video, "render", _boom)
    monkeypatch.setattr(main.video, "scenes_from_plan", _boom)


# 1 ------------------------------------------------------------------------------------------
def test_scheduled_post_routes_to_post_unified(offline_pipeline, no_legacy, tmp_path):
    wf = open(os.path.join(ROOT, ".github", "workflows", "daily_byte.yml"), encoding="utf-8").read()
    cmds = [ln.strip() for ln in wf.splitlines() if "python main.py" in ln]
    assert cmds == ["if [ -f token.json ]; then python main.py --upload; "
                    "else python main.py --force; fi"], cmds
    assert "--mode" not in wf and "legacy" not in wf.lower()
    run_src = _run_source()
    assert "PU.build_post_storyboard(" in run_src and "PU.render_post(" in run_src
    assert "video.render(" not in run_src and "scenes_from_plan(" not in run_src
    assert main.run(_Args())
    m = _manifest(tmp_path)
    assert m["product"] == "POST_UNIFIED" == PU.PRODUCT
    assert m["renderer"] == "daily_video.composer.Composer"
    assert m["legacy_renderer_invoked"] is False
    assert m["entry_point"] == "main.py -> products.route -> main.run"


# 2 ------------------------------------------------------------------------------------------
def test_manual_and_scheduled_post_share_one_planner(offline_pipeline, monkeypatch, tmp_path):
    import render_daily_market_byte as review
    calls = {"plan": [], "storyboard": []}
    real_plan, real_sb = PU.plan_for_post, PU.build_post_storyboard

    def _plan(*a, **k):
        calls["plan"].append(k.get("profile"))
        return real_plan(*a, **k)

    def _sb(*a, **k):
        sb, pres = real_sb(*a, **k)
        calls["storyboard"].append(sb)
        return sb, pres

    monkeypatch.setattr(PU, "plan_for_post", _plan)
    monkeypatch.setattr(PU, "build_post_storyboard", _sb)
    assert main.run(_Args())                                    # scheduled path
    report = glob.glob(str(tmp_path / "reports" / "*.json"))[0]
    monkeypatch.setattr(review, "OUT_DIR", str(tmp_path))
    assert review.main(["--report", report, "--radar-dir", str(tmp_path / "radar"),
                        "--out-dir", str(tmp_path / "review"), "--frames-only",
                        "--no-hook-ai"]) == 0                   # manual / review path
    assert len(calls["plan"]) == 2 and len(calls["storyboard"]) == 2
    assert calls["plan"][0] == calls["plan"][1]                 # same publication profile
    sched, manual = calls["storyboard"]
    assert [s.kind for s in sched.scenes] == [s.kind for s in manual.scenes]
    assert sched.publication_profile == manual.publication_profile == "PUBLIC_UNREGISTERED"


# 3 ------------------------------------------------------------------------------------------
def test_market_structure_is_supported(offline_pipeline, monkeypatch, tmp_path):
    _inject_intel(monkeypatch, structure_scenario="CONCENTRATED")
    assert main.run(_Args())
    m = _manifest(tmp_path)
    assert "STRUCTURE" in m["scenes"]
    assert m["optional_sections"]["MARKET_STRUCTURE"]["code"] == "RENDERED"


# 4 ------------------------------------------------------------------------------------------
def test_exchange_watch_is_supported(offline_pipeline, monkeypatch, tmp_path):
    _inject_intel(monkeypatch, exchange_scenario="FNO_BAN")
    assert main.run(_Args())
    m = _manifest(tmp_path)
    assert "EXCHANGE_WATCH" in m["scenes"]
    assert m["optional_sections"]["EXCHANGE_WATCH"]["code"] == "RENDERED"


# 5 ------------------------------------------------------------------------------------------
def test_ipo_watch_is_supported(offline_pipeline, monkeypatch, tmp_path):
    _inject_intel(monkeypatch, ipo_scenario="CLOSES_TODAY")
    assert main.run(_Args())
    m = _manifest(tmp_path)
    assert any(k in m["scenes"] for k in ("IPO_WATCH", "IPO_BOARD"))
    assert m["optional_sections"]["IPO_WATCH"]["code"] == "RENDERED"


# 6 ------------------------------------------------------------------------------------------
def test_optional_sections_stay_optional_with_reason_codes(offline_pipeline, tmp_path):
    # conftest: every official list is UNAVAILABLE; the offline report has no structure snapshot
    assert main.run(_Args())
    m = _manifest(tmp_path)
    opt = m["optional_sections"]
    assert not {"STRUCTURE", "EXCHANGE_WATCH", "IPO_WATCH", "IPO_BOARD"} & set(m["scenes"])
    assert opt["EXCHANGE_WATCH"]["code"] == "SOURCE_UNAVAILABLE"
    assert opt["IPO_WATCH"]["code"] == "SOURCE_UNAVAILABLE"
    # nothing persisted a Market Structure snapshot for this session - never "source unavailable"
    assert opt["MARKET_STRUCTURE"]["code"] == "SNAPSHOT_NOT_CAPTURED"
    assert m["scenes"][0] in ("DYNAMIC_HOOK", "HOOK") and m["scenes"][-1] == "CLOSING"


# 7 ------------------------------------------------------------------------------------------
def test_publication_audit_runs_on_the_production_render(offline_pipeline, tmp_path):
    assert main.run(_Args())
    audit = _audit(tmp_path)
    assert audit["product"] == "POST_UNIFIED"
    assert audit["publication_profile"] == "PUBLIC_UNREGISTERED"
    assert audit["displayed_claims"], "every displayed number is traced"
    # every content check passes; only the conservative rights decision blocks
    assert audit["failed_checks"] == ["publication_rights"], audit["failed_checks"]
    assert audit["rights_policy"] == {"PUBLIC_REVIEW_REQUIRED_POLICY": "BLOCK"}
    assert audit["artifact"]["sha256"], "the audit is computed over the rendered file"
    m = _manifest(tmp_path)
    assert m["publication_audit"]["final"] == audit["final"]


# 8 ------------------------------------------------------------------------------------------
def test_rights_block_refuses_the_upload(offline_pipeline, monkeypatch, tmp_path):
    monkeypatch.delenv("PUBLIC_REVIEW_REQUIRED_POLICY", raising=False)          # default BLOCK
    monkeypatch.setattr(main, "publish",
                        lambda *a, **k: pytest.fail("rights BLOCK must refuse the upload"))
    assert main.run(_Args(upload=True))
    m = _manifest(tmp_path)
    assert m["rights_policy"] == {"PUBLIC_REVIEW_REQUIRED_POLICY": "BLOCK"}
    assert m["publication_audit"]["final"] == "BLOCK"
    assert m["publication_audit"]["failed_checks"] == ["publication_rights"]
    assert m["upload"].startswith("REFUSED: PUBLICATION_AUDIT")
    assert os.path.exists(m["video"]), "a refused upload keeps the rendered artifact"


# 9 ------------------------------------------------------------------------------------------
def test_post_reuses_the_canonical_report(offline_pipeline, monkeypatch, tmp_path):
    from products.report_job import run_report_job
    run_report_job(FRIDAY)

    def _no_fetch(*a, **k):
        raise AssertionError("POST must render from the stored canonical report")

    monkeypatch.setattr(main.market, "get_market", _no_fetch)
    assert main.run(_Args())
    m = _manifest(tmp_path)
    assert m["report_source"] == "REUSED_CANONICAL" and m["product"] == "POST_UNIFIED"
    assert m["session_date"] == FRIDAY.isoformat()


# 10 -----------------------------------------------------------------------------------------
def test_legacy_renderer_is_not_selectable():
    assert set(MODES) == {"postmarket", "premarket", "report"}
    for flag in ("--legacy", "--post-legacy", "--renderer"):
        res = subprocess.run([sys.executable, "main.py", flag], cwd=ROOT, capture_output=True,
                             text=True, timeout=120)
        assert res.returncode == 2 and "unrecognized arguments" in res.stderr, (flag, res.stderr)
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "LEGACY / NON-PRODUCTION" in src


# 11 -----------------------------------------------------------------------------------------
REAL_24 = os.path.join(ROOT, "output", "reports", "premarket_2026-09-25.json")


@pytest.mark.skipif(not os.path.exists(REAL_24), reason="real 24 Sep canonical report is a "
                    "local artifact (output/ is never committed)")
def test_real_24_sep_scene_sequence_is_unchanged(monkeypatch):
    import render_daily_market_byte as review
    from presentation.public_intelligence import load_public_intelligence
    from publication import resolve_profile
    monkeypatch.delenv("PUBLICATION_PROFILE", raising=False)
    prof = resolve_profile(None)
    report, plan = review.load_report_and_plan(REAL_24, profile=prof)
    intel = load_public_intelligence(dt.date(2026, 9, 24), dt.date(2026, 9, 25),
                                     os.path.join(ROOT, "output"), fetch=False)
    sb, _ = PU.build_post_storyboard(report, plan, profile=prof, intelligence=intel,
                                     radar_dir=os.path.join(ROOT, "output", "radar"))
    assert [s.kind for s in sb.scenes] == EXPECTED_24_SEP
    assert 36.0 <= sb.total_duration <= 38.5


def test_post_unified_sequence_shape_offline(offline_pipeline, monkeypatch, tmp_path):
    """The committed, always-run form of #11: hook -> ... -> closing, structure scenes after
    the market sections, no Radar story and no PULSE after a NIFTY chart repeating the hook."""
    _inject_intel(monkeypatch, structure_scenario="CONCENTRATED")
    assert main.run(_Args())
    kinds = _manifest(tmp_path)["scenes"]
    assert kinds[0] == "DYNAMIC_HOOK" and kinds[-1] == "CLOSING"
    assert "RADAR_STORY" not in kinds and "MOVERS" not in kinds
    assert max(i for i, k in enumerate(kinds) if k in ("SECTORS", "FLOWS", "NIFTY", "PULSE")) \
        < kinds.index("STRUCTURE")


# 12 -----------------------------------------------------------------------------------------
def test_production_post_is_silent(offline_pipeline, tmp_path, monkeypatch):
    from daily_video import composer
    from qa.video_qa import MIN_PLAUSIBLE_BYTES, QAStatus, check_video
    assert composer.AUDIO_ENABLED is False
    main_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "expect_audio=False" in main_src and "video_qa(" in _run_source()
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"x" * (MIN_PLAUSIBLE_BYTES + 1000))
    frames = lambda p, ts: [{"t": t, "decoded": True, "mean": 40.0, "stddev": 45.0} for t in ts]
    probe = {"duration": 37.0, "video": {"codec": "h264", "width": 1080, "height": 1920,
                                         "fps": 30.0}}

    def _audio_check(with_audio):
        res = check_video(str(video), 37.0, probe=lambda p: dict(
            probe, **({"audio": {"codec": "aac", "sample_rate": 44100}} if with_audio else {})),
            sample=frames, expect_audio=False)
        return next(c for c in res.checks if c.name == "audio_stream").status

    assert _audio_check(False) is QAStatus.PASS
    assert _audio_check(True) is QAStatus.FAIL          # audio may not sneak into V2
    assert main.run(_Args())
    assert _audit(tmp_path)["audio"]["audio_phase_enabled"] is False


def test_unreachable_ipo_source_is_source_unavailable_not_no_event(tmp_path):
    """On a GitHub runner NSE often refuses the client; the fetcher then notes "NSE client
    unavailable: ..." - that must read as SOURCE_UNAVAILABLE, never as "no IPO today"."""
    from presentation.public_intelligence import load_public_intelligence
    morning = dt.datetime(2026, 9, 21, 7, 40, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    for i, note in enumerate(("NSE client unavailable: HTTP 403",
                              "upcoming: UNAVAILABLE (Timeout)")):
        out = tmp_path / str(i)
        intel = load_public_intelligence(FRIDAY, MONDAY, str(out), fetch=True, now=morning,
                                         ipo_fn=lambda d, now, _n=note: ([], [_n]))
        assert intel.ipo_snapshot["status"] == "SOURCE_UNAVAILABLE", note
    ok = load_public_intelligence(FRIDAY, MONDAY, str(tmp_path / "ok"), fetch=True, now=morning,
                                  ipo_fn=lambda d, now: ([], []))
    assert ok.ipo_snapshot["status"] == "NO_DATA"            # read fine, nothing listed today

"""PK-C - Production Readiness Gate V1 (readiness/, docs/PRODUCTION_READINESS.md).

Status aggregation, the PRE / POST capability matrices, freshness and point-in-time safety,
the publication / render preflight, the POST_RENDER stage, the CLI contract and the runner
integration. Fully offline and clean-checkout reproducible: every store lives under tmp_path
(conftest isolates config.OUT_DIR), the canonical report comes from the stubbed production
pipeline, overnight cues come from an injected provider, and nothing reaches the network.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

import pytest

from conftest import IST
from readiness import checks as C
from readiness.models import (BLOCKED, DEGRADED, FAIL, HEALTHY, HEALTHY_EMPTY, OPTIONAL, PASS,
                              PUBLISH, READY, REQUIRED, SKIP, UNSUPPORTED, WARN, CheckResult,
                              ReadinessExecutionError, ReadinessResult, health_status, overall)
from test_pipeline import offline_pipeline  # noqa: F401  (fixture re-export)
from test_production_schedule import (CAL, FRIDAY, MONDAY, _no_live_acquisition,  # noqa: F401
                                      _report_job)

PRE_AS_OF = dt.datetime(2026, 9, 21, 7, 45, tzinfo=IST)
POST_AS_OF = dt.datetime(2026, 9, 21, 7, 50, tzinfo=IST)   # the fixture report: 21 Sep 07:40
RECORD_DONE = dt.datetime(2026, 9, 18, 19, 35, tzinfo=IST)
FONTS_OK = {"bold": {"resolved_path": "C:/Windows/Fonts/arialbd.ttf", "fallback_used": True},
            "regular": {"resolved_path": "C:/Windows/Fonts/arial.ttf", "fallback_used": True}}
ENV = {"ffmpeg_resolver": lambda: sys.executable, "ffmpeg_runner": lambda p: True,
       "font_reporter": lambda: FONTS_OK,
       "disk_usage": lambda p: SimpleNamespace(free=50 * 1024 ** 3)}


# --------------------------------------------------------------------------- helpers
_ORIG = {n: getattr(C, n) for n in ("check_institutional", "check_market_events",
                                    "check_official_snapshots", "check_market_structure")}


def _c(status, cid="X", requirement=OPTIONAL):
    return CheckResult(cid, "CAT", "SECTORS", requirement, status, "m")


def _result(*statuses, **kw):
    r = ReadinessResult(edition=kw.pop("edition", "POST"), **kw)
    r.checks = [_c(s, f"C{i}", REQUIRED if s == FAIL else OPTIONAL)
                for i, s in enumerate(statuses)]
    return r


def _pass(edition, cid, cap, cat="CAT"):
    return [C.mk(edition, cid, cat, cap, PASS, "stubbed healthy", source_health=HEALTHY)]


@pytest.fixture
def healthy(monkeypatch):
    """Every OPTIONAL data family healthy (each has its own real-store tests below), so an
    end-to-end evaluation isolates the required path."""
    monkeypatch.setattr(C, "check_institutional",
                        lambda e, *a, **k: _pass(e, "INSTITUTIONAL_FLOW", "INSTITUTIONAL_FLOW"))
    monkeypatch.setattr(C, "check_market_events",
                        lambda e, *a, **k: _pass(e, "MARKET_EVENTS.EARNINGS", "MARKET_EVENTS"))
    monkeypatch.setattr(C, "check_official_snapshots",
                        lambda e, *a, **k: _pass(e, "OFFICIAL_SNAPSHOTS", "OFFICIAL_SNAPSHOTS"))
    monkeypatch.setattr(C, "check_market_structure",
                        lambda e, *a, **k: _pass(e, "MARKET_STRUCTURE", "MARKET_STRUCTURE"))


def _record_path(out, session=FRIDAY):
    folder = os.path.join(out, "report_jobs", session.isoformat())
    return os.path.join(folder, sorted(os.listdir(folder))[-1])


def _pin_record(out, completed=RECORD_DONE, **details):
    """The REPORT record stamps the real wall clock; pin it to the fixture's evening."""
    p = _record_path(out)
    rec = json.load(open(p, encoding="utf-8"))
    rec["completed_at"] = completed.isoformat()
    rec["details"].update(details)
    json.dump(rec, open(p, "w", encoding="utf-8"))
    return rec


@pytest.fixture
def built(offline_pipeline, tmp_path):
    """Friday 18 Sep's canonical report, built by the production REPORT path (stubbed I/O)."""
    _report_job()
    _pin_record(str(tmp_path))
    return str(tmp_path)


def _post(out, session=FRIDAY, as_of=POST_AS_OF, **kw):
    from readiness import evaluate_post
    kw.setdefault("env", ENV)
    return evaluate_post(session, as_of, mode=kw.pop("mode", "REPLAY"), out_dir=out,
                         calendar=CAL, **kw)


def _pre(out, pre_date=MONDAY, as_of=PRE_AS_OF, **kw):
    from readiness import evaluate_pre
    kw.setdefault("env", ENV)
    return evaluate_pre(pre_date, as_of, mode=kw.pop("mode", "REPLAY"), out_dir=out,
                        calendar=CAL, **kw)


def _inst(out, source, key, first, retrieved=None, status="SUCCESS"):
    from institutional_flows.models import (SCHEMA_VERSION, SOURCE_CLASS,
                                            InstitutionalFlowSnapshot, RepresentedPeriod)
    from institutional_flows.store import write_revision
    snap = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=source, source_class=SOURCE_CLASS[source],
        report_type="test", report_key=key, report_date=key, data_as_of=key,
        represented_period=RepresentedPeriod(), retrieved_at=(retrieved or first).isoformat(),
        first_retrieved_at=first.isoformat(), status=status)
    return write_revision(out, snap)


def _event(out, family, key, status, retrieved):
    from market_events.models import SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision
    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=family, event_key=key, symbol="ABC",
                     company="Co", status=status, sub_type=None, data_as_of="2026-09-18",
                     source_name="nse_corp_announcements", source_reference="https://x",
                     retrieved_at=retrieved.isoformat(), first_retrieved_at=retrieved.isoformat(),
                     status_capture=SUCCESS)
    return write_revision(out, ev)


def _structure(out, session, metrics, retrieved):
    folder = os.path.join(out, "market_structure")
    os.makedirs(folder, exist_ok=True)
    art = {"snapshot": {"session_date": session.isoformat(), "universe_label": "NIFTY 200",
                        "constituent_count": 200,
                        "metrics": {k: {"status": s, "coverage_pct": c}
                                    for k, (s, c) in metrics.items()},
                        "universe_source": {"retrieved_at": retrieved.isoformat()}}}
    with open(os.path.join(folder, f"market_structure_{session}.json"), "w") as fh:
        json.dump(art, fh)


CORE_OK = {"ADVANCES": ("PUBLISHABLE", 100.0), "DECLINES": ("PUBLISHABLE", 100.0),
           "NEW_52W_HIGH": ("PARTIAL", 96.0), "NEW_52W_LOW": ("PARTIAL", 96.0)}


def _official(out, session, kinds, retrieved):
    from official_snapshots.store import manifest_path
    p = manifest_path(out, session)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"snapshots": {k: {"status": s, "retrieved_at": retrieved.isoformat()}
                             for k, s in kinds.items()}}, open(p, "w"))


# =========================================================================== 1-8 aggregation
def test_01_all_required_checks_pass_is_ready():
    assert overall([_c(PASS), _c(PASS, requirement=REQUIRED), _c(SKIP)]) == READY
    r = _result(PASS, PASS, SKIP)
    assert (r.overall_status, r.exit_code, r.decision) == (READY, 0, "SAFE TO GENERATE")


def test_02_one_optional_warning_degrades():
    r = _result(PASS, WARN)
    assert (r.overall_status, r.exit_code) == (DEGRADED, 10)
    assert r.decision.startswith("SAFE TO GENERATE WITH DEGRADED") and len(r.warnings) == 1


def test_03_one_blocking_failure_blocks():
    r = _result(PASS, FAIL)
    assert (r.overall_status, r.exit_code) == (BLOCKED, 20)
    assert r.decision.startswith("DO NOT GENERATE") and len(r.blocking_reasons) == 1


def test_04_multiple_warnings_still_degraded():
    assert _result(WARN, WARN, WARN, PASS).overall_status == DEGRADED


def test_05_warn_plus_fail_is_blocked():
    r = _result(WARN, FAIL)
    assert r.overall_status == BLOCKED and r.warnings and r.blocking_reasons


def test_06_healthy_empty_optional_source_is_not_a_failure(tmp_path):
    assert health_status(OPTIONAL, HEALTHY_EMPTY) == PASS
    assert health_status(REQUIRED, HEALTHY_EMPTY) == PASS
    rec = {"completed_at": RECORD_DONE.isoformat(), "details": {"market_events": {
        "market_events_status": "CAPTURED",
        "results": {f: {"status": "SUCCESS", "events": []} for f in
                    ("EARNINGS", "OFS", "BUYBACK", "OPEN_OFFER", "DELISTING",
                     "GOVT_SECURITIES_AUCTION")}}}}
    rows = C.check_market_events("POST", str(tmp_path), FRIDAY, POST_AS_OF, rec)
    assert rows and all(r.status == PASS and r.source_health == HEALTHY_EMPTY for r in rows)


def test_07_unsupported_optional_capability_never_fails(tmp_path):
    assert health_status(OPTIONAL, UNSUPPORTED) == PASS
    _official(str(tmp_path), FRIDAY, {"FNO_BAN": "SUCCESS", "ASM": "NO_DATA",
                                      "ESM": "NOT_SUPPORTED"}, RECORD_DONE)
    r = C.check_official_snapshots("POST", str(tmp_path), FRIDAY, POST_AS_OF)
    assert r.status == PASS and "ESM" in r.observed and "UNSUPPORTED" in r.observed["ESM"]


def test_08_internal_exception_is_a_distinct_execution_error(tmp_path):
    def boom(*a, **k):
        raise KeyError("bug")
    # inside one check: graded, never a crash - required FAILs, optional WARNs
    (req,) = C.guarded("POST", "BENCHMARK", "MARKET_DATA", "BENCHMARK", boom)
    (opt,) = C.guarded("POST", "SECTORS", "MARKET_DATA", "SECTORS", boom)
    assert (req.status, opt.status) == (FAIL, WARN) and "CHECK_ERROR" in req.message
    # the evaluator itself cannot run: an execution error, never a verdict
    from readiness import evaluate_post, evaluate_pre
    with pytest.raises(ReadinessExecutionError):
        evaluate_post(None, None, out_dir=str(tmp_path))
    with pytest.raises(ReadinessExecutionError):
        evaluate_pre(None, None, out_dir=str(tmp_path))


# =========================================================================== 9-18 PRE
def test_09_pre_previous_session_alignment_never_substitutes_an_older_report(built):
    r = _pre(built, dt.date(2026, 9, 22), dt.datetime(2026, 9, 22, 7, 45, tzinfo=IST))
    assert r.source_session == dt.date(2026, 9, 21)
    c = r.check("CANONICAL_REPORT")
    assert c.status == FAIL and "PREVIOUS_SESSION_MISSING" in c.message
    assert r.overall_status == BLOCKED                 # Friday's report is never used for Tue


def test_10_monday_pre_describes_friday(built, healthy):
    r = _pre(built)
    assert r.source_session == FRIDAY and r.session_date == MONDAY
    assert r.check("CANONICAL_REPORT").status == PASS
    assert r.check("BENCHMARK").status == PASS and "2026-09-18" in r.check("BENCHMARK").message


def test_11_holiday_boundary_pre(built):
    holiday = _pre(built, dt.date(2026, 10, 2), dt.datetime(2026, 10, 2, 7, 45, tzinfo=IST))
    assert holiday.overall_status == BLOCKED and "NOT_A_SESSION" in holiday.check("SESSION").message
    # the Monday after the 2 Oct holiday describes Thu 1 Oct - never an older stored session
    after = _pre(built, dt.date(2026, 10, 5), dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST))
    assert after.source_session == dt.date(2026, 10, 1)
    assert after.check("CANONICAL_REPORT").status == FAIL


def _cue_provider(us_last=FRIDAY, asia=True):
    import pandas as pd

    def fn(ticker, start, end, interval="1d"):
        if interval == "5m":
            if not asia:
                return pd.DataFrame()
            idx = pd.date_range("2026-09-21 07:00", "2026-09-21 07:35", freq="5min", tz=IST)
            return pd.DataFrame({"Close": [100.0 + i for i in range(len(idx))]}, index=idx)
        if ticker == "^INDIAVIX":
            return pd.DataFrame({"Close": [13.0, 13.4]},
                                index=pd.to_datetime(["2026-09-17", "2026-09-18"]))
        last = us_last if ticker.startswith("^") and ticker in ("^GSPC", "^IXIC", "^DJI") else FRIDAY
        return pd.DataFrame({"Close": [100.0, 101.0]},
                            index=pd.to_datetime([last - dt.timedelta(days=1), last]))
    return fn


def test_12_valid_overnight_source_live(built, healthy):
    r = _pre(built, mode="LIVE", history_fn=_cue_provider())
    assert r.check("GLOBAL_CUES").status == PASS, r.check("GLOBAL_CUES").message
    assert r.check("INDIA_VIX").status == PASS
    assert r.runtime["network_calls"] == 8            # 3 US + 2x2 Asia + VIX, all counted
    assert r.overall_status == READY


def test_13_stale_required_benchmark_blocks(built):
    from operations.report_lookup import find_canonical_report
    from readiness.evidence import open_history
    with open_history(built) as h:
        report = find_canonical_report(FRIDAY, history=h).report
    close = next(f for f in report.facts if f.fact_id in report.nifty["fact_ids"]
                 and f.metric.value == "INDEX_CLOSE")
    close.market_date = dt.date(2026, 9, 17)           # a provider that lagged a session
    for edition in ("PRE", "POST"):
        c = C.check_benchmark(edition, report, FRIDAY)
        assert c.status == FAIL and c.source_health == "STALE" and c.requirement == REQUIRED


def test_14_optional_market_events_failure_degrades_pre(built, healthy, monkeypatch):
    monkeypatch.setattr(C, "check_market_events", _ORIG["check_market_events"])   # real check
    _pin_record(built, market_events={"market_events_status": "CAPTURED", "results": {
        "EARNINGS": {"status": "SUCCESS", "events": [{"x": 1}]},
        "OPEN_OFFER": {"status": "SOURCE_UNAVAILABLE", "events": []}}})
    r = _pre(built)
    assert r.check("MARKET_EVENTS.OPEN_OFFER").status == WARN
    assert r.check("MARKET_EVENTS.EARNINGS").status == PASS
    assert r.overall_status == DEGRADED and "MARKET_EVENTS" in r.decision


def test_15_optional_institutional_failure_degrades_never_blocks(built, healthy, monkeypatch):
    monkeypatch.setattr(C, "check_institutional", _ORIG["check_institutional"])
    r = _pre(built)                                     # no CDSL / NSDL / NSE snapshot at all
    c = r.check("INSTITUTIONAL_FLOW")
    assert c.status == WARN and c.source_health == "MISSING"
    assert r.overall_status == DEGRADED


def test_16_market_structure_partial_but_valid(tmp_path):
    out = str(tmp_path)
    _structure(out, FRIDAY, CORE_OK, RECORD_DONE)
    pre = C.check_market_structure("PRE", out, FRIDAY, PRE_AS_OF)      # 52W PARTIAL >= 95%
    assert pre.status == PASS and pre.source_health == "PARTIAL"
    post = C.check_market_structure("POST", out, FRIDAY, POST_AS_OF)   # core PUBLISHABLE
    assert post.status == PASS and "52-week" in post.message
    _structure(out, FRIDAY, {**CORE_OK, "ADVANCES": ("PARTIAL", 97.0)}, RECORD_DONE)
    partial = C.check_market_structure("POST", out, FRIDAY, POST_AS_OF)
    assert partial.status == WARN and partial.source_health == "PARTIAL"   # degrades, not blocks
    _structure(out, FRIDAY, {k: ("SUPPRESSED", 40.0) for k in CORE_OK}, RECORD_DONE)
    assert C.check_market_structure("POST", out, FRIDAY, POST_AS_OF).status == WARN


def test_17_current_session_india_leakage_rejected(built):
    from operations.report_lookup import find_canonical_report
    from readiness.evidence import open_history
    with open_history(built) as h:
        report = find_canonical_report(FRIDAY, history=h).report
    report.facts[0].market_date = MONDAY               # an Indian fact of the session to open
    c = C.check_temporal("PRE", report, PRE_AS_OF, FRIDAY)
    assert c.status == FAIL and "future data" in c.message
    # and a PRE cutoff after the 09:15 open is never a before-the-bell edition
    late = _pre(built, as_of=dt.datetime(2026, 9, 21, 9, 20, tzinfo=IST))
    assert late.check("EDITION_WINDOW").status == FAIL and late.overall_status == BLOCKED


def test_18_short_quiet_pre_plan_is_ready(built, healthy):
    r = _pre(built)                                   # replay: no cues -> no OVERNIGHT scene
    ed = r.check("EDITORIAL_PLAN")
    assert ed.status == PASS and "OVERNIGHT" not in ed.observed["order"]
    assert ed.observed["duration"] < 45                # shorter than usual is still valid
    assert r.overall_status == READY, r.warnings + r.blocking_reasons


# =========================================================================== 19-25 POST
def test_19_completed_current_session_accepted(built, healthy):
    r = _post(built)
    assert r.session_date == FRIDAY and r.edition_date == MONDAY
    assert r.check("SESSION").status == PASS and r.check("EDITION_WINDOW").status == PASS
    assert r.overall_status == READY, r.warnings + r.blocking_reasons


def test_20_incomplete_session_blocks(built):
    r = _post(built, session=MONDAY, as_of=dt.datetime(2026, 9, 21, 14, 0, tzinfo=IST))
    c = r.check("EDITION_WINDOW")
    assert c.status == FAIL and "SESSION_NOT_FINAL" in c.message and r.overall_status == BLOCKED
    assert r.check("EDITORIAL_PLAN").status == SKIP          # nothing evaluated on top of it


def test_21_stale_current_session_benchmark_blocks_post(built, healthy, monkeypatch):
    orig = C.check_benchmark

    def stale(edition, report, session):
        close = next(f for f in report.facts if f.metric.value == "INDEX_CLOSE")
        close.market_date = session - dt.timedelta(days=1)
        return orig(edition, report, session)
    monkeypatch.setattr(C, "check_benchmark", stale)
    r = _post(built)
    assert r.check("BENCHMARK").status == FAIL and r.overall_status == BLOCKED


def test_22_optional_event_family_failure_degrades_post(built, healthy, monkeypatch):
    monkeypatch.setattr(C, "check_market_events", _ORIG["check_market_events"])
    _pin_record(built, market_events={"market_events_status": "CAPTURED", "results": {
        f: {"status": "PARSE_ERROR" if f == "BUYBACK" else "SUCCESS", "events": []}
        for f in ("EARNINGS", "OFS", "BUYBACK", "OPEN_OFFER", "DELISTING",
                  "GOVT_SECURITIES_AUCTION")}})
    r = _post(built)
    assert r.check("MARKET_EVENTS.BUYBACK").status == WARN
    assert r.check("MARKET_EVENTS.BUYBACK").source_health == "SOURCE_FAILURE"
    assert r.check("MARKET_EVENTS.OFS").status == PASS
    assert r.overall_status == DEGRADED


def test_23_market_structure_unavailable_degrades_post(built, healthy, monkeypatch):
    monkeypatch.setattr(C, "check_market_structure", _ORIG["check_market_structure"])
    r = _post(built)                                  # the fixture builds no structure snapshot
    c = r.check("MARKET_STRUCTURE")
    assert c.status == WARN and c.source_health == "MISSING"
    assert r.overall_status == DEGRADED and r.check("EDITORIAL_PLAN").status == PASS


def test_24_structurally_invalid_plan_blocks(built, healthy, monkeypatch):
    from products import post_unified as PU
    real = PU.build_post_storyboard

    def broken(*a, **k):
        sb, pres = real(*a, **k)
        sb.scenes = sb.scenes[:-1]                     # no CLOSING
        return sb, pres
    monkeypatch.setattr(PU, "build_post_storyboard", broken)
    r = _post(built)
    c = r.check("EDITORIAL_PLAN")
    assert c.status == FAIL and "close" in c.message and r.overall_status == BLOCKED


def test_24b_unresolved_duplicate_in_the_trace_blocks():
    sb = SimpleNamespace(scenes=[SimpleNamespace(kind=k) for k in ("DYNAMIC_HOOK", "PULSE",
                                                                   "CLOSING")],
                         total_duration=20.0, hook_plan={},
                         post_plan={"editorial": {"trace": [
                             {"candidate_id": "A", "decision": "SUPPRESSED_DUPLICATE",
                              "duplicate_of": "B"},
                             {"candidate_id": "B", "decision": "DROPPED_SLOTS"}]}})
    c = C.check_editorial_post("POST", sb, 62.0)
    assert c.status == FAIL and "unresolved conflict" in c.message


def test_25_valid_concise_post_accepted(built, healthy):
    r = _post(built)
    c = r.check("EDITORIAL_PLAN")
    assert c.status == PASS and 15.0 <= c.observed["duration"] < 45.0
    assert c.observed["scenes"][0] in ("DYNAMIC_HOOK", "HOOK") and c.observed["scenes"][-1] == "CLOSING"


# =========================================================================== 26-32 publication / render
def test_26_publication_policy_block_blocks(built, healthy, monkeypatch):
    import daily_video.public_storyboard as ps
    monkeypatch.setattr(ps, "audit_storyboard", lambda *a, **k: {
        "final": "BLOCK", "failed_checks": ["language", "publication_rights"], "scans": {}})
    r = _post(built)
    c = r.check("PUBLICATION")
    assert c.status == FAIL and "language" in c.message and r.overall_status == BLOCKED
    # never downgraded, whatever the intent
    assert C.check_publication("PRE", "SHADOW", {"final": "BLOCK",
                                                 "failed_checks": ["displayed_claims"]},
                               True).status == FAIL
    assert C.check_publication("PRE", "SHADOW", {"final": "PASS", "failed_checks": []},
                               False).status == FAIL          # the content scan is not SAFE


def test_27_rights_block_is_graded_by_intent(built, healthy):
    shadow = _post(built)
    assert shadow.check("PUBLICATION").status == PASS
    assert shadow.check("PUBLICATION").observed["verdict"] == "RIGHTS_BLOCK_ONLY"
    publish = _post(built, intent=PUBLISH)
    assert publish.check("PUBLICATION").status == FAIL and publish.overall_status == BLOCKED


def test_28_ffmpeg_missing_blocks(tmp_path):
    from readiness.environment import check_ffmpeg
    assert check_ffmpeg("PRE", resolver=lambda: None).status == FAIL
    assert check_ffmpeg("POST", resolver=lambda: "no-such-ffmpeg-binary").status == FAIL
    assert check_ffmpeg("POST", resolver=lambda: sys.executable,
                        runner=lambda p: False).status == FAIL
    assert check_ffmpeg("POST", resolver=lambda: sys.executable,
                        runner=lambda p: True).status == PASS


def test_29_output_path_not_writable_blocks(tmp_path):
    from readiness.environment import check_storage

    def denied(directory):
        raise PermissionError("read-only")
    c = check_storage("POST", str(tmp_path), True, "", write_probe=denied)
    assert c.status == FAIL and "not writable" in c.message
    assert check_storage("POST", str(tmp_path), False, "missing db").status == FAIL
    tiny = check_storage("POST", str(tmp_path), True, "",
                         disk_usage=lambda p: SimpleNamespace(free=10 * 1024 * 1024))
    assert tiny.status == FAIL
    ok = check_storage("POST", str(tmp_path), True, "")
    assert ok.status == PASS and not [f for f in os.listdir(tmp_path)
                                      if f.startswith(".readiness_probe_")]


def test_30_renderer_import_failure_blocks():
    import importlib
    from readiness.environment import check_renderer

    def importer(name):
        if name == "daily_video.composer":
            raise ImportError("broken renderer")
        return importlib.import_module(name)
    c = check_renderer("PRE", importer=importer)
    assert c.status == FAIL and "daily_video.composer" in c.message
    assert check_renderer("POST").status == PASS


def test_31_optional_asset_absent_has_the_right_severity():
    from readiness.environment import check_audio, check_fonts
    assert check_fonts("PRE", reporter=lambda: FONTS_OK).status == PASS   # system font: normal
    none = {"bold": {"resolved_path": None}, "regular": {"resolved_path": None}}
    assert check_fonts("PRE", reporter=lambda: none).status == FAIL       # PIL bitmap default
    a = check_audio("POST")
    assert a.status == SKIP and a.requirement == "NOT_APPLICABLE"          # silent V2 video


def _write_post_manifest(out, qa=None, failed=("publication_rights",), duration=38.0):
    folder = os.path.join(out, "post", "2026-09-21")
    os.makedirs(folder, exist_ok=True)
    json.dump({"session_date": "2026-09-18", "report_source": "REUSED_CANONICAL",
               "duration": duration,
               "qa": qa or {"video_qa": "PASS", "frames_qa": {"passed": True},
                            "content_qa": "SAFE"},
               "publication_audit": {"final": "BLOCK" if failed else "PASS",
                                     "failed_checks": list(failed)}},
              open(os.path.join(folder, "production_manifest.json"), "w"))


def test_32_final_qa_is_a_separate_post_render_stage(tmp_path):
    from readiness import evaluate_post_render
    out = str(tmp_path)
    missing = evaluate_post_render("POST", FRIDAY, out_dir=out)
    assert missing.stage == "POST_RENDER" and missing.overall_status == BLOCKED
    _write_post_manifest(out)
    ok = evaluate_post_render("POST", FRIDAY, out_dir=out)
    assert ok.overall_status == READY and ok.decision == "RENDERED EDITION PASSED QA"
    assert evaluate_post_render("POST", FRIDAY, out_dir=out, intent=PUBLISH).overall_status == BLOCKED
    _write_post_manifest(out, qa={"video_qa": "FAIL", "frames_qa": {"passed": True},
                                  "content_qa": "SAFE"})
    assert evaluate_post_render("POST", FRIDAY, out_dir=out).check("VIDEO_QA").status == FAIL
    # PRE: a DEGRADED run that passed QA is DEGRADED, not READY
    run = os.path.join(out, "pre_shadow", "2026-09-21")
    os.makedirs(run)
    json.dump({"run_status": "DEGRADED", "qa": {"ok": True, "freeze_frame_qa": True,
                                                "content_safety": "SAFE", "language_issues": [],
                                                "probed_duration": 40.0}},
              open(os.path.join(run, "shadow_manifest.json"), "w"))
    json.dump({"publication_audit": {"final": "BLOCK", "failed_checks": ["publication_rights"]}},
              open(os.path.join(run, "pre_result_2026-09-21.json"), "w"))
    assert evaluate_post_render("PRE", MONDAY, out_dir=out).overall_status == DEGRADED


# =========================================================================== 33-38 temporal / replay
def test_33_historical_pre_as_of_is_replay_without_network(built, healthy, monkeypatch):
    import providers.premarket as pm

    def no_net(*a, **k):
        raise AssertionError("network in a replay")
    monkeypatch.setattr(pm, "yahoo_history", no_net)
    r = _pre(built)
    assert r.mode == "REPLAY" and r.runtime["network_calls"] == 0
    assert r.check("GLOBAL_CUES").status == SKIP
    assert not os.path.exists(os.path.join(built, "readiness"))      # replay writes nothing


def test_34_historical_post_as_of_report_not_yet_built(built, healthy):
    before = _post(built, as_of=dt.datetime(2026, 9, 21, 7, 30, tzinfo=IST))
    c = before.check("TEMPORAL_SAFETY")
    assert c.status == FAIL and "did not exist yet" in c.message
    after = _post(built, as_of=POST_AS_OF)
    assert after.check("TEMPORAL_SAFETY").status == PASS


def test_35_no_future_source_leakage_institutional(tmp_path):
    out = str(tmp_path)
    early = dt.datetime(2026, 9, 18, 19, 0, tzinfo=IST)
    late = dt.datetime(2026, 9, 21, 9, 0, tzinfo=IST)            # after the PRE cutoff
    _inst(out, "NSE", "2026-09-18", early)
    _inst(out, "CDSL", "2026-09-18", late)
    _inst(out, "NSDL", "2026-09-15", early)
    c = C.check_institutional("PRE", out, MONDAY, PRE_AS_OF, nse_session=FRIDAY)
    assert c.status == WARN and c.observed["CDSL"]["health"] == "MISSING"   # not yet captured
    assert c.observed["NSE"]["health"] == HEALTHY and c.observed["NSDL"]["health"] == HEALTHY
    after = C.check_institutional("PRE", out, MONDAY, late + dt.timedelta(minutes=1),
                                  nse_session=FRIDAY)
    assert after.status == PASS                                    # captured -> available


def test_36_no_future_market_event_revision_leakage(tmp_path):
    from readiness.evidence import events_known_at, report_record_at
    out = str(tmp_path)
    r1 = dt.datetime(2026, 9, 18, 19, 0, tzinfo=IST)
    r2 = dt.datetime(2026, 9, 22, 19, 0, tzinfo=IST)               # a later correction
    _event(out, "EARNINGS", "EARNINGS:ABC:b1", "SCHEDULED", r1)
    _event(out, "EARNINGS", "EARNINGS:ABC:b1", "REVISED_DATE", r2)
    assert events_known_at(out, "EARNINGS", r1 - dt.timedelta(minutes=1)) == []   # before capture
    (old,) = events_known_at(out, "EARNINGS", PRE_AS_OF)
    assert old.status == "SCHEDULED"                                # before the revision
    (new,) = events_known_at(out, "EARNINGS", r2 + dt.timedelta(minutes=1))
    assert new.status == "REVISED_DATE"                             # after it
    # a REPORT capture record completed after the cutoff did not exist yet
    folder = os.path.join(out, "report_jobs", "2026-09-18")
    os.makedirs(folder)
    json.dump({"completed_at": r2.isoformat(), "details": {}},
              open(os.path.join(folder, "report_job_a.json"), "w"))
    assert report_record_at(out, FRIDAY, PRE_AS_OF) is None
    assert report_record_at(out, FRIDAY, r2 + dt.timedelta(seconds=1)) is not None


def test_37_no_future_market_structure_snapshot_leakage(tmp_path):
    out = str(tmp_path)
    _structure(out, FRIDAY, CORE_OK, dt.datetime(2026, 9, 21, 9, 0, tzinfo=IST))
    c = C.check_market_structure("PRE", out, FRIDAY, PRE_AS_OF)
    assert c.status == WARN and c.source_health == "MISSING" and "did not exist" in c.message
    assert C.check_market_structure("PRE", out, FRIDAY,
                                    dt.datetime(2026, 9, 21, 9, 1, tzinfo=IST)).status == PASS


def _stable(r):
    d = r.to_dict()
    d.pop("evaluated_at")
    d.pop("runtime")
    return json.dumps(d, sort_keys=True, default=str)


def test_38_historical_readiness_is_independent_of_the_wall_clock(built, healthy, monkeypatch):
    import config
    import products.premarket as pm
    results = []
    for now in (dt.datetime(2026, 9, 21, 8, 0, tzinfo=IST), dt.datetime(2031, 1, 1, tzinfo=IST)):
        monkeypatch.setattr(config, "now_ist", lambda n=now: n)
        monkeypatch.setattr(pm, "now_ist", lambda n=now: n)
        results.append((_stable(_pre(built)), _stable(_post(built))))
    assert results[0] == results[1]


# =========================================================================== 39-46 CLI
@pytest.fixture
def cli(monkeypatch):
    import readiness.__main__ as M
    box = {}

    def evaluate(edition, **kw):
        box["kw"] = kw
        if box.get("raise"):
            raise box["raise"]
        mode = "REPLAY" if kw.get("session_date") or kw.get("as_of") else "LIVE"
        return _result(*box["statuses"], edition=edition.upper(), mode=mode,
                       session_date=MONDAY, as_of=PRE_AS_OF, cutoff=PRE_AS_OF)
    monkeypatch.setattr(M, "evaluate", evaluate)
    return M, box


@pytest.mark.parametrize("statuses,verdict,code", [
    ((PASS, PASS), "READY", 0),                       # 39 / 43
    ((PASS, WARN), "DEGRADED", 10),                   # 40 / 44
    ((WARN, FAIL), "BLOCKED", 20),                    # 41 / 45
])
def test_39_45_human_output_and_exit_codes(cli, capsys, statuses, verdict, code):
    M, box = cli
    box["statuses"] = statuses
    assert M.main(["pre", "--no-write"]) == code
    out = capsys.readouterr().out
    assert "DMB PRE READINESS" in out and f"VERDICT: {verdict}" in out and "DECISION:" in out


def test_42_json_output_schema(cli, capsys):
    M, box = cli
    box["statuses"] = (PASS, WARN)
    assert M.main(["post", "--json", "--no-write"]) == 10
    d = json.loads(capsys.readouterr().out)
    for key in ("schema", "edition", "stage", "mode", "intent", "evaluated_at", "as_of", "cutoff",
                "session_date", "overall_status", "decision", "exit_code", "blocking_reasons",
                "warnings", "checks", "runtime"):
        assert key in d
    assert d["schema"] == "dmb.readiness/1" and d["overall_status"] == "DEGRADED"
    for c in d["checks"]:
        assert {"check_id", "category", "capability", "requirement", "severity", "status",
                "message", "source_health", "observed", "expected", "remediation"} <= set(c)


def test_46_internal_error_exit_code(cli, capsys):
    M, box = cli
    box["raise"] = ReadinessExecutionError("history unreadable")
    assert M.main(["pre"]) == 30
    assert "VERDICT: ERROR" in capsys.readouterr().out
    box["raise"] = RuntimeError("unexpected")
    assert M.main(["post", "--json"]) == 30
    assert json.loads(capsys.readouterr().out)["overall_status"] is None


def test_cli_live_writes_one_report_and_replay_writes_none(cli, tmp_path):
    import config
    M, box = cli
    box["statuses"] = (PASS,)
    M.main(["pre"])
    files = os.listdir(os.path.join(config.OUT_DIR, "readiness"))
    assert files == ["readiness_PRE_2026-09-21.json"]
    M.main(["pre"])                                              # overwritten, not sprawl
    M.main(["pre", "--as-of", "2026-09-21T07:45:00"])
    assert os.listdir(os.path.join(config.OUT_DIR, "readiness")) == files
    assert box["kw"]["as_of"].tzinfo is not None                  # naive = IST


# =========================================================================== runners / isolation
def test_evening_full_stops_before_the_post_when_blocked(tmp_path):
    from operations import evening_full as EF
    from test_evening_full import Runner, _run
    r = Runner(str(tmp_path))
    gate = _result(PASS, FAIL)
    res = _run(tmp_path, r, readiness_fn=lambda *a: gate)
    assert r.calls == [EF.REPORT_ARGS]                           # the POST never rendered
    assert res["rows"]["READINESS"][0] == "BLOCKED" and "readiness BLOCKED" in res["fatal"]
    assert res["rows"]["POST_UNIFIED"][0] == "NOT RUN" and EF.verdict(res) == EF.ATTENTION


def test_evening_full_continues_when_degraded_and_fails_closed_on_error(tmp_path):
    from operations import evening_full as EF
    from test_evening_full import Runner, _run
    r = Runner(str(tmp_path))
    res = _run(tmp_path, r, readiness_fn=lambda *a: _result(PASS, WARN))
    assert r.calls == [EF.REPORT_ARGS, EF.POST_ARGS] and res["fatal"] is None
    assert res["rows"]["READINESS"][0] == "DEGRADED"
    assert any(n.startswith("READINESS WARN") for n in res["notes"])

    def boom(*a):
        raise RuntimeError("gate bug")
    r2 = Runner(str(tmp_path / "b"))
    res2 = _run(tmp_path / "b", r2, readiness_fn=boom)
    assert r2.calls == [EF.REPORT_ARGS] and "could not evaluate" in res2["fatal"]


def test_morning_pre_script_gates_the_render_on_readiness():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", "run_morning_pre.bat")
    text = open(path, encoding="utf-8").read()
    gate, render = text.index("python -m readiness pre"), text.index("python main.py --mode premarket")
    assert gate < render and '"%READY_RC%"=="20"' in text and "exit /b 2" in text
    assert b"\r\n" in open(path, "rb").read() and "--upload" not in text


def test_readiness_is_read_only_over_the_stores(built, healthy):
    def snapshot():
        out = {}
        for root, _dirs, files in os.walk(built):
            for f in files:
                p = os.path.join(root, f)
                out[p] = (os.path.getsize(p), os.path.getmtime(p))
        return out
    before = snapshot()
    _pre(built)
    _post(built)
    assert snapshot() == before


def test_open_readonly_history_refuses_writes_and_never_creates(tmp_path):
    import sqlite3
    from storage import MarketHistory
    with pytest.raises(FileNotFoundError):
        MarketHistory.open_readonly(str(tmp_path / "nope.db"))
    assert not os.path.exists(tmp_path / "nope.db")
    MarketHistory(str(tmp_path / "h.db")).close()
    with MarketHistory.open_readonly(str(tmp_path / "h.db")) as h:
        assert h.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31)) == []
        with pytest.raises(sqlite3.OperationalError):
            h.conn.execute("CREATE TABLE x (a INTEGER)")


def test_readiness_never_reaches_the_private_desk_or_upload():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "readiness")
    import ast
    for name in os.listdir(root):
        if name.endswith(".py"):
            src = open(os.path.join(root, name), encoding="utf-8").read()
            assert "private_desk" not in src, name
            mods = set()
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module.split(".")[0])
            assert not mods & {"upload", "requests", "urllib", "yfinance", "news", "market",
                               "googleapiclient"}, (name, mods)


def test_matrix_is_edition_specific():
    from readiness.matrix import MATRIX, requirement
    assert requirement("GLOBAL_CUES", "PRE") == OPTIONAL
    assert requirement("GLOBAL_CUES", "POST") == "NOT_APPLICABLE"
    assert requirement("MOVERS", "POST") == OPTIONAL and requirement("MOVERS", "PRE") == "NOT_APPLICABLE"
    required = {k for k, v in MATRIX.items() if REQUIRED in v[:2]}
    assert {"SESSION", "CANONICAL_REPORT", "BENCHMARK", "EDITORIAL_PLAN", "PUBLICATION",
            "RENDERER", "FFMPEG", "STORAGE"} <= required
    assert not {"MARKET_EVENTS", "INSTITUTIONAL_FLOW", "MARKET_STRUCTURE"} & required

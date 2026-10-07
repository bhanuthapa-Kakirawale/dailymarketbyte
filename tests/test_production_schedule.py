"""PRE production scheduling + shadow mode: the REPORT job, POST reuse of the canonical report,
PRE's exact previous-session rule, shadow runs, the GIFT publication gate, the connectivity
diagnostic, the official-event reminder and REPORT/POST/PRE run-history separation.

Fully offline: every network boundary is stubbed (the POST pipeline via `offline_pipeline`),
NSE IX payloads are SYNTHETIC (same shape as the exchange's, no real exchange content).
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

import main
from conftest import IST, SESSION
from core.trading_calendar import SessionCalendar
from operations import gift_policy as gp
from operations.report_lookup import MISSING, find_canonical_report
from operations.sessions import (SESSION_FINAL_TIME, edition_date_for, latest_final_session,
                                 next_session)
from products import VideoRequest, route
from storage import MarketHistory
from test_pipeline import _Args, offline_pipeline  # noqa: F401  (fixture re-export)

CAL = SessionCalendar()
FRIDAY = SESSION                                     # 2026-09-18, the offline pipeline's session
MONDAY = dt.date(2026, 9, 21)
EVENING = dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST)
PRE_AS_OF = dt.datetime(2026, 9, 21, 7, 45, tzinfo=IST)


@pytest.fixture(autouse=True)
def _no_live_acquisition(monkeypatch):
    """Belt and braces: in this module real acquisition must be unreachable. `offline_pipeline`
    replaces these with stubs; a test that forgets it fails loudly instead of fetching live data
    (and writing wherever OUT_DIR happens to point). Never call `monkeypatch.undo()` here - it
    also reverts conftest's OUT_DIR isolation."""
    def _live(*a, **k):
        raise AssertionError("live market acquisition attempted in an offline test")
    monkeypatch.setattr(main.market, "get_market", _live)
    monkeypatch.setattr(main.news, "ai_pass", _live)


def _db(tmp_path):
    return str(tmp_path / "data" / "market_history.db")


def _runs(tmp_path, **kw):
    with MarketHistory(_db(tmp_path)) as h:
        return h.get_publication_runs(**kw)


def _reports(tmp_path):
    with MarketHistory(_db(tmp_path)) as h:
        return h.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))


def _radar_stub(calls=None):
    def fn(session, out_dir, as_of):
        if calls is not None:
            calls.append(session)
        return {"status": "BUILT", "pipeline_status": "OK", "stories": 0}
    return fn


def _official_stub(session, out_dir, now):
    """Official snapshots captured fine (their own behaviour: tests/test_official_snapshots)."""
    return {"official_snapshot_status": "SUCCESS", "ipo_snapshot_status": "NO_DATA",
            "exchange_snapshot_status": "SUCCESS", "capture": "ATTEMPTED", "kinds": {}}


def _report_job(**kw):
    from products.report_job import run_report_job
    kw.setdefault("now", EVENING)
    kw.setdefault("radar_fn", _radar_stub())
    kw.setdefault("official_fn", _official_stub)
    return run_report_job(kw.pop("session_date", None), **kw)


# --------------------------------------------------------------------------- sessions
def test_session_final_time_matches_the_market_rule():
    import market
    assert SESSION_FINAL_TIME == market.SESSION_FINAL_TIME


@pytest.mark.parametrize("now,expected", [
    (dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST), dt.date(2026, 9, 18)),   # Fri evening
    (dt.datetime(2026, 9, 18, 15, 39, tzinfo=IST), dt.date(2026, 9, 17)),   # Fri, not final
    (dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST), dt.date(2026, 9, 18)),    # Mon morning -> Fri
    (dt.datetime(2026, 9, 19, 11, 0, tzinfo=IST), dt.date(2026, 9, 18)),    # Saturday
    (dt.datetime(2026, 9, 15, 7, 40, tzinfo=IST), dt.date(2026, 9, 11)),    # Tue after Mon holiday
])
def test_latest_final_session_follows_the_canonical_calendar(now, expected):
    assert latest_final_session(now, CAL) == expected


def test_edition_is_the_next_canonical_session():
    assert edition_date_for(FRIDAY, CAL) == MONDAY                        # Friday -> Monday
    assert edition_date_for(dt.date(2026, 9, 11), CAL) == dt.date(2026, 9, 15)   # skips 14 Sep
    assert edition_date_for(dt.date(2026, 10, 1), CAL) == dt.date(2026, 10, 5)   # skips 2 Oct
    assert next_session(dt.date(2030, 1, 1), CAL) is None                 # calendar can't say


# --------------------------------------------------------------------------- 1. REPORT job
def test_report_job_builds_the_canonical_report_without_rendering(offline_pipeline, tmp_path):
    rec = _report_job()
    assert rec["run_status"] == "SUCCESS" and rec["details"]["report_source"] == "BUILT"
    assert "scenes" not in offline_pipeline, "the report job must never render"
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".mp4")]
    (stored,) = _reports(tmp_path)
    assert stored.session_date == FRIDAY.isoformat()
    # report_date is the edition (the Monday it recaps Friday for) - the same id the 07:40
    # POST run has always produced, so the two paths can never create two canonical reports
    assert stored.report_id == "20260921_PRE_MARKET" and stored.report_date == "2026-09-21"
    run = _runs(tmp_path, job_type="REPORT_BUILD")[0]
    assert (run.mode, run.job, run.run_status) == ("REPORT_BUILD", "REPORT_BUILD", "SUCCESS")
    assert run.target_date == run.source_session_date == FRIDAY.isoformat()
    assert run.publication_status == "NOT_APPLICABLE" and run.report_id == stored.report_id
    assert os.path.exists(rec["record_path"])


def test_evening_build_never_accepts_a_next_morning_gift_reading(offline_pipeline):
    # ai_pass (stubbed) "answers" a GIFT reading; the fixture's global tiles carry one legacy
    # GIFT tile of their own. Only the morning path may add Gemini's reading on top.
    evening = main.collect(_Args(), MONDAY, morning_facts=False)
    morning = main.collect(_Args(), MONDAY)
    gift_tiles = lambda raw: sum(t["label"] == "GIFT NIFTY" for t in raw["tiles"])
    assert "gift" not in evening["ai_facts"] and "gift" in morning["ai_facts"]
    assert gift_tiles(morning) == gift_tiles(evening) + 1


def test_report_job_resolution_refuses_a_session_that_is_not_final(offline_pipeline, tmp_path):
    rec = _report_job(session_date=MONDAY)                   # asked on Friday evening
    assert rec["run_status"] == "BLOCKED" and rec["failure_stage"] == "SESSION_NOT_FINAL"
    rec = _report_job(session_date=dt.date(2026, 9, 14))     # a listed NSE holiday
    assert rec["failure_stage"] == "NOT_A_SESSION"
    assert _reports(tmp_path) == []


def test_report_job_never_builds_from_an_older_benchmark_session(offline_pipeline, tmp_path,
                                                                 monkeypatch, market_dict):
    stale = dict(market_dict, recap_date=dt.date(2026, 9, 17), prev_date=dt.date(2026, 9, 16))
    monkeypatch.setattr(main.market, "get_market", lambda: dict(stale))
    rec = _report_job()
    assert rec["run_status"] == "BLOCKED" and rec["failure_stage"] == "SESSION_ALIGNMENT"
    assert _reports(tmp_path) == []


# --------------------------------------------------------------------------- 5. idempotency
def test_report_job_is_idempotent(offline_pipeline, tmp_path, monkeypatch):
    calls = []
    first = _report_job(radar_fn=_radar_stub(calls))
    report_path = first["details"]["report_path"]
    mtime = os.path.getmtime(report_path)

    def _no_fetch():
        raise AssertionError("an already-built session must not be re-acquired")

    monkeypatch.setattr(main.market, "get_market", _no_fetch)
    # radar artifacts for the session now "exist" -> the rerun skips Radar too
    radar_dir = tmp_path / "radar"
    (radar_dir / "presentation").mkdir(parents=True)
    (radar_dir / f"daily_radar_{FRIDAY}.json").write_text(json.dumps({"pipeline_status": "OK"}))
    (radar_dir / "presentation" / f"radar_presentation_{FRIDAY}.json").write_text("{}")
    second = _report_job(radar_fn=_radar_stub(calls))
    assert second["run_status"] == "SUCCESS"
    assert second["details"]["report_source"] == "ALREADY_BUILT"
    assert second["details"]["radar"]["status"] == "ALREADY_BUILT" and calls == [FRIDAY]
    assert len(_reports(tmp_path)) == 1 and os.path.getmtime(report_path) == mtime
    assert len(_runs(tmp_path, job_type="REPORT_BUILD")) == 2      # runs append, history doesn't


def test_unfit_report_is_not_committed_and_the_job_can_be_retried(offline_pipeline, tmp_path,
                                                                  monkeypatch):
    from core import MarketReport
    state = {"fit": False}
    real = MarketReport.publication_ready
    monkeypatch.setattr(MarketReport, "publication_ready",
                        property(lambda self: state["fit"] and real.fget(self)))
    rec = _report_job()
    assert rec["run_status"] == "BLOCKED" and rec["failure_stage"] == "DATA_QA"
    assert _reports(tmp_path) == [], "an unfit evening build must not become canonical"
    assert os.path.exists(rec["diagnostic_artifact"]) and "unfit" in rec["diagnostic_artifact"]
    # PRE the next morning surfaces the missing report
    from products.premarket import PreMarketBlocked, require_previous_report
    with pytest.raises(PreMarketBlocked) as exc:
        require_previous_report(MONDAY, CAL, _db(tmp_path))
    assert exc.value.code == "PREVIOUS_SESSION_MISSING"
    # the data settles; a retry commits the canonical report
    state["fit"] = True
    retry = _report_job()
    assert retry["run_status"] == "SUCCESS" and len(_reports(tmp_path)) == 1


def test_radar_failure_degrades_the_job_but_keeps_the_report(offline_pipeline, tmp_path):
    rec = _report_job(radar_fn=lambda *a: {"status": "FAILED", "error": "benchmark down"})
    assert rec["run_status"] == "DEGRADED"
    assert rec["details"]["degradations"][0]["source"] == "MARKET_RADAR"
    assert find_canonical_report(FRIDAY, db_path=_db(tmp_path)).found


# --------------------------------------------------------------------------- 6. POST reuse
def test_post_reuses_the_canonical_report(offline_pipeline, tmp_path, monkeypatch):
    _report_job()

    def _no_fetch():
        raise AssertionError("POST must render from the stored report, not re-acquire")

    monkeypatch.setattr(main.market, "get_market", _no_fetch)
    out = main.run(_Args())
    assert out and os.path.exists(out) and "scenes" in offline_pipeline
    assert len(_reports(tmp_path)) == 1
    post = _runs(tmp_path, job_type="POST_MARKET")[0]
    assert post.details["report_source"] == "REUSED_CANONICAL"
    assert post.run_status == "SUCCESS" and post.source_session_date == FRIDAY.isoformat()
    assert offline_pipeline["info"]["today_short"] == "Mon 21 Sep"     # the report's edition


def test_post_builds_inline_when_the_report_job_did_not_run(offline_pipeline, tmp_path):
    assert main.run(_Args())
    post = _runs(tmp_path, job_type="POST_MARKET")[0]
    assert post.details["report_source"] == "BUILT_INLINE"
    assert find_canonical_report(FRIDAY, db_path=_db(tmp_path)).found


def test_post_video_failure_never_invalidates_the_stored_report(offline_pipeline, tmp_path,
                                                                monkeypatch):
    _report_job()
    before = find_canonical_report(FRIDAY, db_path=_db(tmp_path))

    def _crash(*a, **k):
        raise OSError("ffmpeg died")

    from products import post_unified
    monkeypatch.setattr(post_unified, "render_post", _crash)
    with pytest.raises(OSError):
        main.run(_Args())
    post = _runs(tmp_path, job_type="POST_MARKET")[0]
    assert post.run_status == "FAILED" and "ffmpeg" in post.failure_reason
    after = find_canonical_report(FRIDAY, db_path=_db(tmp_path))
    assert after.found and after.report.to_json() == before.report.to_json()


# --------------------------------------------------------------------------- 2-4. PRE previous session
def test_monday_pre_finds_fridays_report(offline_pipeline, tmp_path):
    from products.premarket import require_previous_report
    _report_job()
    prev, report, path = require_previous_report(MONDAY, CAL, _db(tmp_path))
    assert prev == FRIDAY and report.session_date == FRIDAY and os.path.exists(path)


def test_missing_previous_report_blocks_and_an_older_one_is_never_substituted(offline_pipeline,
                                                                              tmp_path):
    from products.premarket import PreMarketBlocked, require_previous_report
    _report_job()                                      # only Friday 18 Sep is stored
    for pre_date, expected in ((dt.date(2026, 9, 22), dt.date(2026, 9, 21)),
                               (dt.date(2026, 10, 5), dt.date(2026, 10, 1))):   # Fri holiday
        assert CAL.previous_session(pre_date) == expected
        with pytest.raises(PreMarketBlocked) as exc:
            require_previous_report(pre_date, CAL, _db(tmp_path))
        assert exc.value.code == "PREVIOUS_SESSION_MISSING" and str(expected) in exc.value.message


def test_holiday_uses_the_previous_canonical_session():
    assert CAL.previous_session(dt.date(2026, 9, 15)) == dt.date(2026, 9, 11)   # Mon 14 holiday
    assert CAL.previous_session(dt.date(2026, 10, 5)) == dt.date(2026, 10, 1)   # Fri 2 Oct holiday


def test_unusable_canonical_artifact_is_reported_not_substituted(offline_pipeline, tmp_path):
    _report_job()
    os.remove(find_canonical_report(FRIDAY, db_path=_db(tmp_path)).path)
    lookup = find_canonical_report(FRIDAY, db_path=_db(tmp_path))
    assert lookup.status == "ARTIFACT_MISSING" and lookup.report is None
    assert find_canonical_report(MONDAY, db_path=_db(tmp_path)).status == MISSING


def test_pre_on_a_non_session_day_is_skipped(tmp_path):
    from products.premarket import run_premarket
    req = VideoRequest(mode="premarket", session_date=dt.date(2026, 10, 2), frames_only=True,
                       out_dir=str(tmp_path / "o"))
    assert run_premarket(req, db_path=_db(tmp_path)) is None
    run = _runs(tmp_path, job_type="PRE_MARKET")[0]
    assert run.run_status == "SKIPPED" and run.failure_stage == "NOT_A_SESSION"


# --------------------------------------------------------------------------- GIFT fakes (SYNTHETIC)
def _rate(ltp="25,160.00", change="60.00", ts="21-Sep-2026 07:38:12"):
    return json.dumps({"data": [{"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY",
                                 "EXPIRYDATE": "29-Sep-2026", "LASTPRICE": ltp,
                                 "DAYCHANGE": change, "PERCHANGE": ".24",
                                 "CONTRACTSTRADED": 1000, "TIMESTMP": ts}]})


def _dsp(day, settle=25100.0):
    d = day.strftime("%d-%b-%Y").upper()
    return ("DATE,INSTRUMENT TYPE,SYMBOL,EXPIRY DATE,STRIKE,OPTION TYPE,SETTLEMENT PRICE\n"
            f"{d},FUTIDX,NIFTY,29-SEP-2026,0,FF,{settle:.7f}\n")


def _gift_fn(calls=None, fail=None):
    from providers import gift_nifty as gn
    files = {gn.DSP_URL.format(d=FRIDAY): _dsp(FRIDAY)}

    def get(url):
        if url == gn.MARKET_RATE_URL:
            return 200, _rate()
        return (200, files[url]) if url in files else (404, "Not Found")

    def fn(pre_date, as_of):
        if calls is not None:
            calls.append(pre_date)
        if fail:
            raise gn.GiftSourceError(fail)
        return gn.fetch_gift_nifty(pre_date, as_of, get=get, nifty_close=25140.35,
                                   retrieved_at=dt.datetime(2026, 9, 21, 7, 45, 30, tzinfo=IST))
    return fn


ENABLED = gp.GiftPolicy(True, "true", "test approval ref", "enabled under approval: test")


def _brief(tmp_path, **kw):
    from products.premarket import build_real_brief
    return build_real_brief(MONDAY, PRE_AS_OF, history_fn=lambda *a: None,
                            calendar=CAL, db_path=_db(tmp_path), **kw)


# --------------------------------------------------------------------------- 8-10. GIFT policy
@pytest.mark.parametrize("env,allowed", [
    ({}, False),
    ({"GIFT_NIFTY_PUBLICATION_ENABLED": "false"}, False),
    ({"GIFT_NIFTY_PUBLICATION_ENABLED": "0"}, False),
    ({"GIFT_NIFTY_PUBLICATION_ENABLED": "true"}, False),      # no approval reference
    ({"GIFT_NIFTY_PUBLICATION_ENABLED": "yes", "GIFT_NIFTY_PUBLICATION_APPROVAL": "  "}, False),
    ({"GIFT_NIFTY_PUBLICATION_ENABLED": "true",
      "GIFT_NIFTY_PUBLICATION_APPROVAL": "NDAL licence ref X"}, True),
])
def test_gift_publication_defaults_disabled_and_needs_an_approval(env, allowed):
    policy = gp.gift_publication_policy(env)
    assert policy.publication_allowed is allowed and policy.reason
    assert gp.should_fetch(policy, shadow=True) is True
    assert gp.should_fetch(policy, shadow=False) is allowed


def test_gift_policy_default_in_this_repository_is_off(monkeypatch):
    monkeypatch.delenv("GIFT_NIFTY_PUBLICATION_ENABLED", raising=False)
    monkeypatch.delenv("GIFT_NIFTY_PUBLICATION_APPROVAL", raising=False)
    assert gp.gift_publication_policy().publication_allowed is False


def test_publication_mode_never_fetches_or_shows_unapproved_gift(offline_pipeline, tmp_path):
    _report_job()
    calls = []
    brief, acq = _brief(tmp_path, gift_fn=_gift_fn(calls), shadow=False,
                        gift_policy=gp.gift_publication_policy({}))
    assert calls == [] and acq.gift is None and brief.gift is None
    assert acq.gift_status["status"] == "POLICY_DISABLED"
    assert brief.gift_policy == {"fetched": False, "publication_allowed": False,
                                 "withheld": False, "reason": brief.gift_policy["reason"],
                                 "shadow": False}
    from presentation.pre_plan import plan_pre_sections
    plan = plan_pre_sections(brief)
    assert not plan.show_gift_nifty
    gift_omit = [o for o in plan.omitted if o["section"] == "GIFT NIFTY"]
    assert gift_omit and "publication policy" in gift_omit[0]["reason"]
    from products.pre_run_history import degradations
    assert "GIFT NIFTY" not in {d["source"] for d in degradations(acq, brief)}


def test_shadow_mode_evaluates_gift_but_never_displays_it(offline_pipeline, tmp_path):
    _report_job()
    calls = []
    brief, acq = _brief(tmp_path, gift_fn=_gift_fn(calls), shadow=True,
                        gift_policy=gp.gift_publication_policy({}))
    assert calls == [MONDAY]
    assert acq.gift is not None and acq.gift.fresh and acq.gift.validation_status == "SINGLE_SOURCE"
    assert brief.gift is None and brief.gift_policy["withheld"] is True
    from presentation.pre_plan import plan_pre_sections
    assert not plan_pre_sections(brief).show_gift_nifty
    audit = gp.gift_audit(acq, gp.gift_publication_policy({}), shadow=True, fetched=True,
                          displayed=False)
    assert (audit["gift_data_available"], audit["gift_data_valid"],
            audit["gift_publication_allowed"], audit["gift_displayed"]) == (True, True, False, False)
    r = audit["reading"]
    assert r["expiry"] == "2026-09-29" and r["live_price"] == 25160.0
    assert r["previous_settlement"] == 25100.0 and r["settlement_date"] == "2026-09-18"
    assert r["reconciles_with_settlement"] is True and r["freshness"] == "FRESH"
    assert abs(r["calculated_pct"] - (25160 / 25100 - 1) * 100) < 1e-3
    assert "contracts_seen" not in json.dumps(audit)       # no raw exchange rows in the audit


def test_approved_gift_is_displayed(offline_pipeline, tmp_path):
    _report_job()
    brief, acq = _brief(tmp_path, gift_fn=_gift_fn(), shadow=False, gift_policy=ENABLED)
    from presentation.pre_plan import plan_pre_sections
    assert brief.gift is not None and plan_pre_sections(brief).show_gift_nifty


# --------------------------------------------------------------------------- 7/13. shadow runs
def _stub_render(result_ok=True):
    def fn(brief, out_dir, **kw):
        os.makedirs(out_dir, exist_ok=True)
        from presentation.pre_plan import plan_pre_sections
        plan = plan_pre_sections(brief)
        tag = brief.pre_date.isoformat()
        for name in (f"pre_section_plan_{tag}.json", f"pre_provenance_{tag}.json",
                     f"pre_result_{tag}.json"):
            open(os.path.join(out_dir, name), "w").write("{}")
        open(os.path.join(out_dir, f"full_pre_{tag}.mp4"), "wb").write(b"stub")
        return {"ok": result_ok, "blocked": None, "order": plan.order, "total_duration": 40.0,
                "content_safety": "SAFE", "language_issues": [], "hook": None,
                "plan": {"reasons": plan.reasons, "omitted": plan.omitted},
                "freeze_frame_qa": {"passed": True, "issues": {}},
                "video": os.path.join(out_dir, f"full_pre_{tag}.mp4"),
                "provenance": {"ok": True, "ai_violations": [], "displayed": []},
                "gift_displayed": bool(plan.show_gift_nifty)}
    return fn


def _shadow(tmp_path, monkeypatch, gift_fn, upload=False):
    import products.premarket as pm
    monkeypatch.setattr(pm, "render_pre", _stub_render())
    import upload as up
    monkeypatch.setattr(up, "upload", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a shadow run must never upload")))
    monkeypatch.delenv("GIFT_NIFTY_PUBLICATION_ENABLED", raising=False)
    req = VideoRequest(mode="premarket", session_date=MONDAY, as_of=PRE_AS_OF, shadow=True,
                       upload=upload)
    return pm.run_premarket(req, db_path=_db(tmp_path), history_fn=lambda *a: None,
                            gift_fn=gift_fn)


def test_shadow_upload_request_is_refused(tmp_path, monkeypatch):
    assert _shadow(tmp_path, monkeypatch, _gift_fn(), upload=True) is None
    assert _runs(tmp_path) == []


def test_shadow_run_retains_the_full_artifact_set(offline_pipeline, tmp_path, monkeypatch):
    _report_job()
    res = _shadow(tmp_path, monkeypatch, _gift_fn())
    run_dir = tmp_path / "pre_shadow" / MONDAY.isoformat()
    manifest = json.loads((run_dir / "shadow_manifest.json").read_text())
    assert manifest["shadow"] and manifest["never_uploaded"]
    for key in ("pre_plan", "provenance", "gift_audit", "run_record", "mp4", "qa_result"):
        assert manifest["artifacts"][key], key
    assert manifest["gift"]["gift_data_valid"] is True and manifest["gift"]["gift_displayed"] is False
    run = _runs(tmp_path, job_type="PRE_MARKET")[0]
    assert run.publication_status == "SHADOW" and run.mode == "PRE_MARKET"
    assert run.target_date == MONDAY.isoformat() and run.source_session_date == FRIDAY.isoformat()
    assert run.details["gift_audit"]["gift_publication_allowed"] is False
    assert res["gift_audit"]["reading"]["contract"].startswith("NSEIX:NIFTY FUTIDX")


def test_unreachable_gift_is_degraded_not_blocked(offline_pipeline, tmp_path, monkeypatch):
    _report_job()
    res = _shadow(tmp_path, monkeypatch, _gift_fn(fail="HTTP 403 from a cloud runner"))
    assert res["run"]["run_status"] == "DEGRADED" and res["video"]
    gift = [d for d in res["degradations"] if d["source"] == "GIFT NIFTY"]
    assert gift and "403" in gift[0]["reason"]
    assert res["gift_audit"]["gift_data_available"] is False


def test_shadow_without_previous_report_is_blocked_with_a_manifest(tmp_path, monkeypatch):
    assert _shadow(tmp_path, monkeypatch, _gift_fn()) is None
    run = _runs(tmp_path, job_type="PRE_MARKET")[0]
    assert run.run_status == "BLOCKED" and run.failure_stage == "PREVIOUS_SESSION_MISSING"
    manifest = json.loads((tmp_path / "pre_shadow" / MONDAY.isoformat()
                           / "shadow_manifest.json").read_text())
    assert manifest["run_status"] == "BLOCKED" and manifest["missing_artifacts"]


# --------------------------------------------------------------------------- 12. run history
def test_report_post_and_pre_runs_are_distinct(offline_pipeline, tmp_path, monkeypatch):
    _report_job()
    main.run(_Args())
    _shadow(tmp_path, monkeypatch, _gift_fn())
    jobs = {r.job for r in _runs(tmp_path)}
    assert jobs == {"REPORT_BUILD", "POST_MARKET", "PRE_MARKET"}
    for job, mode in (("REPORT_BUILD", "REPORT_BUILD"), ("POST_MARKET", "LOCAL"),
                      ("PRE_MARKET", "PRE_MARKET")):
        (r,) = _runs(tmp_path, job_type=job)
        assert r.mode == mode and r.job_type == job and r.completed_at and r.run_status
        assert r.source_session_date == FRIDAY.isoformat()
    assert _runs(tmp_path, job_type="PRE_MARKET")[0].target_date == MONDAY.isoformat()


def test_legacy_rows_keep_their_job_through_the_mode(tmp_path):
    with MarketHistory(str(tmp_path / "h.db")) as h:
        rid = h.start_run("LOCAL")
        h.conn.execute("UPDATE publication_runs SET job_type = NULL WHERE run_id = ?", (rid,))
        h.start_run("PRE_MARKET", target_date=MONDAY)
        assert [r.job for r in h.get_publication_runs(job_type="POST_MARKET")] == ["POST_MARKET"]
        assert [r.mode for r in h.get_publication_runs(job_type="PRE_MARKET")] == ["PRE_MARKET"]


# --------------------------------------------------------------------------- router
def test_router_modes():
    with pytest.raises(ValueError):
        route(VideoRequest(mode="report", upload=True))
    with pytest.raises(ValueError):
        route(VideoRequest(mode="postmarket", shadow=True), postmarket_runner=lambda a: None)


# --------------------------------------------------------------------------- 11. connectivity
def test_connectivity_diagnostic_fails_safely_and_prints_no_secret(monkeypatch):
    import requests
    from operations import connectivity as cx
    monkeypatch.setenv("GEMINI_API_KEY", "sekret-value-123")

    def get(url):
        if "market-rate" in url:
            raise requests.exceptions.ConnectTimeout("connect timed out")
        if "DSP" in url:
            return 403, "Forbidden"
        return 200, "<html>not a csv</html>"

    def history_fn(ticker, start, end, interval):
        if ticker == "^N225":
            raise requests.exceptions.ConnectionError("Connection refused")
        import pandas as pd
        return pd.DataFrame() if ticker == "^GSPC" else pd.DataFrame(
            {"Close": [1.0, 2.0]}, index=pd.to_datetime(["2026-09-17", "2026-09-18"]))

    res = cx.run_diagnostic(get=get, history_fn=history_fn,
                            now=dt.datetime(2026, 9, 21, 7, 30, tzinfo=IST))
    by = {c["check"]: c["status"] for c in res["checks"]}
    assert by["NSEIX_MARKET_RATE"] == cx.TIMEOUT
    assert by["NSEIX_SETTLEMENT"] == cx.BLOCKED
    assert by["NSE_INDEX_ARCHIVE"] == cx.PARSE_ERROR
    assert by["YAHOO_ASIA_5M"] == cx.BLOCKED and by["YAHOO_US_DAILY"] == cx.NO_DATA
    assert by["YAHOO_NIFTY_DAILY"] == cx.REACHABLE
    assert res["summary"]["required_sources_reachable"] is False
    assert res["summary"]["gift_sources_reachable"] is False
    text = json.dumps(res)
    assert "sekret-value-123" not in text and "GEMINI" not in text


def test_connectivity_all_reachable(monkeypatch):
    from operations import connectivity as cx
    from providers import gift_nifty as gn

    def get(url):
        if url == gn.MARKET_RATE_URL:
            return 200, _rate()
        if "DSP" in url:
            return 200, _dsp(FRIDAY)
        return 200, "Index Name,Index Date,Closing Index Value\nNifty 50,18-09-2026,25140.35\n"

    import pandas as pd
    hist = lambda *a: pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-09-18"]))
    res = cx.run_diagnostic(get=get, history_fn=hist,
                            now=dt.datetime(2026, 9, 21, 7, 30, tzinfo=IST))
    assert all(c["status"] == cx.REACHABLE for c in res["checks"]), res["checks"]
    assert res["reference_session"] == FRIDAY.isoformat()


# --------------------------------------------------------------------------- 12. RBI reminder
def _events_file(tmp_path, verified_on, event_date="2027-02-05"):
    entry = {"event_id": "rbi-mpc-test", "organiser": "RBI", "event_type": "RBI",
             "event_name": "RBI MPC policy decision", "tag": "RBI POLICY",
             "event_date": event_date, "meeting_dates": ["2027-02-03", "2027-02-04", event_date],
             "event_time": None, "time_label": "Final day", "importance": "HIGH",
             "source_url": "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=1",
             "source_reference": "test", "source_text": "February 3, 4 and 5, 2027",
             "retrieved_at": "2026-09-25T21:33:00+05:30", "verified_on": verified_on,
             "validation_status": "OFFICIAL_SCHEDULE"}
    p = tmp_path / "events.json"
    p.write_text(json.dumps({"schema_version": "official-events-1.0", "events": [entry]}))
    return str(p)


@pytest.mark.parametrize("verified,today,status", [
    ("2026-09-25", dt.date(2026, 10, 1), "OK"),              # expiry 2027-03-24, far away
    ("2026-09-25", dt.date(2027, 1, 1), "WARN_COVERAGE"),    # last event 2027-02-05 < 60 days
    ("2026-08-01", dt.date(2027, 1, 10), "WARN_EXPIRING"),   # expiry 2027-01-28: 18 days left
    ("2026-07-01", dt.date(2027, 1, 10), "EXPIRED"),         # expired 2026-12-28
])
def test_official_event_reminder_warns_before_expiry(tmp_path, verified, today, status):
    from operations.official_events import check_official_events
    res = check_official_events(today, _events_file(tmp_path, verified))
    assert res["status"] == status, res["findings"]
    expiry = dt.date.fromisoformat(verified) + dt.timedelta(days=180)
    assert res["next_verification_expiry"] == expiry.isoformat()


def test_official_event_reminder_flags_expired_and_coverage(tmp_path):
    from operations.official_events import check_official_events
    res = check_official_events(dt.date(2027, 1, 20), _events_file(tmp_path, "2026-07-01"))
    levels = {f["level"] for f in res["findings"]}
    assert res["status"] == "EXPIRED" and "EXPIRED" in levels and "WARN_COVERAGE" in levels


def test_official_event_reminder_on_the_real_file_never_mutates_it():
    from core.event_calendar import OFFICIAL_EVENTS_PATH
    from operations.official_events import check_official_events
    before = open(OFFICIAL_EVENTS_PATH, encoding="utf-8").read()
    res = check_official_events(dt.date(2026, 9, 25))
    assert res["file_status"] == "OK" and res["accepted"] >= 1
    assert open(OFFICIAL_EVENTS_PATH, encoding="utf-8").read() == before

"""PRE data sources + run history: GIFT Nifty (NSE IX), the RBI official-event file, PRE run
history, PRE provenance, and the frozen PRE renderers.

Fully offline. NSE IX payloads here are SYNTHETIC but mirror the real response shapes
(verified live 2026-09-25: `market-rate?type=derivative` rows with string numbers like ".16"
and "-.02", TIMESTMP "25-Sep-2026 21:28:56"; DSP CSV header "DATE,INSTRUMENT TYPE,SYMBOL,
EXPIRY DATE,STRIKE,OPTION TYPE,SETTLEMENT PRICE").
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3

import pytest

from core.event_calendar import (EventValidation, OfficialCalendar, event_calendar_audit,
                                 load_official_events, parse_meeting_line, scheduled_events_for,
                                 validate_official_entry)
from core.freshness import IST, FreshnessStatus
from core.sources import GROUP_NSEIX, SRC_NSEIX_LIVE, SourceFamily, source_metadata
from core.trading_calendar import SessionCalendar
from presentation.pre_plan import build_pre_brief, plan_pre_sections, pre_language_issues
from products.pre_fixtures import AS_OF, PRE_DATE, PREV, synthetic_brief
from providers import gift_nifty as gn
from providers.premarket import (PreMarketAcquisition, VixReading, fetch_premarket_quotes,
                                 gift_status_of)

CAL = SessionCalendar()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- NSE IX fakes
def _rate_payload(ltp="23,955.50", change="-44.50", ts="06-Oct-2026 07:38:12",
                  near="27-Oct-2026", extra_rows=True):
    rows = [{"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY", "EXPIRYDATE": near,
             "OPTIONTYPE": "-", "STRIKEPRICE": "-", "LASTPRICE": ltp, "DAYCHANGE": change,
             "PERCHANGE": "-.19", "CONTRACTSTRADED": 51234, "TIMESTMP": ts}]
    if extra_rows:
        rows += [{"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY", "EXPIRYDATE": "24-Nov-2026",
                  "LASTPRICE": "24100.00", "DAYCHANGE": "0.00", "PERCHANGE": "0",
                  "CONTRACTSTRADED": 1, "TIMESTMP": "15-Sep-2026 19:06:10"},
                 {"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "BANKNIFTY", "EXPIRYDATE": near,
                  "LASTPRICE": "51000", "DAYCHANGE": "10", "TIMESTMP": ts},
                 {"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY", "EXPIRYDATE": "29-Sep-2026",
                  "LASTPRICE": "23900", "DAYCHANGE": "1", "TIMESTMP": "29-Sep-2026 15:40:00"}]
    return json.dumps({"data": rows})


def _dsp(day: dt.date, settle=24000.0, expiry="27-OCT-2026", row_day=None):
    d = (row_day or day).strftime("%d-%b-%Y").upper()
    return ("DATE,INSTRUMENT TYPE,SYMBOL,EXPIRY DATE,STRIKE,OPTION TYPE,SETTLEMENT PRICE\n"
            f"{d},FUTIDX,NIFTY,{expiry},0,FF,{settle:.7f}\n"
            f"{d},FUTIDX,NIFTY,24-NOV-2026,0,FF,{settle + 60:.7f}\n"
            f"{d},FUTIDX,BANKNIFTY,{expiry},0,FF,51000.0000000\n")


def _fake_get(files: dict, rate=None, rate_code=200):
    def get(url):
        if url == gn.MARKET_RATE_URL:
            return rate_code, rate if rate is not None else _rate_payload()
        return (200, files[url]) if url in files else (404, "<html>Not Found</html>")
    return get


LIVE_RETRIEVED = dt.datetime(2026, 10, 6, 7, 45, 30, tzinfo=IST)     # a live run, 30s after cutoff


def _dsp_url(day):
    return gn.DSP_URL.format(d=day)


# --------------------------------------------------------------------------- 1. GIFT parsing
def test_market_rate_parsing_mirrors_the_real_shape():
    cs = gn.parse_market_rate(_rate_payload())
    assert [c.expiry for c in cs] == [dt.date(2026, 9, 29), dt.date(2026, 10, 27),
                                      dt.date(2026, 11, 24)]          # BANKNIFTY ignored
    near = cs[1]
    assert near.last_price == 23955.5 and near.day_change == -44.5 and near.pct_change == -0.19
    assert near.timestamp == dt.datetime(2026, 10, 6, 7, 38, 12, tzinfo=IST)
    assert gn._num(".16") == 0.16 and gn._num("-.02") == -0.02 and gn._num("-") is None


def test_near_month_is_the_earliest_expiry_on_or_after_the_session():
    cs = gn.parse_market_rate(_rate_payload())
    assert gn.select_near_month(cs, dt.date(2026, 9, 29)).expiry == dt.date(2026, 9, 29)
    assert gn.select_near_month(cs, dt.date(2026, 9, 30)).expiry == dt.date(2026, 10, 27)
    assert gn.select_near_month(cs, dt.date(2026, 12, 1)) is None


def test_market_rate_without_data_is_not_a_quote():
    with pytest.raises(gn.GiftSourceError):
        gn.parse_market_rate({"error": "blocked"})


def test_settlement_file_must_carry_its_own_date():
    day = dt.date(2026, 10, 5)
    assert gn.parse_settlement_file(_dsp(day), day)[dt.date(2026, 10, 27)] == 24000.0
    with pytest.raises(gn.GiftSourceError):
        gn.parse_settlement_file(_dsp(day, row_day=dt.date(2026, 10, 2)), day)
    with pytest.raises(gn.GiftSourceError):
        gn.parse_settlement_file("DATE,SYMBOL\n05-OCT-2026,NIFTY\n", day)


# --------------------------------------------------------------------------- 2. GIFT freshness
def _fetch(get, as_of=AS_OF, retrieved=LIVE_RETRIEVED, nifty_close=24043.0):
    return gn.fetch_gift_nifty(PRE_DATE, as_of, get=get, retrieved_at=retrieved,
                               nifty_close=nifty_close)


def test_fresh_gift_from_the_exchange_with_full_provenance():
    get = _fake_get({_dsp_url(PREV): _dsp(PREV)})
    q = _fetch(get)
    assert q.fresh and q.freshness is FreshnessStatus.FRESH
    assert q.change_pct == pytest.approx((23955.5 / 24000.0 - 1) * 100)
    assert q.reference_value == 24000.0 and q.reference_date == PREV
    assert q.source == SRC_NSEIX_LIVE and q.source_type == "PRIMARY"
    assert q.independence_group == GROUP_NSEIX and q.validation_status == "SINGLE_SOURCE"
    p = q.provenance
    assert p["consistency"] == "CONSISTENT" and p["settlement_url"] == _dsp_url(PREV)
    assert p["contract_expiry"] == "2026-10-27" and p["last_trade_timestamp"].startswith("2026-10-06T07:38")
    assert q.retrieved_at == LIVE_RETRIEVED and q.market_timestamp.hour == 7


def test_missing_settlement_file_steps_back_to_the_last_one():
    fri = dt.date(2026, 10, 2)
    get = _fake_get({_dsp_url(fri): _dsp(fri)})       # Mon 5 Oct: no file (NSE IX holiday)
    q = _fetch(get)
    assert q.reference_date == fri and q.fresh
    assert [a["result"] for a in q.provenance["settlement_attempts"]][:1] == ["HTTP 404"]


def test_stale_gift_is_omitted():
    get = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="06-Oct-2026 06:55:00"))
    q = _fetch(get)
    assert q.freshness is FreshnessStatus.STALE and not q.fresh
    plan = plan_pre_sections(dataclasses.replace(synthetic_brief("QUIET"), gift=q))
    assert not plan.show_gift_nifty and all(w.category != "GIFT" for w in plan.watch)
    assert any(o["section"] == "GIFT NIFTY" and o["reason"].startswith("STALE")
               for o in plan.omitted)


def test_gift_from_the_previous_evening_is_stale():
    get = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="05-Oct-2026 23:10:00"))
    assert _fetch(get).freshness is FreshnessStatus.STALE


def test_a_reading_after_the_india_open_is_not_pre_open():
    # a live run late on the session date: the print is "fresh" by age but not pre-open
    get = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="06-Oct-2026 22:05:00"))
    late = dt.datetime(2026, 10, 6, 22, 6, tzinfo=IST)
    q = gn.fetch_gift_nifty(PRE_DATE, late, get=get, retrieved_at=late, nifty_close=24043.0)
    assert q.freshness is FreshnessStatus.STALE and "not a pre-open reading" in q.freshness_reason


def test_reconstruction_never_uses_a_reading_taken_after_the_cutoff():
    get = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="06-Oct-2026 21:29:00"))
    q = _fetch(get, retrieved=dt.datetime(2026, 10, 6, 21, 30, tzinfo=IST))
    assert q.freshness is FreshnessStatus.FUTURE and not q.fresh
    assert gift_status_of(q)["status"] == "FUTURE"


def test_live_run_tolerates_a_small_exchange_clock_skew():
    get = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="06-Oct-2026 07:45:50"))
    q = _fetch(get)
    assert q.fresh and q.provenance["clock_skew_seconds"] == 20.0


def test_inconsistent_exchange_reference_is_a_conflict_and_omitted():
    get = _fake_get({_dsp_url(PREV): _dsp(PREV, settle=23900.0)})     # DAYCHANGE implies 24000
    q = _fetch(get)
    assert q.validation_status == "CONFLICT" and q.change_pct is None and not q.fresh
    plan = plan_pre_sections(dataclasses.replace(synthetic_brief("QUIET"), gift=q))
    assert not plan.show_gift_nifty
    assert any(o["reason"].startswith("CONFLICT") for o in plan.omitted)


@pytest.mark.parametrize("ltp,change,close", [("27000", "3000", 24043.0),     # +12.5%: bad print
                                              ("23955.5", "-44.5", 21000.0)])  # 14% from Nifty
def test_implausible_gift_is_rejected(ltp, change, close):
    settle = float(ltp) - float(change)
    get = _fake_get({_dsp_url(PREV): _dsp(PREV, settle=settle)},
                    rate=_rate_payload(ltp=ltp, change=change))
    q = _fetch(get, nifty_close=close)
    assert q.validation_status == "REJECTED" and not q.fresh


def test_unavailable_gift_is_omitted_and_never_blocks():
    get = _fake_get({}, rate_code=403)
    with pytest.raises(gn.GiftSourceError):
        _fetch(get)
    acq = fetch_premarket_quotes(PRE_DATE, AS_OF, PREV, CAL, history_fn=lambda *a: None,
                                 gift_fn=lambda d, a: _fetch(get))
    assert acq.gift is None and acq.gift_status["status"] == "UNAVAILABLE"
    assert any(line.startswith("GIFT NIFTY: source failed") for line in acq.log)
    brief = dataclasses.replace(synthetic_brief("QUIET"), gift=acq.gift)
    plan = plan_pre_sections(brief)
    assert not plan.show_gift_nifty and plan.order        # the Short still plans


def test_gift_freshness_limit_is_twenty_minutes():
    from core.freshness import LIVE_MAX_AGE
    assert LIVE_MAX_AGE["GIFT"] == dt.timedelta(minutes=20)
    ok = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="06-Oct-2026 07:26:00"))
    old = _fake_get({_dsp_url(PREV): _dsp(PREV)}, rate=_rate_payload(ts="06-Oct-2026 07:24:00"))
    assert _fetch(ok).fresh and not _fetch(old).fresh


# --------------------------------------------------------------------------- 3. wording
def _fresh_gift():
    return _fetch(_fake_get({_dsp_url(PREV): _dsp(PREV)}))


def test_gift_wording_is_past_tense_with_time_and_never_predicts():
    brief = dataclasses.replace(synthetic_brief("QUIET"), gift=_fresh_gift())
    plan = plan_pre_sections(brief)
    assert plan.overnight.gift.sentence == "GIFT Nifty was 0.19% lower at 7:38 AM IST"
    assert plan.overnight.gift.reference == "VS ITS MON SETTLEMENT"
    from daily_video.pre_storyboard import build_pre_storyboard
    sb = build_pre_storyboard(brief, plan)
    public = sb.public_text()
    assert pre_language_issues(public) == []
    blob = " ".join(v for v in public.values() if isinstance(v, str)).lower()
    for bad in ("will open", "points to", "gap", "likely", "expected to", "indicates"):
        assert bad not in blob, bad
    assert "nifty's close" not in blob            # never a futures-vs-spot comparison
    gift_card = next(w for w in plan.watch if w.category == "GIFT")
    assert gift_card.title == "GIFT Nifty -0.19%" and "Mon settlement" in gift_card.note


def test_hook_sheet_words_gift_as_its_own_move():
    from hooks.sheet_pre import pre_market_inputs_from_brief, pre_market_sheet
    brief = dataclasses.replace(synthetic_brief("QUIET"), gift=_fresh_gift())
    sheet = pre_market_sheet(pre_market_inputs_from_brief(brief, plan_pre_sections(brief)))
    f = sheet.fact("gift.gap")
    assert f is not None and f.display == "0.19% lower"
    assert "Nifty's close" not in f.statement


# --------------------------------------------------------------------------- 4. RBI calendar
def test_rbi_file_loads_from_the_official_publication_only():
    cal = load_official_events()
    assert cal.status == "OK" and not cal.rejected
    rbi = [e for e in cal.events if e.event_type == "RBI"]
    assert [e.event_date.isoformat() for e in rbi] == [
        "2026-04-08", "2026-06-05", "2026-08-05", "2026-10-07", "2026-12-04", "2027-02-05"]
    for e in rbi:
        assert e.source.startswith("https://www.rbi.org.in/")
        assert e.source_reference == "RBI Press Release 2025-2026/2306"
        assert e.event_time is None                     # RBI's schedule states no time
        assert e.verified_on and e.retrieved_at


def test_rbi_decision_day_reaches_the_plan_with_its_source():
    evs = scheduled_events_for(dt.date(2026, 10, 7), CAL, expiry_weekday=1)
    assert [e.event_type for e in evs] == ["RBI"]
    # synthetic=False: a synthetic brief labels every source "synthetic fixture" by design
    brief = dataclasses.replace(synthetic_brief("QUIET"), events=evs, synthetic=False)
    plan = plan_pre_sections(brief)
    assert plan.event.title == "RBI MPC policy decision"
    assert plan.event.source_label == "Source: RBI press release"
    assert plan.event.time_label == "Final day of the RBI MPC meeting"
    assert not re.search(r"\d{1,2}:\d{2}", plan.event.time_label)


def _entry(**over):
    base = json.load(open(os.path.join(ROOT, "data", "official_events.json"),
                          encoding="utf-8"))["events"][3]
    return dict(base, **over)


@pytest.mark.parametrize("over,why", [
    ({"source_url": "https://www.someblog.com/rbi-mpc-dates"}, "own site"),
    ({"source_url": "http://www.rbi.org.in/x"}, "own site"),
    ({"verified_on": ""}, "missing"),
    ({"source_reference": None}, "missing"),
    ({"meeting_dates": ["2026-10-06", "2026-10-07", "2026-10-08"]}, "do not match"),
    ({"event_date": "2026-10-05"}, "final day"),
    ({"event_time": "10:00"}, "no invented times"),
    ({"validation_status": "UNVERIFIED"}, "not an official schedule"),
])
def test_unverified_or_malformed_rbi_entry_is_rejected(over, why):
    ev, reason = validate_official_entry(_entry(**over))
    assert ev is None and why in reason


def test_rbi_time_only_with_a_quoted_time_source():
    ev, _ = validate_official_entry(_entry(event_time="10:00",
                                           time_source_text="announced at 10:00 AM"))
    assert ev is not None and ev.event_time == dt.time(10, 0)


def test_missing_or_corrupt_event_file_fails_closed(tmp_path):
    missing = load_official_events(str(tmp_path / "nope.json"))
    assert missing.status == "UNAVAILABLE" and missing.events == ()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_official_events(str(bad)).status == "INVALID"
    one_bad = tmp_path / "partial.json"
    one_bad.write_text(json.dumps({"events": [_entry(), _entry(event_id="x", source_url="https://x.com")]}),
                       encoding="utf-8")
    part = load_official_events(str(one_bad))
    assert part.status == "PARTIAL" and len(part.events) == 1 and part.rejected[0]["event_id"] == "x"
    audit = event_calendar_audit(dt.date(2026, 10, 7), CAL, 1, official_file=missing)
    assert audit["shown"] == [] and audit["official_file"]["status"] == "UNAVAILABLE"


def test_stale_verification_is_not_shown():
    ev, _ = validate_official_entry(_entry(verified_on="2026-03-01"))
    assert scheduled_events_for(dt.date(2026, 10, 7), CAL, 1, official=(ev,)) == []
    audit = event_calendar_audit(dt.date(2026, 10, 7), CAL, 1,
                                 official_file=OfficialCalendar("x", "OK", (ev,)))
    assert audit["excluded"] and "more than 180 days" in audit["excluded"][0]["reason"]


def test_meeting_line_parser_and_verifier_comparison():
    assert parse_meeting_line("December 2, 3 and 4, 2026") == [
        [dt.date(2026, 12, 2), dt.date(2026, 12, 3), dt.date(2026, 12, 4)]]
    import verify_official_events as vo
    entries = [_entry()]
    page = "Press Release: 2025-2026/2306 ... October 5, 6 and 7, 2026"
    assert vo.compare_source(entries, page)["matches"]
    moved = vo.compare_source(entries, "2025-2026/2306 October 6, 7 and 8, 2026")
    assert not moved["matches"] and moved["page_meetings_not_in_file"]
    assert not vo.compare_source(entries, "October 5, 6 and 7, 2026")["reference_found"]


# --------------------------------------------------------------------------- 5. run history
def _acq(gift=None, gift_status=None, vix_fresh=True):
    base = synthetic_brief("QUIET")
    acq = PreMarketAcquisition(global_cues=list(base.global_cues), vix=base.vix, gift=gift,
                               gift_status=gift_status or gift_status_of(gift))
    if not vix_fresh:
        acq.vix = None
    return acq


def _events_ok():
    return event_calendar_audit(PRE_DATE, CAL, 1)


def _result(ok=True, blocked=None, hook_source="DETERMINISTIC"):
    return {"ok": ok, "blocked": blocked, "order": ["OVERNIGHT", "SETUP", "WATCH"],
            "total_duration": 40.0, "content_safety": "SAFE", "language_issues": [],
            "hook": {"source": hook_source, "fallback_reason": None if hook_source == "GEMINI"
                     else "no client"},
            "plan": {"reasons": {"VIX": "omitted: small"}, "omitted": []},
            "freeze_frame_qa": {"passed": ok, "issues": {}}, "video": "x.mp4" if ok else None,
            "provenance": {"ok": True, "ai_violations": [], "displayed": []}}


def _recorder(tmp_path):
    from products.pre_run_history import PreRunRecorder
    rec = PreRunRecorder(str(tmp_path / "h.db"))
    rec.start(PRE_DATE, AS_OF, {"frames_only": True})
    return rec


def _runs(tmp_path, **kw):
    from storage import MarketHistory
    with MarketHistory(str(tmp_path / "h.db")) as h:
        return h.get_publication_runs(**kw)


def test_pre_run_history_success(tmp_path):
    brief = dataclasses.replace(synthetic_brief("QUIET"), gift=_fresh_gift())
    rec = _recorder(tmp_path).finished(_result(), brief, _acq(brief.gift), _events_ok(),
                                       out_dir=str(tmp_path / "run"))
    assert rec["run_status"] == "SUCCESS" and rec["details"]["degradations"] == []
    run = _runs(tmp_path, mode="PRE_MARKET")[0]
    assert (run.mode, run.target_date, run.run_status) == ("PRE_MARKET", PRE_DATE.isoformat(), "SUCCESS")
    assert run.publication_status == "NOT_ATTEMPTED" and run.report_id is None
    assert run.started_at and run.completed_at and run.video_qa_status == "PASSED"
    d = run.details
    for key in ("section_plan", "freshness", "gift", "events", "hook", "outputs", "qa",
                "provenance", "previous_session"):
        assert key in d, key
    assert os.path.exists(rec["record_path"])


def test_pre_run_history_degraded_names_every_reason(tmp_path):
    brief = synthetic_brief("QUIET")
    rec = _recorder(tmp_path).finished(
        _result(hook_source="DETERMINISTIC"), brief,
        _acq(None, {"status": "UNAVAILABLE", "reason": "GiftSourceError: HTTP 403"}, vix_fresh=False),
        _events_ok(), hook_ai=True)
    assert rec["run_status"] == "DEGRADED"
    by = {d["source"]: d for d in rec["details"]["degradations"]}
    assert by["GIFT NIFTY"]["status"] == "UNAVAILABLE" and "403" in by["GIFT NIFTY"]["reason"]
    assert by["INDIA VIX"]["status"] == "UNAVAILABLE"
    assert by["GEMINI HOOK"]["status"] == "FALLBACK"
    assert _runs(tmp_path)[0].run_status == "DEGRADED"


def test_pre_run_history_blocked_and_failed(tmp_path):
    from products import VideoRequest
    from products.premarket import run_premarket
    req = VideoRequest(mode="premarket", session_date=dt.date(2026, 9, 25), frames_only=True,
                       out_dir=str(tmp_path / "out"))
    assert run_premarket(req, db_path=str(tmp_path / "h.db"),
                         history_fn=lambda *a: None, gift_fn=lambda d, a: None) is None
    run = _runs(tmp_path, mode="PRE_MARKET")[0]
    assert run.run_status == "BLOCKED" and run.failure_stage == "PREVIOUS_SESSION_MISSING"
    assert run.target_date == "2026-09-25" and run.completed_at
    # a gate block and a failed render
    brief = synthetic_brief("QUIET")
    assert _recorder(tmp_path).finished(_result(ok=False, blocked="content/language gate: x"),
                                        brief, _acq(), _events_ok())["run_status"] == "BLOCKED"
    assert _recorder(tmp_path).finished(_result(ok=False), brief, _acq(),
                                        _events_ok())["run_status"] == "FAILED"
    r = _recorder(tmp_path)
    assert r.failed(RuntimeError("ffmpeg died"), "RENDER")["run_status"] == "FAILED"
    statuses = [x.run_status for x in _runs(tmp_path, mode="PRE_MARKET")]
    assert sorted(statuses) == ["BLOCKED", "BLOCKED", "FAILED", "FAILED"]


def test_render_exception_is_recorded_then_raised(tmp_path, monkeypatch):
    import products.premarket as pm
    brief = synthetic_brief("QUIET")
    monkeypatch.setattr(pm, "build_real_brief", lambda *a, **k: (brief, _acq()))
    monkeypatch.setattr(pm, "render_pre", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    from products import VideoRequest
    with pytest.raises(OSError):
        pm.run_premarket(VideoRequest(mode="premarket", session_date=PRE_DATE,
                                      out_dir=str(tmp_path / "o")), db_path=str(tmp_path / "h.db"))
    run = _runs(tmp_path)[0]
    assert run.run_status == "FAILED" and run.failure_stage == "RENDER" and "disk full" in run.failure_reason


def test_run_premarket_records_a_degraded_run_end_to_end(tmp_path, monkeypatch):
    import products.premarket as pm
    brief = synthetic_brief("QUIET")
    monkeypatch.setattr(pm, "build_real_brief", lambda *a, **k: (brief, _acq()))
    monkeypatch.setattr(pm, "render_pre", lambda *a, **k: _result())
    from products import VideoRequest
    res = pm.run_premarket(VideoRequest(mode="premarket", session_date=PRE_DATE,
                                        out_dir=str(tmp_path / "o")), db_path=str(tmp_path / "h.db"))
    assert res["run"]["run_status"] == "DEGRADED"           # no GIFT source -> degraded, rendered
    assert any(d["source"] == "GIFT NIFTY" for d in res["degradations"])


def test_demo_runs_are_kept_apart(tmp_path):
    from products.pre_run_history import PRE_DEMO_MODE, PreRunRecorder
    r = PreRunRecorder(str(tmp_path / "h.db"), mode=PRE_DEMO_MODE)
    r.start(PRE_DATE, AS_OF)
    r.finished(_result(), synthetic_brief("QUIET"))
    assert _runs(tmp_path, mode="PRE_MARKET") == []
    assert len(_runs(tmp_path, mode=PRE_DEMO_MODE)) == 1


def test_history_failure_never_blocks_pre(tmp_path):
    from products.pre_run_history import PreRunRecorder
    blocker = tmp_path / "file"
    blocker.write_text("x")
    r = PreRunRecorder(str(blocker / "h.db"))        # a path that cannot be a database
    r.start(PRE_DATE, AS_OF)
    rec = r.finished(_result(), synthetic_brief("QUIET"), out_dir=str(tmp_path / "o"))
    assert rec["history_error"] and rec["run_status"] in ("SUCCESS", "DEGRADED")
    assert os.path.exists(rec["record_path"])


# --------------------------------------------------------------------------- 6. POST regression
def _v1_db(path):
    from storage.schema import SCHEMA_SQL
    # strip every column/index added after v1 (v2: target_date, run_status; v3: job_type,
    # source_session_date) to get the v1 publication_runs table
    v1 = re.sub(r",\s*target_date\s+TEXT.*?\n\);", "\n);", SCHEMA_SQL, flags=re.S)
    v1 = re.sub(r"CREATE INDEX IF NOT EXISTS idx_runs_(mode|job)_target[^;]*;", "", v1)
    assert "target_date" not in v1 and "job_type" not in v1
    conn = sqlite3.connect(path)
    conn.executescript(v1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO publication_runs (run_id, report_id, mode, stage, started_at, "
                 "publication_status) VALUES ('r1','rep1','LOCAL','NO_UPLOAD','2026-09-25T02:10:00',"
                 "'NOT_ATTEMPTED')")
    conn.commit()
    conn.close()


def test_v1_history_migrates_additively(tmp_path):
    from storage import SCHEMA_VERSION, MarketHistory
    path = str(tmp_path / "v1.db")
    _v1_db(path)
    with MarketHistory(path) as h:
        assert h.schema_version == SCHEMA_VERSION == 3
        post = h.get_publication_runs()
        assert len(post) == 1 and post[0].mode == "LOCAL" and post[0].target_date is None
        # the legacy row is not rewritten; its job is derived from its mode on read
        assert post[0].job_type is None and post[0].job == "POST_MARKET"
        rid = h.start_run("PRE_MARKET", target_date=PRE_DATE, stage="STARTED")
        h.finish_run(rid, "RENDERED", "NOT_ATTEMPTED", run_status="SUCCESS")
        # a POST report's runs never include PRE runs
        assert [r.run_id for r in h.get_publication_runs(report_id="rep1")] == ["r1"]
        assert [r.mode for r in h.get_publication_runs(mode="PRE_MARKET")] == ["PRE_MARKET"]
        assert h.get_publication_runs(target_date=PRE_DATE)[0].run_status == "SUCCESS"


def test_post_run_api_unchanged(tmp_path):
    from storage import MarketHistory
    with MarketHistory(str(tmp_path / "p.db")) as h:
        rid = h.start_run("LOCAL")
        h.finish_run(rid, "NO_UPLOAD", "NOT_ATTEMPTED", artifact_path="x.mp4")
        run = h.get_publication_runs()[0]
        assert (run.stage, run.publication_status, run.run_status, run.target_date) == \
            ("NO_UPLOAD", "NOT_ATTEMPTED", None, None)


# --------------------------------------------------------------------------- 7. provenance
def _report_with_flows(source_type):
    from core import MarketReport
    from core.enums import Metric, SourceType
    from core.models import Fact, Observation
    day = dt.date(2026, 9, 24)
    r = MarketReport(report_id="t", report_type="PRE_MARKET", report_date=day, session_date=day)
    r.nifty = {"close": 23063.1, "change_pct": -1.6, "open": 23221.8, "high": 23281.9,
               "low": 23046.2, "previous_close": 23446.8}
    ids = []
    for who, val in (("fii", -5027.4), ("dii", 4301.2)):
        o = Observation(metric=Metric.FII_NET_CASH if who == "fii" else Metric.DII_NET_CASH,
                        instrument=who.upper(), value=val, unit="INR_CR", market_date=day,
                        source_name="gemini" if source_type is SourceType.AI else "nse_website",
                        source_type=source_type, retrieved_at=dt.datetime(2026, 9, 25, 2, 0))
        f = r.add_fact(Fact.from_observations([o]))
        ids.append(f.fact_id)
    r.institutional_flows = {"fii_net_cash_cr": -5027.4, "dii_net_cash_cr": 4301.2,
                             "legacy_source_tag": "x", "fact_ids": ids}
    return r


def test_ai_only_flows_never_reach_a_pre_screen():
    from core.enums import SourceType
    for st, kept in ((SourceType.AI, False), (SourceType.PRIMARY, True)):
        brief = build_pre_brief(_report_with_flows(st), dt.date(2026, 9, 25),
                                dt.datetime(2026, 9, 25, 7, 45, tzinfo=IST), CAL,
                                PreMarketAcquisition(), [])
        assert (brief.flows is not None) is kept
        if not kept:
            assert any("backed only by an AI source" in n for n in brief.notes)
        else:
            obs = next(iter(brief.fact_provenance.values()))["observations"]
            assert obs[0]["source"] == "nse_website" and obs[0]["source_type"] == "PRIMARY"


def test_provenance_audit_covers_every_pre_fact_and_blocks_ai(tmp_path):
    from presentation.pre_provenance import pre_provenance_audit
    brief = dataclasses.replace(synthetic_brief("RISK_OFF"), gift=_fresh_gift())
    audit = pre_provenance_audit(brief, plan_pre_sections(brief))
    cats = {r["category"] for r in audit["records"]}
    assert {"US_INDEX", "ASIA_INDEX", "GIFT", "INDIA_VIX", "FII_DII", "PREV_NIFTY"} <= cats
    gift = next(r for r in audit["records"] if r["category"] == "GIFT")
    assert gift["displayed"] and gift["source"] == SRC_NSEIX_LIVE and gift["retrieved_at"]
    assert gift["provenance"]["settlement_url"] and gift["market_timestamp"]
    assert audit["ok"] and audit["ai_violations"] == []
    # a model-sourced reading on screen is a provenance violation, and render_pre refuses it
    ai = dataclasses.replace(brief.gift, source="gemini", source_type="AI", independence_group="GEMINI")
    bad = dataclasses.replace(brief, gift=ai)
    assert not pre_provenance_audit(bad, plan_pre_sections(bad))["ok"]
    from products.premarket import render_pre
    # PRIVATE_ANALYTICS lets the reading reach the provenance gate, which refuses it ...
    res = render_pre(dataclasses.replace(bad, publication_profile="PRIVATE_ANALYTICS"),
                     str(tmp_path / "r"), frames_only=True)
    assert res["blocked"].startswith("provenance gate") and not res["ok"]
    # ... and PUBLIC_UNREGISTERED withholds GIFT before it (NSE IX rights RESTRICTED, policy
    # closed), so it can never be displayed at all
    pub = render_pre(dataclasses.replace(bad, publication_profile="PUBLIC_UNREGISTERED"),
                     str(tmp_path / "p"), frames_only=True)
    assert pub["gift_displayed"] is False


def test_no_acquisition_path_reaches_a_model():
    for rel in ("providers/gift_nifty.py", "providers/premarket.py", "core/event_calendar.py",
                "core/freshness.py", "presentation/pre_provenance.py", "products/pre_run_history.py",
                "verify_official_events.py"):
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        code = re.sub(r'"""[\s\S]*?"""|#.*', "", src)
        assert not re.search(r"ask_gemini|import news|from news|providers\.gemini|genai", code), rel


def test_new_sources_are_registered_honestly():
    for name in ("nseix_market_rate", "nseix_settlement_file"):
        m = source_metadata(name)
        assert m.source_type.value == "PRIMARY" and m.independence_group == GROUP_NSEIX
    assert source_metadata("rbi_press_release").source_family is SourceFamily.REGULATOR


# --------------------------------------------------------------------------- 8. frozen renderers
_FROZEN_SHA = json.load(open(os.path.join(os.path.dirname(__file__), "frozen_renderers.json"),
                             encoding="utf-8"))


@pytest.mark.parametrize("rel", sorted(_FROZEN_SHA))
def test_frozen_renderers_unchanged(rel):
    src = open(os.path.join(ROOT, rel), "rb").read().replace(b"\r\n", b"\n")
    assert hashlib.sha256(src).hexdigest() == _FROZEN_SHA[rel], \
        f"{rel} changed - PRE/POST visuals are frozen; a change needs owner approval"

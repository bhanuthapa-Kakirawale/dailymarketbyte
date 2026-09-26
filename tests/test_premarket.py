"""PRE-MARKET V1: planner, freshness, events, acquisition, storyboard, language and QA.

Fully offline: synthetic briefs (products.pre_fixtures), hand-built frames for the provider's
pure readers, a fake history_fn for the network wiring, and no Gemini unless a fake client is
passed in explicitly.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import json
import re

import pandas as pd
import pytest

from core.event_calendar import (OFFICIAL_EVENTS, EventValidation, Importance, ScheduledEvent,
                                 expiry_event, scheduled_events_for)
from core.freshness import (IST, FreshnessStatus, QuoteKind, expected_us_session,
                            live_freshness, us_close_freshness)
from core.trading_calendar import SessionCalendar
from presentation import pre_plan
from presentation.pre_plan import (MAX_RUNTIME, PreMarketBlocked, build_pre_brief,
                                   plan_pre_sections, pre_language_issues)
from products import VideoRequest, route
from products.pre_fixtures import AS_OF, PRE_DATE, PREV, SCENARIOS, synthetic_brief
from providers.premarket import (PreMarketQuote, VixReading, fetch_premarket_quotes, live_quote,
                                 us_close_quote, vix_reading)

CAL = SessionCalendar()


def _plan_json(brief):
    return json.dumps(plan_pre_sections(brief).to_dict(), sort_keys=True, default=str)


def _storyboard(brief, **kw):
    from daily_video.pre_storyboard import build_pre_storyboard
    return build_pre_storyboard(brief, plan_pre_sections(brief), **kw)


def _gift(minutes_old=7, day=PRE_DATE, pct=-0.6):
    ts = dt.datetime.combine(day, dt.time(7, 45), tzinfo=IST) - dt.timedelta(minutes=minutes_old)
    st, why = live_freshness(ts, PRE_DATE, AS_OF, "GIFT")
    return PreMarketQuote("GIFT", "GIFT NIFTY", "GIFT", QuoteKind.LIVE, 23900.0, pct, 24044.0, PREV,
                          ts.date(), ts, AS_OF, st, why, source="demo_fixture")


def _daily(rows):
    idx = pd.to_datetime([d for d, _ in rows])
    return pd.DataFrame({"Close": [c for _, c in rows]}, index=idx)


# --------------------------------------------------------------------------- 1. deterministic
@pytest.mark.parametrize("kind", SCENARIOS)
def test_planner_is_deterministic(kind):
    assert _plan_json(synthetic_brief(kind)) == _plan_json(synthetic_brief(kind))


def test_every_section_decision_has_a_reason():
    plan = plan_pre_sections(synthetic_brief("RISK_OFF"))
    for key in ("OVERNIGHT", "SETUP", "VIX", "FLOWS", "SECTORS", "EVENT", "STOCK_WATCH", "WATCH"):
        assert plan.reasons.get(key), key


# --------------------------------------------------------------------------- 2. no Gemini in sections
def test_planner_never_reaches_a_model():
    import ast
    tree = ast.parse(inspect.getsource(pre_plan))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for bad in ("news", "providers.gemini", "hooks.gemini", "requests", "yfinance", "market"):
        assert bad not in imported, bad
    assert not {"ask_gemini", "ai_pass", "plan_hook", "default_client"} & names


def test_hook_model_cannot_change_the_sections():
    brief = synthetic_brief("RISK_OFF")
    plan = plan_pre_sections(brief)
    calls = []

    def client(prompt, schema):
        calls.append(prompt)
        return json.dumps({"candidate_id": "pre-things", "archetype": "THINGS_TO_KNOW",
                           "hero_visual": "NUMBERED_LIST", "teaser_beats": ["GIFT_NIFTY", "PREV_NIFTY"],
                           "curiosity_line": "Skip the flows section today.",
                           "summary_line": "Before the bell: only the hook.", "fact_ids_used": []})

    sb = _storyboard(brief, hook_ai=True, hook_client=client)
    assert calls, "the fake model was offered the hook"
    body = [s.section for s in sb.scenes if s.section not in ("HOOK", "CLOSING")]
    assert body == plan.order


# --------------------------------------------------------------------------- 3. GIFT freshness
def test_fresh_gift_is_shown_with_its_time():
    brief = dataclasses.replace(synthetic_brief("QUIET"), gift=_gift(7))
    plan = plan_pre_sections(brief)
    assert plan.show_gift_nifty
    assert plan.overnight.gift.time_label == "7:38 AM IST"
    assert "at 7:38 AM IST" in plan.overnight.gift.sentence
    # GIFT's own move since its previous settlement - never "vs Nifty's close" (an opening gap)
    assert plan.overnight.gift.sentence == "GIFT Nifty was 0.60% lower at 7:38 AM IST"
    assert plan.overnight.gift.reference == "VS ITS MON SETTLEMENT"
    sb = _storyboard(brief)
    ov = next(s for s in sb.scenes if s.kind == "PRE_OVERNIGHT")
    assert ov.texts["gift"]["time"] == "AT 7:38 AM IST"
    assert ov.texts["gift"]["reference"] == "VS ITS MON SETTLEMENT"


@pytest.mark.parametrize("minutes_old,day", [(45, PRE_DATE), (5, PREV)])
def test_stale_gift_is_omitted(minutes_old, day):
    g = _gift(minutes_old, day)
    assert g.freshness is FreshnessStatus.STALE
    plan = plan_pre_sections(dataclasses.replace(synthetic_brief("QUIET"), gift=g))
    assert not plan.show_gift_nifty and plan.overnight.gift is None
    assert any(o["section"] == "GIFT NIFTY" and "STALE" in o["reason"] for o in plan.omitted)
    assert all(w.category != "GIFT" for w in plan.watch)


def test_gift_after_cutoff_is_rejected():
    st, _ = live_freshness(AS_OF + dt.timedelta(minutes=3), PRE_DATE, AS_OF, "GIFT")
    assert st is FreshnessStatus.FUTURE


def test_missing_gift_is_recorded_not_invented():
    plan = plan_pre_sections(synthetic_brief("QUIET"))
    assert not plan.show_gift_nifty
    assert any(o["section"] == "GIFT NIFTY" and "never taken from Gemini" in o["reason"]
               for o in plan.omitted)


# --------------------------------------------------------------------------- 4. VIX freshness
def test_stale_vix_is_omitted_everywhere():
    stale = VixReading(15.0, PREV, 12.0, dt.date(2026, 10, 1), 25.0, FreshnessStatus.STALE,
                       "reading from an older session")
    brief = dataclasses.replace(synthetic_brief("RISK_OFF"), vix=stale, stock_facts=[], flows=None)
    plan = plan_pre_sections(brief)
    assert "VIX" not in plan.order and plan.vix is None
    assert all(w.category != "VIX" for w in plan.watch)
    assert plan.reasons["VIX"].startswith("omitted: STALE")


def test_vix_reading_uses_canonical_dates_and_crosschecks():
    daily = _daily([("2026-09-22", 11.0), ("2026-09-23", 10.35), ("2026-09-24", 12.69)])
    v = vix_reading(daily, dt.date(2026, 9, 24), CAL, report_vix=12.69)
    assert v.fresh and v.previous_session == dt.date(2026, 9, 23)
    assert round(v.change_pct, 1) == 22.6
    bad = vix_reading(daily, dt.date(2026, 9, 24), CAL, report_vix=13.5)
    assert not bad.fresh and bad.validation_status == "CONFLICT"
    assert vix_reading(daily, dt.date(2026, 9, 25), CAL) is None       # no bar for that session


def test_large_vix_move_gets_its_own_scene_when_budget_allows():
    brief = dataclasses.replace(synthetic_brief("RISK_OFF"), stock_facts=[], flows=None)
    plan = plan_pre_sections(brief)
    assert "VIX" in plan.order
    assert plan.vix.headline == "India VIX closed 9.5% higher on Monday"


# --------------------------------------------------------------------------- 5/6/12. language
def _all_public_text():
    out = {}
    for kind in SCENARIOS:
        brief = synthetic_brief(kind)
        for k, v in _storyboard(brief).public_text().items():
            out[f"{kind}.{k}"] = v
    return out


def test_no_prediction_causal_or_recommendation_wording_anywhere():
    assert pre_language_issues(_all_public_text()) == []


def test_no_buy_sell_hold_words():
    for key, text in _all_public_text().items():
        assert not re.search(r"\b(buy|sell|hold|target|stop[- ]?loss)\b", text, re.I), (key, text)


@pytest.mark.parametrize("bad", [
    "Nifty will open lower", "GIFT Nifty points to a softer start", "Watch for continuation",
    "Likely to rally", "Oil fell, so airlines rose", "IT fell because Nasdaq fell",
    "Nasdaq dropped after the Fed decision", "BUY the dip", "Hold your positions",
    "Stock X is set to break out"])
def test_language_guard_rejects(bad):
    assert pre_language_issues({"x": bad})


def test_overnight_takeaway_never_links_markets():
    for kind in SCENARIOS:
        ov = plan_pre_sections(synthetic_brief(kind)).overnight
        for s in (ov.headline, ov.takeaway):
            assert not re.search(r"\b(because|after|as|so|while|amid|points? to|lead to)\b", s), s


# --------------------------------------------------------------------------- 7. labels
def test_tuesday_setup_is_labelled_yesterday_with_the_day_named():
    plan = plan_pre_sections(synthetic_brief("QUIET"))
    assert plan.labels["SETUP"] == "YESTERDAY'S SETUP"
    assert plan.setup.headline.endswith("on Monday")
    assert plan.labels["FLOWS"].endswith("· MON")


def test_monday_setup_names_friday_never_yesterday():
    mon, fri = dt.date(2026, 10, 12), dt.date(2026, 10, 9)
    brief = dataclasses.replace(synthetic_brief("RISK_OFF"), pre_date=mon, previous_session=fri)
    plan = plan_pre_sections(brief)
    assert plan.labels["SETUP"] == "FRIDAY'S SETUP"
    assert "Friday" in plan.setup.headline and "yesterday" not in plan.setup.headline.lower()
    for w in plan.watch:
        assert "Monday" not in w.title + w.note
    sb = _storyboard(brief)
    labels = [lab for _, lab, _, _ in sb.sections()]
    assert "FRIDAY'S SETUP" in labels and not any("YESTERDAY" in lab for lab in labels)
    assert sb.date_label == "MON 12 OCT 2026" and sb.kicker == "BEFORE THE BELL"


# --------------------------------------------------------------------------- 8. events
def test_only_verified_events_are_returned():
    tentative = [e for e in OFFICIAL_EVENTS if e.validation_status is EventValidation.TENTATIVE]
    assert tentative
    for e in tentative:
        assert scheduled_events_for(e.event_date, CAL, 1) == [] or all(
            x.validation_status is not EventValidation.TENTATIVE
            for x in scheduled_events_for(e.event_date, CAL, 1))


def test_fomc_event_has_verified_date_and_no_invented_time():
    evs = scheduled_events_for(dt.date(2026, 10, 28), CAL, expiry_weekday=1)
    fed = [e for e in evs if e.event_type == "FED"]
    assert len(fed) == 1 and fed[0].event_time is None
    assert fed[0].time_label.startswith("US time")
    assert "federalreserve.gov" in fed[0].source and fed[0].verified_on


def test_expiry_rule_uses_the_calendar():
    assert expiry_event(dt.date(2026, 9, 22), CAL, 1).event_name == "Nifty weekly F&O expiry"
    assert expiry_event(dt.date(2026, 9, 29), CAL, 1).event_name == "Nifty monthly F&O expiry"
    assert expiry_event(dt.date(2026, 9, 29), CAL, 1).importance is Importance.HIGH
    assert expiry_event(dt.date(2026, 9, 23), CAL, 1) is None           # not the weekday
    # a holiday on the expiry weekday: the exchange's move is never guessed
    assert expiry_event(dt.date(2026, 11, 24), CAL, 1) is None


def test_news_headlines_are_never_today_event():
    brief = dataclasses.replace(synthetic_brief("QUIET"),
                                news_events=[{"tag": "RBI", "text": "RBI keeps repo rate unchanged"}])
    plan = plan_pre_sections(brief)
    assert plan.event is None and "EVENT" not in plan.order
    assert any("not a verified schedule" in o["reason"] for o in plan.omitted)


def test_unverified_event_cannot_reach_the_plan():
    ev = ScheduledEvent("Rumoured policy move", PRE_DATE, None, "", "RBI", "RBI", "a headline",
                        Importance.HIGH, EventValidation.UNVERIFIED)
    assert not ev.showable
    report_like = synthetic_brief("QUIET")
    assert [e for e in [ev] if e.showable] == []
    assert plan_pre_sections(report_like).event is None


def test_event_card_carries_time_and_source():
    plan = plan_pre_sections(synthetic_brief("EVENT"))
    assert plan.event.time_label == "10:00 AM IST"
    assert plan.event.source_label == "Source: synthetic fixture"
    assert plan.watch[1].category == "EVENT" and plan.watch[1].note == "10:00 AM IST"


# --------------------------------------------------------------------------- 9/10/11. optional
def test_flows_can_disappear():
    for flows in (None, {"fii": -300.0, "dii": 250.0}):
        plan = plan_pre_sections(dataclasses.replace(synthetic_brief("RISK_OFF"), flows=flows))
        assert "FLOWS" not in plan.order and plan.flows is None


def test_stock_watch_can_disappear():
    plan = plan_pre_sections(dataclasses.replace(synthetic_brief("RISK_OFF"), stock_facts=[]))
    assert "STOCK_WATCH" not in plan.order
    assert "no speculative overnight stock selection" in plan.reasons["STOCK_WATCH"]


def test_at_most_two_stock_watch_items():
    base = synthetic_brief("RISK_OFF")
    many = [dict(base.stock_facts[0], symbol=f"STOCK-{c}") for c in "ABCDE"]
    plan = plan_pre_sections(dataclasses.replace(base, stock_facts=many))
    assert len(plan.stock_watch.items) == 2
    assert [i["symbol"] for i in plan.stock_watch.items] == ["STOCK-A", "STOCK-B"]


def test_optional_budget_is_two():
    brief = dataclasses.replace(synthetic_brief("RISK_OFF"))
    plan = plan_pre_sections(brief)
    optional = [k for k in plan.order if k in pre_plan.OPTIONAL_PRIORITY]
    assert len(optional) <= pre_plan.OPTIONAL_BUDGET


def test_watch_cards_are_two_or_three_facts_never_actions():
    for kind in SCENARIOS:
        plan = plan_pre_sections(synthetic_brief(kind))
        assert 1 <= len(plan.watch) <= 3       # a quiet morning may have a single honest card
        assert plan.watch[0].category == "NIFTY_LEVEL"
        cats = [w.category for w in plan.watch]
        assert len(cats) == len(set(cats))


# --------------------------------------------------------------------------- 13. degraded
def test_all_global_cues_stale_drops_overnight_but_still_plans():
    brief = synthetic_brief("QUIET")
    stale = [dataclasses.replace(q, freshness=FreshnessStatus.STALE, freshness_reason="old")
             for q in brief.global_cues]
    plan = plan_pre_sections(dataclasses.replace(brief, global_cues=stale))
    assert "OVERNIGHT" not in plan.order and plan.order[0] == "SETUP"
    assert sum(1 for o in plan.omitted if o["section"] == "OVERNIGHT") == len(stale)
    sb = _storyboard(dataclasses.replace(brief, global_cues=stale))
    assert sb.total_duration > 0


def _report(session, close=24000.0, pct=0.5):
    from core import MarketReport
    r = MarketReport(report_id="t", report_type="PRE_MARKET", report_date=session,
                     session_date=session)
    r.nifty = {"close": close, "change_pct": pct, "open": close, "high": close + 10,
               "low": close - 10, "previous_close": close / (1 + pct / 100)}
    return r


def test_blocked_when_report_is_for_the_wrong_session():
    from providers.premarket import PreMarketAcquisition
    with pytest.raises(PreMarketBlocked) as e:
        build_pre_brief(_report(dt.date(2026, 9, 21)), dt.date(2026, 9, 23),
                        dt.datetime(2026, 9, 23, 7, 45, tzinfo=IST), CAL, PreMarketAcquisition(), [])
    assert e.value.code == "PREVIOUS_SESSION_MISMATCH"


def test_blocked_without_previous_nifty_or_on_a_holiday():
    from providers.premarket import PreMarketAcquisition
    r = _report(dt.date(2026, 9, 24))
    r.nifty = {}
    with pytest.raises(PreMarketBlocked) as e:
        build_pre_brief(r, dt.date(2026, 9, 25), dt.datetime(2026, 9, 25, 7, 45, tzinfo=IST), CAL,
                        PreMarketAcquisition(), [])
    assert e.value.code == "PREVIOUS_NIFTY_MISSING"
    with pytest.raises(PreMarketBlocked) as e:
        build_pre_brief(r, dt.date(2026, 10, 2), dt.datetime(2026, 10, 2, 7, 45, tzinfo=IST), CAL,
                        PreMarketAcquisition(), [])
    assert e.value.code == "NOT_A_SESSION"


def test_real_brief_blocks_without_a_canonical_report(tmp_path):
    from products.premarket import build_real_brief
    with pytest.raises(PreMarketBlocked) as e:
        build_real_brief(dt.date(2026, 9, 24), dt.datetime(2026, 9, 24, 7, 45, tzinfo=IST),
                         history_fn=lambda *a: None, db_path=str(tmp_path / "empty.db"))
    assert e.value.code == "PREVIOUS_SESSION_MISSING"


def test_one_failing_market_never_costs_the_others():
    def history_fn(ticker, start, end, interval):
        if ticker == "^IXIC":
            raise RuntimeError("feed down")
        if interval == "5m":
            return pd.DataFrame({"Close": []}, index=pd.DatetimeIndex([], tz="UTC"))
        return _daily([("2026-10-02", 100.0), ("2026-10-05", 101.0)])
    acq = fetch_premarket_quotes(PRE_DATE, AS_OF, PREV, CAL, history_fn=history_fn)
    names = [q.name for q in acq.global_cues]
    assert "NASDAQ" not in names and "S&P 500" in names
    assert any("NASDAQ: fetch failed" in line for line in acq.log)
    assert acq.gift is None and any("GIFT NIFTY: no approved non-AI source" in l for l in acq.log)


# --------------------------------------------------------------------------- provider readers
def test_us_close_never_reads_the_pre_date_bar():
    daily = _daily([("2026-10-01", 100.0), ("2026-10-02", 101.0), ("2026-10-05", 102.0),
                    ("2026-10-06", 150.0)])
    q = us_close_quote("SP500", "S&P 500", "^GSPC", daily, PRE_DATE, AS_OF, AS_OF)
    assert q.market_date == dt.date(2026, 10, 5) and q.fresh
    assert round(q.change_pct, 4) == round((102 / 101 - 1) * 100, 4)


def test_us_close_stale_on_a_us_holiday_and_monday_uses_friday():
    daily = _daily([("2026-10-01", 100.0), ("2026-10-02", 101.0)])        # no Monday bar
    q = us_close_quote("SP500", "S&P 500", "^GSPC", daily, PRE_DATE, AS_OF, AS_OF)
    assert q.freshness is FreshnessStatus.STALE and not q.fresh
    assert expected_us_session(dt.date(2026, 10, 12)) == dt.date(2026, 10, 9)
    st, _ = us_close_freshness(dt.date(2026, 10, 5), PRE_DATE,
                               dt.datetime(2026, 10, 6, 1, 0, tzinfo=IST))
    assert st is FreshnessStatus.IN_PROGRESS                               # before the US close


def test_live_quote_excludes_a_bar_that_ends_after_the_cutoff():
    idx = pd.DatetimeIndex(["2026-10-06 07:35", "2026-10-06 07:40", "2026-10-06 07:45"]).tz_localize(IST)
    intra = pd.DataFrame({"Close": [99.0, 98.0, 50.0]}, index=idx)
    daily = _daily([("2026-10-02", 100.0), ("2026-10-05", 100.0)])
    q = live_quote("HSI", "HANG SENG", "^HSI", "ASIA", intra, daily, PRE_DATE, AS_OF, AS_OF)
    assert q.value == 98.0 and q.market_timestamp.strftime("%H:%M") == "07:45"
    assert q.fresh and round(q.change_pct, 2) == -2.0


def test_live_quote_from_a_closed_market_is_not_shown():
    idx = pd.DatetimeIndex(["2026-10-05 11:55"]).tz_localize(IST)            # yesterday's last bar
    intra = pd.DataFrame({"Close": [98.0]}, index=idx)
    q = live_quote("N225", "NIKKEI 225", "^N225", "ASIA", intra,
                   _daily([("2026-10-02", 100.0), ("2026-10-05", 98.0)]), PRE_DATE, AS_OF, AS_OF)
    assert q.freshness is FreshnessStatus.STALE and not q.fresh


# --------------------------------------------------------------------------- 14. POST unchanged
def test_post_storyboard_contract_unchanged():
    from daily_video.storyboard import RADAR_PUBLISH_LIMIT, SECTION_LABELS, SceneSpec, Storyboard
    from presentation.post_plan import OPTIONAL_BUDGET, POST_PLAN_VERSION
    assert POST_PLAN_VERSION == "post-3.0" and OPTIONAL_BUDGET == 2 and RADAR_PUBLISH_LIMIT == 3
    assert SECTION_LABELS["PULSE"] == "MARKET PULSE" and SECTION_LABELS["FLOWS"] == "FII / DII"
    sb = Storyboard(dt.date(2026, 9, 24), "THU 24 SEP 2026", "SESSION RECAP",
                    [SceneSpec("PULSE", "PULSE", 5.4), SceneSpec("FLOWS", "FLOWS", 5.5)])
    assert [s[1] for s in sb.sections()] == ["MARKET PULSE", "FII / DII"]
    assert "pre_plan" not in sb.to_dict() and "section_labels" not in sb.to_dict()


def test_router_keeps_postmarket_the_default():
    class Args:
        pass
    assert VideoRequest.from_args(Args()).mode == "postmarket"
    seen = []
    route(VideoRequest(), Args(), postmarket_runner=lambda a: seen.append(a) or "post")
    assert len(seen) == 1
    with pytest.raises(ValueError):
        route(VideoRequest(mode="weekly"), None, postmarket_runner=lambda a: None)


def test_premarket_refuses_upload():
    from products.premarket import run_premarket
    assert run_premarket(VideoRequest(mode="premarket", upload=True)) is None


# --------------------------------------------------------------------------- 15. shared components
def test_pre_reuses_the_post_scene_classes_and_chrome():
    from daily_video.composer import SCENE_CLASSES, build_scenes
    from daily_video.market_scenes import MarketPulseScene, QuickCloseScene, SectorBoardScene
    sb = _storyboard(synthetic_brief("RISK_ON"))
    kinds = [s.kind for s in sb.scenes]
    assert kinds[0] == "DYNAMIC_HOOK" and kinds[-1] == "CLOSING"
    assert SCENE_CLASSES["SECTORS"] is SectorBoardScene and SCENE_CLASSES["CLOSING"] is QuickCloseScene
    assert SCENE_CLASSES["PULSE"] is MarketPulseScene
    assert len(build_scenes(sb)) == len(sb.scenes)


def test_hook_uses_pre_mode_and_promises_only_real_sections():
    for kind in SCENARIOS:
        sb = _storyboard(synthetic_brief(kind))
        hp = sb.hook_plan
        assert hp["mode"] == "PRE_MARKET" and hp["source"] == "DETERMINISTIC"
        assert hp["summary_line"].startswith("Before the bell:")
        if "GIFT" in hp["summary_line"]:
            assert any(s.kind == "PRE_OVERNIGHT" and "gift" in s.texts for s in sb.scenes)


def test_live_cue_hook_line_carries_its_time():
    from hooks import plan_hook, pre_market_sheet
    from hooks.sheet_pre import pre_market_inputs_from_brief
    brief = synthetic_brief("RISK_OFF")
    cues = [q for q in brief.global_cues if q.region == "ASIA"]
    brief = dataclasses.replace(brief, global_cues=[dataclasses.replace(cues[0], change_pct=-3.1)])
    plan = plan_pre_sections(brief)
    hp = plan_hook(pre_market_sheet(pre_market_inputs_from_brief(brief, plan)), use_ai=False)
    assert hp.archetype.value == "OVERNIGHT_CUE"
    assert re.fullmatch(r"(Japan's )?Nikkei( 225)? was 3\.10% lower at 7:40 AM\.",
                        hp.curiosity_line), hp.curiosity_line


# --------------------------------------------------------------------------- 16. runtime
@pytest.mark.parametrize("kind", SCENARIOS)
def test_runtime_within_ceiling(kind):
    assert _storyboard(synthetic_brief(kind)).total_duration <= MAX_RUNTIME


def test_runtime_with_every_section_qualifying():
    base = synthetic_brief("RISK_OFF")
    ev = synthetic_brief("EVENT").events
    many = [dict(base.stock_facts[0], symbol=f"STOCK-{c}") for c in "AB"]
    brief = dataclasses.replace(base, events=ev, stock_facts=many, gift=_gift(5))
    sb = _storyboard(brief)
    assert sb.total_duration <= MAX_RUNTIME
    assert 45.0 <= sb.total_duration


# --------------------------------------------------------------------------- 17. safe area / text QA
@pytest.mark.parametrize("brief_fn", [
    lambda: synthetic_brief("EVENT"),
    lambda: dataclasses.replace(synthetic_brief("RISK_OFF"), gift=_gift(5)),
    lambda: dataclasses.replace(synthetic_brief("RISK_OFF"), stock_facts=[], flows=None),
])
def test_every_pre_scene_passes_freeze_frame_qa(brief_fn):
    from daily_video import Composer
    sb = _storyboard(brief_fn())
    comp = Composer(sb)
    for i, spec in enumerate(sb.scenes):
        _, rec, qa = comp.freeze(i)
        assert qa["passed"], (spec.kind, qa["issues"])
        drawn = {tb.text for tb in rec.texts}
        declared = set(spec.public_text("x").values()) | {
            lab for _, lab, _, _ in sb.sections()}
        # every body-scene string drawn is declared (chrome: brand, date, kicker, disclaimer)
        if spec.kind.startswith("PRE_"):
            undeclared = {t for t in drawn if t not in declared and not any(t in d for d in declared)}
            undeclared -= {"DAILY MARKET BYTE", "For information only - not investment advice",
                           str(i + 1) if False else ""}
            undeclared = {t for t in undeclared if not re.fullmatch(r"\d", t)}
            assert not undeclared, (spec.kind, undeclared)


# --------------------------------------------------------------------------- PRE V1 final polish
def _hook(brief):
    from hooks import plan_hook, pre_market_sheet
    from hooks.sheet_pre import pre_market_inputs_from_brief
    plan = plan_pre_sections(brief)
    sheet = pre_market_sheet(pre_market_inputs_from_brief(brief, plan))
    return plan, sheet, plan_hook(sheet, use_ai=False)


def _live_lead_brief():
    """25 Sep shape: a live Asian cue leads, a US close and a contrasting Asian cue support."""
    b = synthetic_brief("RISK_OFF")
    cues = {q.key: q for q in b.global_cues}
    return dataclasses.replace(b, gift=None, global_cues=[
        dataclasses.replace(cues["HANGSENG"], change_pct=-1.73),
        dataclasses.replace(cues["NIKKEI"], change_pct=1.12),
        dataclasses.replace(cues["DOW"], change_pct=-0.31),
        dataclasses.replace(cues["SP500"], change_pct=-0.02),
        dataclasses.replace(cues["NASDAQ"], change_pct=0.01)])


@pytest.mark.parametrize("brief_fn", [_live_lead_brief, lambda: synthetic_brief("RISK_OFF"),
                                      lambda: synthetic_brief("EVENT")])
def test_curiosity_entity_is_the_first_teaser_beat(brief_fn):
    from hooks.engine import beat_entities, curiosity_entity
    _, sheet, hp = _hook(brief_fn())
    ent = curiosity_entity(sheet, hp.curiosity_line)
    assert ent is not None
    assert ent in beat_entities(sheet, hp.teaser_beats[0]), (hp.curiosity_line, hp.teaser_beats)


def test_live_lead_hook_matches_the_25_sep_shape():
    _, sheet, hp = _hook(_live_lead_brief())
    assert hp.curiosity_line == "Hang Seng was 1.73% lower at 7:40 AM."   # fixture reading time
    assert hp.teaser_beats == ["CUE:HANG_SENG", "CUE:NIKKEI_225", "CUE:DOW_JONES"]
    assert hp.hero_visual.value == "OVERNIGHT_BOARD" and hp.hero_payload["lead"]["name"] == "HANG SENG"


@pytest.mark.parametrize("brief_fn", [_live_lead_brief] + [
    (lambda k=k: synthetic_brief(k)) for k in SCENARIOS])
def test_no_teaser_entity_repeats(brief_fn):
    from hooks.engine import beat_entities
    _, sheet, hp = _hook(brief_fn())
    seen = set()
    for bid in hp.teaser_beats:
        ents = beat_entities(sheet, bid)
        assert not (ents & seen), (bid, hp.teaser_beats)
        seen |= ents
    assert len(hp.teaser_beats) == len(set(hp.teaser_beats))


def test_curiosity_entity_without_a_visual_leaves_beats_alone():
    from hooks.engine import lead_beat_first
    _, sheet, hp = _hook(_live_lead_brief())
    beats = ["CUE:NIKKEI_225", "CUE:DOW_JONES", "PREV_NIFTY"]
    sheet.beats = [b for b in sheet.beats if b.beat_id != "CUE:HANG_SENG"]
    assert lead_beat_first(sheet, hp.curiosity_line, beats) == beats
    # a line that names no entity ("Two things before the bell.") changes nothing either
    assert lead_beat_first(sheet, "Two things before the bell.", beats) == beats


def _phrase_positions(summary, phrases):
    return [summary.index(p) for p in phrases if p in summary]


def test_pre_summary_follows_the_plan_order():
    brief = dataclasses.replace(_live_lead_brief())
    plan, sheet, hp = _hook(brief)
    assert plan.order.index("OVERNIGHT") < plan.order.index("SETUP") < plan.order.index("FLOWS")
    assert hp.summary_line == ("Before the bell: overnight cues, the previous session and "
                               "FII/DII flows.")
    pos = _phrase_positions(hp.summary_line, ["overnight cues", "the previous session", "FII/DII"])
    assert pos == sorted(pos) and len(pos) == 3
    for kind in SCENARIOS:
        plan, sheet, hp = _hook(synthetic_brief(kind))
        order = [s for s in sheet.sections]
        from hooks.candidates import _section_phrase
        said = [(hp.summary_line.index(ph), sec) for sec in order
                if (ph := _section_phrase(sec, sheet)) and ph in hp.summary_line]
        assert [sec for _, sec in sorted(said)] == [sec for _, sec in said], hp.summary_line


def test_omitted_sections_are_never_promised():
    brief = dataclasses.replace(_live_lead_brief(), flows=None, stock_facts=[])
    plan, sheet, hp = _hook(brief)
    assert "FLOWS" not in plan.order and "FII" not in hp.summary_line
    assert "EVENT" not in plan.order and "event" not in hp.summary_line.lower()
    assert "GIFT" not in hp.summary_line                        # no GIFT reading today
    for sec in sheet.sections:
        assert sec in ("GLOBAL", "GIFT", "PREV", "FLOWS", "VIX", "SECTORS", "EVENTS", "STOCKS",
                       "WATCH")


def test_quiet_pre_may_be_short_and_is_not_padded():
    brief = synthetic_brief("QUIET")
    plan = plan_pre_sections(brief)
    sb = _storyboard(brief)
    assert sb.total_duration < 45.0
    assert plan.order == ["OVERNIGHT", "SETUP", "WATCH"]
    # the +0.31% "strongest sector" card is below the value threshold: not shown to fill time
    assert all(w.category != "SECTOR" for w in plan.watch)
    assert all(abs(float(s["pct"])) < pre_plan.SECTOR_WATCH_MIN_PCT for s in brief.sectors)


def test_sector_watch_card_kept_when_it_carries_a_real_move():
    plan = plan_pre_sections(dataclasses.replace(synthetic_brief("RISK_OFF")))
    assert any(w.category == "SECTOR" and "-1.92%" in w.title for w in plan.watch)


def test_every_stock_card_names_the_previous_session():
    for kind in ("RISK_OFF", "EVENT"):
        brief = synthetic_brief(kind)
        sb = _storyboard(brief)
        sc = next(s for s in sb.scenes if s.kind == "PRE_STOCKS")
        wd = brief.prev_weekday
        assert sc.headline.endswith(f"on {wd}") and sb.section_labels["STOCK_WATCH"].endswith(
            brief.previous_session.strftime("%a").upper())
        for it in sc.texts["items"]:
            assert it["line"].startswith(f"{wd}: ")
            assert it["day"] == f"{wd.upper()}'S CLOSE"


def test_post_hook_beats_are_not_reordered():
    from hooks import plan_hook, post_market_sheet
    from hooks import fixtures as F
    from hooks.candidates import build_candidates
    from hooks.engine import lead_beat_first
    for make in (F.post_quiet, F.post_sector_contrast, F.post_radar_unusual, F.post_big_move):
        fx = make()
        sheet = post_market_sheet(fx["plan"], fx["pres"], fx["stories"], fx["evidence"],
                                  fx["sections"], fx["universe"])
        hp = plan_hook(sheet, use_ai=False)
        top = build_candidates(sheet)[0]
        assert hp.teaser_beats == list(top.default_beats)
        assert lead_beat_first(sheet, hp.curiosity_line, hp.teaser_beats) == hp.teaser_beats

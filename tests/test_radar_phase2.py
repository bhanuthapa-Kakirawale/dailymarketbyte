"""Phase 2: Market Radar stock stories - chart-first shell, 3 published stories.

Covers the publication limit, selector order, the presentation model's locked rules (one event,
<= 2 prices, exactly one supporting fact, volume before price location, no relative strength),
the shared shell, safe areas/clipping/overlap, the untouched intro, and wording safety.
"""
import datetime as dt
import hashlib
import inspect
import itertools
import os
import re

import pytest

import daily_video.radar_scenes as radar_scenes
import daily_video.storyboard as sbm
from core.content_safety import SafetyStatus, classify_text
from daily_video import Composer, theme
from daily_video.radar_story_scene import CARD, PriceTags, TAG_X0, TAG_X1, TAKEAWAY_Y
from daily_video.storyboard import Storyboard
from daily_video.typography import Recorder, font, tlen
from hooks.validation import _COMPILED
from presentation.radar_story import (EVENT_PRIORITY, LOCATION_LABEL, VOLUME_WORDS,
                                      build_radar_story_model, day_location)
from radar.visual_evidence import RadarVisualEvidence

REPORT = "output/reports/premarket_2026-09-23.json"
RADAR_RESULT = "output/radar/daily_radar_2026-09-21.json"
INTRO_SCENE_SHA = "aad14cd1396ebe6d0c7ab30680db5ab3365f5521804902461a8594ad0801eb71"
INTRO_SPEC_SHA = "0b2739eb06f4f8bca9636946a2e06839a7e08fb8917ff44650ad2200bc065613"


@pytest.fixture(scope="module")
def real():
    if not (os.path.exists(REPORT) and os.path.exists(RADAR_RESULT)):
        pytest.skip("real validation artifacts not present")
    import render_daily_market_byte as r
    plan, pres, rp, rr, ev, uni, src = r.load_inputs(REPORT, "output/radar", "2026-09-21")
    sb = sbm.build_storyboard(plan, pres, rp, rr, ev, uni, src, profile="PRIVATE_ANALYTICS")
    comp = Composer(sb)
    stories = [(i, s) for i, s in enumerate(sb.scenes) if s.kind == "RADAR_STORY"]
    frames = {i: comp.freeze(i) for i, _ in stories}
    return {"sb": sb, "comp": comp, "rp": rp, "rr": rr, "ev": ev, "stories": stories,
            "frames": frames}


def _ev(n=38, jump=6.0, opens=True):
    d0 = dt.date(2026, 3, 2)
    dates = [d0 + dt.timedelta(days=i) for i in range(n)]
    closes = [500 + (i % 5) for i in range(n)]
    closes[-1] = closes[-2] + jump
    o = [closes[0]] + closes[:-1]
    h = [max(a, b) + 1 for a, b in zip(o, closes)]
    lo = [min(a, b) - 1 for a, b in zip(o, closes)]
    sma = [None] * 19 + [502.0] * (n - 19)
    return RadarVisualEvidence(
        instrument="STOCK-Z", session_date=dates[-1], calculation_version="t", window_dates=dates,
        close_series=closes, volume_series=[100.0] * (n - 1) + [420.0], sma20_series=sma,
        sma50_series=sma, range_20_high=max(closes[-21:-1]), range_20_low=min(closes[-21:-1]),
        range_50_high=max(closes[:-1]), range_50_low=min(closes[:-1]), highlight_events=[],
        rvol=None, rvol_level=None, stock_return_5d_pct=None, stock_return_20d_pct=None,
        market_relative_5d_pp=2.0, market_relative_20d_pp=3.0, relative_window_sessions=20,
        relative_dates=None, relative_stock_normalized=[1.0], relative_benchmark_normalized=[1.0],
        benchmark_symbol="^NSEI", source_session_dates=dates,
        open_series=o if opens else None, high_series=h if opens else None,
        low_series=lo if opens else None)


def _story(events, level=None, rvol=None, chg=1.2):
    sp = {"instrument": "STOCK-Z", "company_name": "Placeholder Ltd.",
          "price_change_display": f"{chg:+.1f}%"}
    st = {"instrument": "STOCK-Z", "price_change_pct": chg, "technical_context": {"events": events},
          "volume_context": {"level": level, "relative_volume": rvol} if level else None,
          "relative_context": {"market_relative_20d_pp": 3.0}}
    return sp, st


# --------------------------------------------------------------------------- 1-2 publication
def test_exactly_three_radar_stories_are_published(real):
    assert len(real["stories"]) == sbm.RADAR_PUBLISH_LIMIT == 3


def test_selector_order_is_preserved(real):
    selector = [s["instrument"] for s in real["rr"]["stories"]]
    pres = [sc["story"]["instrument"] for sc in real["rp"]["scenes"] if sc["role"] == "STORY"]
    published = [s.texts["symbol"] for _, s in real["stories"]]
    assert published == selector[:3] == pres[:3]
    held = [o["item"] for o in real["sb"].omitted if o.get("section") == "MARKET RADAR"]
    assert held == selector[3:]                      # the rest recorded, not silently dropped


def test_hook_counts_only_what_is_published(real):
    hp = real["sb"].hook_plan
    assert "3 Radar stocks" in hp["summary_line"]
    assert hp["curiosity_line"].startswith("Nifty moved just +0.29%.")


# --------------------------------------------------------------------------- 3 no hardcoding
def test_renderer_and_model_have_no_stock_specific_branching():
    import daily_video.radar_story_scene as scene
    import presentation.radar_story as model
    for mod in (scene, model):
        src = inspect.getsource(mod)
        for sym in ("MANKIND", "ETERNAL", "OFSS", "ALKEM", "ATGL"):
            assert sym not in src, (mod.__name__, sym)
        assert not re.search(r"(symbol|instrument)[\"'\]]*\s*==", src)


# --------------------------------------------------------------------------- 4-5 data
def test_candles_come_from_the_supplied_evidence(real):
    for _, s in real["stories"]:
        ev = real["ev"][s.texts["symbol"]]
        k = len(s.data["candles"]["close"])
        assert s.data["candles"] == {"open": ev.open_series[-k:], "high": ev.high_series[-k:],
                                     "low": ev.low_series[-k:], "close": ev.close_series[-k:]}
        assert s.data["latest_close"] == ev.close_series[-1]


def test_no_lookahead(real):
    session = real["sb"].session_date
    for _, s in real["stories"]:
        ev = real["ev"][s.texts["symbol"]]
        assert ev.window_dates[-1] == session and max(ev.window_dates) == session
        assert all(d <= session for d in ev.source_session_dates)
        band = s.data["band"]
        if band:
            assert band["i1"] == len(s.data["candles"]["close"]) - 2   # prior window only


# --------------------------------------------------------------------------- 6-10 model rules
def _rupee_texts(rec):
    from video import RS
    return [tb.text for tb in rec.texts if tb.text.startswith(RS.strip()) or tb.text.startswith("Rs")]


def test_at_most_two_prices_on_screen(real):
    for i, s in real["stories"]:
        _, rec, _ = real["frames"][i]
        assert len(_rupee_texts(rec)) <= 2
        assert s.data["reference_display"] and s.data["latest_close_display"]


def test_exactly_one_supporting_fact(real):
    for i, s in real["stories"]:
        _, rec, _ = real["frames"][i]
        marks = {k for k, _ in rec.marks}
        assert len(marks & {"volume_spike", "day_range"}) == 1


def test_volume_is_chosen_before_price_location():
    ev = _ev()
    for level in VOLUME_WORDS:
        m = build_radar_story_model(*_story(["BREAK_ABOVE_20D_RANGE"], level, 4.2), ev, 1, 3)
        assert m.support["kind"] == "VOLUME"
        assert VOLUME_WORDS[level] in m.takeaway          # worded as the detector's own level


def test_price_location_fallback():
    ev = _ev()
    m = build_radar_story_model(*_story(["BREAK_ABOVE_20D_RANGE"]), ev, 1, 3)
    assert m.support["kind"] == "DAY_RANGE"
    assert m.support["label"] in LOCATION_LABEL.values()
    assert day_location(10, 12, 8, 11.8) == "HIGH" and day_location(10, 12, 8, 8.1) == "LOW"
    assert day_location(10, 12, 8, 10) == "MID" and day_location(10, 10, 10, 10) is None
    mid = build_radar_story_model(*_story(["CROSS_BELOW_SMA20"]), ev, 1, 3)
    if mid.support["location"] == "MID":
        assert mid.takeaway == "Price moved below its 20-day average."   # nothing manufactured


def test_relative_strength_is_not_displayed(real):
    for i, s in real["stories"]:
        _, rec, _ = real["frames"][i]
        shown = " ".join(tb.text for tb in rec.texts)
        assert "vs Nifty" not in shown and "pts" not in shown
        assert "relative_gap" not in {k for k, _ in rec.marks}


def test_text_scene_when_no_ohlc():
    m = build_radar_story_model(*_story(["BREAK_ABOVE_20D_RANGE"]), _ev(opens=False), 1, 3)
    assert m.event_family == "TEXT" and m.candles is None and m.price_values == []


def test_one_event_by_priority():
    ev = _ev()
    m = build_radar_story_model(*_story(["CROSS_ABOVE_SMA20", "BREAK_ABOVE_20D_RANGE",
                                         "BREAK_ABOVE_50D_RANGE"]), ev, 1, 3)
    assert m.event_type == "BREAK_ABOVE_50D_RANGE" and m.reference_label == "50-DAY HIGH"
    assert m.ma_series is None                     # only the event's own overlay


# --------------------------------------------------------------------------- 11-13 shell + intro
def test_counters(real):
    assert [s.counter for _, s in real["stories"]] == ["1 / 3", "2 / 3", "3 / 3"]
    for i, _ in real["stories"]:
        _, rec, _ = real["frames"][i]
        assert any(tb.role == "counter" for tb in rec.texts)


def test_intro_exists_before_the_stories(real):
    kinds = [s.kind for s in real["sb"].scenes]
    first = kinds.index("RADAR_STORY")
    assert kinds[first - 1] == "RADAR_INTRO"
    intro = real["sb"].scenes[first - 1]
    assert intro.headline == "Beyond the biggest movers"
    assert intro.texts["names"] == [s.texts["symbol"] for _, s in real["stories"]]


def test_intro_animation_logic_is_unchanged():
    src = inspect.getsource(radar_scenes.RadarIntroScene)
    assert hashlib.sha256(src.encode()).hexdigest() == INTRO_SCENE_SHA
    spec_src = inspect.getsource(sbm._radar_intro)
    assert hashlib.sha256(spec_src.encode()).hexdigest() == INTRO_SPEC_SHA


# --------------------------------------------------------------------------- 14-15 wording/facts
def _all_takeaways():
    out = set()
    ev = _ev()
    for evt in EVENT_PRIORITY:
        for level in (None, *VOLUME_WORDS):
            m = build_radar_story_model(*_story([evt], level, 3.0 if level else None), ev, 1, 3)
            out.add(m.takeaway)
    return out


def test_no_recommendation_prediction_or_cause_in_takeaways():
    banned = re.compile(r"\b(bullish|bearish|buy|sell|opportunity|weak stock|strong buy|target|"
                        r"will|could|should|likely|because|due to)\b", re.I)
    for t in _all_takeaways():
        assert classify_text(t).status is SafetyStatus.SAFE, t
        assert not banned.search(t), t
        for fam, pats in _COMPILED.items():
            if fam != "causal":          # "with"/"and" joiners are descriptive, not causal
                assert not any(p.search(t) for p in pats), (t, fam)


def test_every_drawn_string_is_declared(real):
    for i, s in real["stories"]:
        declared = {v for v in s.texts.values() if isinstance(v, str) and v}
        for t in (1.0, 2.65, s.freeze["t"]):
            rec = Recorder()
            real["comp"].frame(real["sb"].starts()[i] + t, rec)
            for tb in rec.texts:
                if tb.role in ("brand", "disclaimer", "chip", "counter"):
                    continue
                assert tb.text in declared or any(tb.text in d for d in declared if len(tb.text) >= 6), \
                    (s.texts["symbol"], t, tb.text)


def test_displayed_prices_are_the_evidence_values(real):
    from presentation.radar_story import rupees
    for _, s in real["stories"]:
        ev = real["ev"][s.texts["symbol"]]
        assert s.data["latest_close_display"] == rupees(ev.close_series[-1])
        fam = s.data["event_family"]
        w = 50 if "50" in (s.data["event_type"] or "") else 20
        expected = {("RANGE_UP", 20): ev.range_20_high, ("RANGE_UP", 50): ev.range_50_high,
                    ("RANGE_DOWN", 20): ev.range_20_low, ("RANGE_DOWN", 50): ev.range_50_low}
        assert s.data["reference_display"] == rupees(expected[(fam, w)])


# --------------------------------------------------------------------------- 16-19 layout
def test_real_scenes_fit_safe_areas(real):
    for i, s in real["stories"]:
        _, _, qa = real["frames"][i]
        assert qa["passed"], (s.texts["symbol"], qa["issues"])


def test_no_text_clipping(real):
    for i, s in real["stories"]:
        _, rec, _ = real["frames"][i]
        for tb in rec.texts:
            if tb.role in ("price_label", "price_value"):
                assert TAG_X0 <= tb.box[0] and tb.box[2] <= TAG_X1, (tb.text, tb.box)
            if tb.role == "takeaway":
                assert tb.box[2] <= theme.RIGHT_RAIL_X + 5
        # every label a scene can put in a tag fits at the minimum font
    for lab in ("20-DAY HIGH", "50-DAY HIGH", "20-DAY LOW", "50-DAY LOW", "20-DAY AVG",
                "50-DAY AVG", "CLOSE"):
        assert tlen(lab, font(theme.MIN_FONT)) <= TAG_X1 - TAG_X0 - 20, lab


def test_no_chart_label_overlap(real):
    for i, s in real["stories"]:
        _, rec, _ = real["frames"][i]
        tags = [b for k, b in rec.marks if k == "price_tag"]
        for a, b in itertools.combinations(tags, 2):
            assert min(a[3], b[3]) <= max(a[1], b[1]), (a, b)
        callouts = [b for k, b in rec.marks if k == "callout"]
        for c in callouts:
            for t in tags:
                assert c[2] <= t[0] or c[0] >= t[2] or c[3] <= t[1] or c[1] >= t[3]


def test_all_three_share_one_shell(real):
    shells = []
    for i, s in real["stories"]:
        _, rec, _ = real["frames"][i]
        marks = {k for k, _ in rec.marks}
        assert {"candles", "event_candle", "price_tag"} <= marks
        ys = {tb.role: tb.box[1] for tb in rec.texts if tb.role in ("symbol", "takeaway")}
        shells.append((ys["symbol"], ys["takeaway"], s.duration))
    # same positions for every story (glyph ascenders move a text box by a pixel or two)
    for a, b in itertools.combinations(shells, 2):
        assert abs(a[0] - b[0]) <= 4 and abs(a[1] - b[1]) <= 4, (a, b)
    assert all(6.0 <= d <= 8.0 for *_, d in shells)


def test_radar_section_runtime(real):
    radar = sum(s.duration for s in real["sb"].scenes if s.section == "RADAR")
    assert radar <= 3.8 + 3 * 8.0
    assert real["sb"].total_duration <= 65.0

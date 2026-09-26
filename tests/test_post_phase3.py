"""Phase 3: POST editorial planner + one-story structure.

Real 2026-09-21 session (the planner decides - nothing forced) plus synthetic sessions that make
each optional section qualify, so both sides of every rule are exercised.
"""
import ast
import copy
import datetime as dt
import hashlib
import inspect
import os
import re
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import daily_video.radar_scenes as radar_scenes
import daily_video.storyboard as sbm
import presentation.post_plan as pp
from core.content_safety import SafetyStatus, scan_publication
from daily_video import Composer, theme
from daily_video.market_scenes import MarketPulseScene, SectorBoardScene
from daily_video.typography import Recorder
from editorial.models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from hooks.validation import _COMPILED
from presentation.post_plan import (OPTIONAL_BUDGET, direction_phrase, plan_post_sections,
                                    session_support, structural_event)
from presentation.radar_story import build_radar_story_model

REPORT = "output/reports/premarket_2026-09-23.json"
RADAR_RESULT = "output/radar/daily_radar_2026-09-21.json"


@pytest.fixture(scope="module")
def real():
    if not (os.path.exists(REPORT) and os.path.exists(RADAR_RESULT)):
        pytest.skip("real validation artifacts not present")
    import render_daily_market_byte as r
    plan, pres, rp, rr, ev, uni, src = r.load_inputs(REPORT, "output/radar", "2026-09-21")
    sb = sbm.build_storyboard(plan, pres, rp, rr, ev, uni, src)
    by = {s["instrument"]: s for s in rr["stories"]}
    stories = [(sc["story"], by[sc["story"]["instrument"]]) for sc in rp["scenes"]
               if sc.get("role") == "STORY"][:3]
    return {"sb": sb, "plan": plan, "pres": pres, "stories": stories, "ev": ev, "uni": uni,
            "comp": Composer(sb), "rp": rp, "rr": rr, "src": src}


# --------------------------------------------------------------------------- synthetic sessions
def _df(n=40, cross=None, last_pct=0.3):
    """Nifty-like candles with an ema20 column; `cross` = "up"/"down" puts a fresh 20-day-average
    cross on the last session, else the close stays on one side."""
    closes = [24000 + 30 * np.sin(i / 3) for i in range(n)]
    ema = [24040.0] * n                       # every earlier close sits below the average
    if cross == "up":                         # crosses the average, stays inside the range
        closes[-2], closes[-1] = 24020.0, 24045.0
    elif cross == "down":
        closes[-2], closes[-1] = 24045.0, 24020.0
    opens = [closes[0]] + closes[:-1]
    idx = pd.bdate_range(end="2026-03-06", periods=n)
    return pd.DataFrame({"Open": opens, "High": [max(o, c) + 20 for o, c in zip(opens, closes)],
                         "Low": [min(o, c) - 20 for o, c in zip(opens, closes)], "Close": closes,
                         "ema20": ema}, index=idx)


def _pres(pct=0.3, cross=None, sectors=(("Metal", 1.2), ("IT", -0.4))):
    df = _df(cross=cross)
    c = float(df["Close"].iloc[-1])
    m = {"pct": pct, "close": c, "chg": c * pct / 100, "open": float(df["Open"].iloc[-1]),
         "high": float(df["High"].iloc[-1]), "low": float(df["Low"].iloc[-1]), "chart_df": df}
    return SimpleNamespace(m=m, sec=[{"name": n, "pct": v} for n, v in sectors],
                           session_date=dt.date(2026, 3, 6), fd=None)


def _items(rows, label=""):
    return [EditorialItem(title=n, value=f"{v:+.2f}%" if isinstance(v, float) else str(v),
                          numeric=v if isinstance(v, float) else None,
                          positive=(v >= 0) if isinstance(v, float) else None, label=label, rank=i + 1)
            for i, (n, v) in enumerate(rows)]


def _plan(gainers=(("STOCK-E", 1.4),), losers=(("STOCK-F", -1.2),), globals_=(("NASDAQ", 0.3),),
          flows=None, events=(("STOCK-X IPO opens", "IPO"),)):
    scenes = [ScenePlan(scene_id="g", scene_type=SceneType.GAINERS, items=_items(gainers)),
              ScenePlan(scene_id="l", scene_type=SceneType.LOSERS, items=_items(losers)),
              ScenePlan(scene_id="gl", scene_type=SceneType.GLOBAL, items=_items(globals_))]
    if flows:
        scenes.append(ScenePlan(scene_id="f", scene_type=SceneType.FLOWS, items=[
            EditorialItem(title=n, value=f"{'+' if v >= 0 else '-'}Rs {abs(v):,.0f} cr", numeric=v,
                          positive=v >= 0) for n, v in flows]))
    if events:
        scenes.append(ScenePlan(scene_id="e", scene_type=SceneType.EVENTS,
                                items=[EditorialItem(title=t, label=tag) for t, tag in events]))
    return ShortsPlan(report_id="SYNTH", scenes=scenes)


def _stories(*syms):
    return [({"instrument": s}, {"instrument": s}) for s in syms]


# --------------------------------------------------------------------------- 1-2 planner basics
def test_plan_is_deterministic(real):
    a = plan_post_sections(real["pres"], real["plan"], real["stories"], real["uni"]).to_dict()
    b = plan_post_sections(real["pres"], real["plan"], real["stories"], real["uni"]).to_dict()
    assert a == b


def test_real_session_plan_and_reasons(real):
    post = real["sb"].post_plan
    assert post["order"] == ["PULSE", "SECTORS", "RADAR"]
    assert post["show_market_pulse"] and post["show_sectors"] and post["show_radar"]
    assert not post["show_nifty_structure"] and not post["show_movers"]
    assert not post["show_global_context"] and not post["show_special_event"]
    assert set(post["reasons"]) >= {"PULSE", "NIFTY", "SECTORS", "MOVERS", "FLOWS", "GLOBAL",
                                    "EVENT", "RADAR"}
    assert all(v for v in post["reasons"].values())            # every decision has a reason


def test_core_sections_survive_on_any_day_with_data():
    post = plan_post_sections(_pres(), _plan(), _stories("STOCK-A", "STOCK-B", "STOCK-C"))
    assert post.order[0] == "PULSE" and "SECTORS" in post.order and post.order[-1] == "RADAR"


# --------------------------------------------------------------------------- 3-7 optional sections
def test_movers_disappear_when_small_and_appear_when_distinct():
    assert not plan_post_sections(_pres(), _plan(), _stories("STOCK-A")).show_movers
    big = plan_post_sections(_pres(), _plan(gainers=(("STOCK-E", 7.4),)), _stories("STOCK-A"))
    assert big.show_movers and big.movers["cards"][0]["name"] == "STOCK-E"


@pytest.mark.parametrize("gain,loss,shown", [
    (6.9, -1.2, False),      # below 7% and an 8.1-pt spread: not its own story any more
    (6.2, -4.0, False),      # the old 4% / 6-pt rule fired here; the freeze rule does not
    (7.0, -1.2, True),       # a single move at the 7% line
    (1.0, -7.2, True),       # the single move can be the fall
    (6.5, -5.6, True),       # 12.1-pt spread with both sides distinct from Radar
    (6.0, -5.9, False),      # 11.9-pt spread: just under
])
def test_movers_freeze_threshold(gain, loss, shown):
    post = plan_post_sections(_pres(), _plan(gainers=(("STOCK-E", gain),),
                                             losers=(("STOCK-F", loss),)), _stories("STOCK-A"))
    assert post.show_movers is shown, post.reasons["MOVERS"]


def test_movers_never_repeat_a_radar_story():
    post = plan_post_sections(_pres(), _plan(gainers=(("STOCK-A", 7.5),)), _stories("STOCK-A"))
    assert not post.show_movers and "Radar story" in post.reasons["MOVERS"]


def test_global_context_is_rare_and_never_overnight():
    quiet = plan_post_sections(_pres(pct=0.3), _plan(globals_=(("NASDAQ", -2.4),)), _stories())
    assert not quiet.show_global_context                      # Nifty itself barely moved
    opposite = plan_post_sections(_pres(pct=1.6), _plan(globals_=(("NASDAQ", -2.4),)), _stories())
    assert not opposite.show_global_context                   # not the same side
    same = plan_post_sections(_pres(pct=-1.6), _plan(globals_=(("NASDAQ", -2.4),)), _stories())
    assert same.show_global_context and same.global_context["headline"] == "Global context"


def test_overnight_cues_never_appear_in_post(real):
    shown = " ".join(real["sb"].public_text().values()).lower()
    assert "overnight" not in shown
    assert all(s.kind != "AHEAD" for s in real["sb"].scenes)
    busy = sbm.build_storyboard(_plan(globals_=(("NASDAQ", -2.4),), gainers=(("STOCK-E", 6.0),)),
                                _pres(pct=-1.6), dynamic_hook=False)
    assert "overnight" not in " ".join(busy.public_text().values()).lower()


def test_forward_looking_events_are_not_post_sections():
    post = plan_post_sections(_pres(), _plan(events=(("IPO opens tomorrow", "IPO"),)), _stories())
    assert not post.show_special_event
    rbi = plan_post_sections(_pres(), _plan(events=(("RBI policy decision", "RBI"),)), _stories())
    assert rbi.show_special_event


def test_nifty_chart_only_for_a_new_structural_event():
    assert structural_event(_df()) is None                     # a continuing state is not news
    assert structural_event(_df(cross="up"))["family"] == "MA_UP"
    assert structural_event(_df(cross="down"))["family"] == "MA_DOWN"
    post = plan_post_sections(_pres(cross="up"), _plan(), _stories())
    assert post.show_nifty_structure
    assert post.structure["model"]["support"]["kind"] == "NONE"    # no repeat of the pulse fact


def test_optional_budget_keeps_the_short_a_story():
    post = plan_post_sections(_pres(pct=-1.6, cross="down"),
                              _plan(gainers=(("STOCK-E", 8.0),), globals_=(("NASDAQ", -2.4),),
                                    flows=(("FII", -3200.0), ("DII", 2900.0)),
                                    events=(("RBI policy decision", "RBI"),)),
                              _stories("STOCK-A", "STOCK-B", "STOCK-C"))
    optional = [k for k in post.order if k not in ("PULSE", "SECTORS", "RADAR")]
    assert optional == ["NIFTY", "FLOWS"] and len(optional) == OPTIONAL_BUDGET
    for k in ("MOVERS", "EVENT", "GLOBAL"):
        assert "higher priority" in post.reasons[k]


# --------------------------------------------------------------------------- 6 no AI
def test_no_ai_in_section_selection():
    src = inspect.getsource(pp)
    tree = ast.parse(src)
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not mods & {"news", "requests", "hooks", "hooks.gemini", "hooks.engine", "providers"}
    assert "gemini" not in src.lower().replace("never ask gemini", "")


def test_gemini_reply_cannot_change_sections(real):
    calls = []
    sb = sbm.build_storyboard(real["plan"], real["pres"], real["rp"], real["rr"], real["ev"],
                              real["uni"], real["src"], hook_ai=True,
                              hook_client=lambda p, s: calls.append(p) or '{"sections": ["MOVERS"]}')
    assert calls and sb.post_plan["order"] == real["sb"].post_plan["order"]


# --------------------------------------------------------------------------- 8-10 locked sections
def test_radar_intro_unchanged(real):
    src = inspect.getsource(radar_scenes.RadarIntroScene)
    assert hashlib.sha256(src.encode()).hexdigest() == \
        "aad14cd1396ebe6d0c7ab30680db5ab3365f5521804902461a8594ad0801eb71"
    intro = next(s for s in real["sb"].scenes if s.kind == "RADAR_INTRO")
    assert intro.headline == "Beyond the biggest movers" and intro.duration == 3.8


def test_radar_stories_unchanged(real):
    stories = [s for s in real["sb"].scenes if s.kind == "RADAR_STORY"]
    assert [s.counter for s in stories] == ["1 / 3", "2 / 3", "3 / 3"]
    for i, (s, (sp, st)) in enumerate(zip(stories, real["stories"]), start=1):
        m = build_radar_story_model(sp, st, real["ev"][sp["instrument"]], i, 3)
        assert s.data == m.to_dict() and s.duration == 6.8


def test_radar_is_the_last_analytical_section_and_close_follows(real):
    kinds = [s.kind for s in real["sb"].scenes]
    assert kinds[-1] == "CLOSING" and kinds[-2] == "RADAR_STORY"
    assert real["sb"].scenes[-1].duration <= 3.0


def test_dynamic_hook_unchanged_and_promises_only_what_is_shown(real):
    hp = real["sb"].hook_plan
    assert hp["archetype"] == "QUIET_MARKET_HIDDEN_ACTION"
    assert hp["curiosity_line"] == "Nifty moved just +0.29%. Three stocks underneath didn't."
    assert hp["summary_line"] == "Inside: the close, every sector and 3 Radar stocks."
    assert "movers" not in hp["summary_line"]                  # the Short no longer has Movers


# --------------------------------------------------------------------------- 11-12 wording
PLANNER_PHRASES = [direction_phrase(p) for p in (0.05, 0.3, -0.3, 0.9, -0.9, 1.8, -1.8)] + \
    [session_support(100, 110, 90, c)["text"] for c in (109, 91, 104, 96)] + \
    ["Global context", "Biggest single-stock moves", "Today's scheduled event"]


def test_no_recommendation_or_prediction_language():
    for t in PLANNER_PHRASES:
        for fam in ("recommendation", "prediction"):
            assert not any(p.search(t) for p in _COMPILED[fam]), (t, fam)


def test_public_text_passes_content_safety(real):
    assert scan_publication(real["sb"].public_text()).status is SafetyStatus.SAFE


# --------------------------------------------------------------------------- 13-15 layout
def _all_optional_storyboard():
    return sbm.build_storyboard(
        _plan(gainers=(("STOCK-E", 6.0),), losers=(("STOCK-F", -5.1),)),
        _pres(pct=-0.9, cross="down"), dynamic_hook=False)


def test_safe_areas_and_clipping_real_and_optional(real):
    for sb in (real["sb"], _all_optional_storyboard()):
        comp = Composer(sb)
        for i, spec in enumerate(sb.scenes):
            _, rec, qa = comp.freeze(i)
            assert qa["passed"], (spec.kind, qa["issues"])
            for tb in rec.texts:
                assert 0 <= tb.box[0] and tb.box[2] <= theme.CANVAS_W - 30, (spec.kind, tb.text)


def test_one_primary_fact_per_market_screen(real):
    comp = real["comp"]
    for i, spec in enumerate(real["sb"].scenes):
        if spec.kind != "PULSE":
            continue
        _, rec, _ = comp.freeze(i)
        roles = [tb.role for tb in rec.texts]
        assert roles.count("hero") == 1 and roles.count("support") == 1
        shown = " ".join(tb.text for tb in rec.texts)
        assert "BANK NIFTY" not in shown and "VIX" not in shown     # no second market
    for i, spec in enumerate(real["sb"].scenes):
        if spec.kind == "SECTORS":
            _, rec, _ = comp.freeze(i)
            assert sum(1 for tb in rec.texts if tb.role == "headline") <= 2   # one sentence


# --------------------------------------------------------------------------- 16 runtime
def test_runtime(real):
    assert real["sb"].total_duration <= 65.0
    busiest = sbm.build_storyboard(
        _plan(gainers=(("STOCK-E", 6.0),), globals_=(("NASDAQ", -2.4),),
              flows=(("FII", -3200.0), ("DII", 2900.0)), events=(("RBI policy decision", "RBI"),)),
        _pres(pct=-1.6, cross="down"), dynamic_hook=False)
    assert busiest.total_duration + 4.6 + 3.8 + 3 * 6.8 <= 65.0     # + hook and a full Radar


# --------------------------------------------------------------------------- transitions
def test_no_scene_boundary_passes_through_an_empty_frame(real):
    """Every hand-off keeps content on screen: at each cut, the stage's contrast never drops
    toward the level of the bare background (the pre-Phase-3 fade-out/fade-in left a blank
    beat at every boundary)."""
    comp = real["comp"]
    bg = np.asarray(comp.chrome.background(10.0, dim=True).convert("L").crop((40, 280, 1040, 1300)),
                    dtype=float).std()
    for cut in real["sb"].starts()[1:]:
        worst = min(np.asarray(comp.frame(cut + f / 30).convert("L").crop((40, 280, 1040, 1300)),
                               dtype=float).std() for f in range(0, 10))
        assert worst > 3 * bg, (cut, worst, bg)


# --------------------------------------------------------------------------- multi-session findings
@pytest.mark.parametrize("sectors,headline,tags", [
    ((("IT", -0.44), ("Pharma", -1.1), ("Bank", -1.96)),
     "All 3 sector indices fell; IT fell least", ("FELL LEAST", "FELL MOST")),
    ((("Metal", 2.1), ("Auto", 0.6), ("IT", 0.2)),
     "All 3 sector indices rose; Metal led", ("LEADER", "ROSE LEAST")),
    ((("Metal", 1.2), ("IT", -0.4)), "Metal led, IT lagged", ("LEADER", "LAGGARD")),
])
def test_sector_wording_never_calls_a_falling_sector_a_leader(sectors, headline, tags):
    """Found on the real 24 Sep 2026 session: every sector fell, and the headline said
    "IT led" (IT -0.44%) - which reads as "IT rose"."""
    post = plan_post_sections(_pres(sectors=sectors), _plan(), _stories())
    assert post.sectors["headline"] == headline
    assert (post.sectors["leader_tag"], post.sectors["laggard_tag"]) == tags
    spec = sbm._sectors_scene(post.sectors)
    from daily_video.storyboard import Storyboard
    _, rec, qa = Composer(Storyboard(session_date=dt.date(2026, 3, 6), date_label="FRI 06 MAR 2026",
                                     kicker="SESSION RECAP", scenes=[spec])).freeze(0)
    assert qa["passed"], qa["issues"]

"""POST freeze corrections: Movers threshold (see test_post_phase3), universe coverage gate,
extreme-move / corporate-action / bad-data guard, major-index hook priority, teaser diversity.

Synthetic placeholders only (STOCK-A ...). Offline.
"""
from __future__ import annotations

import functools
import datetime as dt
import inspect
import json
from types import SimpleNamespace

import pytest

import core.move_guard as move_guard
from conftest import PREV_SESSION, SESSION, build_test_report, full_movers_coverage
from core.move_guard import validate_move
from editorial import plan_short
from editorial.models import SceneType
from editorial.movers_gate import movers_coverage_verdict

# These tests pin the editorial ENGINE (movers ranking, news events, stock insights), which
# is PRIVATE_ANALYTICS content since the publication boundary; the public default is
# covered by tests/test_public_publication.py.
plan_short = functools.partial(plan_short, profile="PRIVATE_ANALYTICS")
from hooks import plan_hook, post_market_sheet
from hooks import diversity
from hooks.candidates import build_candidates
from hooks.fixtures import POST_EXAMPLES, _plan as fx_plan, _post_bundle, _pres as fx_pres
from hooks.models import Archetype, HookSource
from presentation.radar_guard import guard_radar_stories


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("no network in tests")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# =========================================================================== move guard
def _v(**kw):
    base = dict(close=1020.0, prev_close=1000.0, open_=1001.0, high=1025.0, low=998.0,
                change_pct=2.0, relative_volume=1.2)
    base.update(kw)
    return validate_move(**base)


def test_ordinary_move_is_validated():
    v = _v()
    assert v.status == "VALIDATED" and v.publishable


def test_missing_previous_close_is_held():
    assert _v(prev_close=None).status == "MISSING_PREVIOUS_CLOSE"
    assert _v(prev_close=0).status == "MISSING_PREVIOUS_CLOSE"
    assert not _v(prev_close=float("nan")).publishable


def test_stale_previous_close_is_held():
    v = _v(prev_date=dt.date(2026, 9, 16), expected_prev_date=dt.date(2026, 9, 17))
    assert v.status == "STALE_PREVIOUS_CLOSE" and not v.publishable
    v = _v(session_date="2026-09-17", expected_session_date=dt.date(2026, 9, 18))
    assert v.status == "STALE_PREVIOUS_CLOSE"
    assert _v(prev_date="2026-09-17", expected_prev_date=dt.date(2026, 9, 17)).publishable


def test_self_contradicting_bar_is_held():
    assert _v(low=1030.0).status == "INCONSISTENT_OHLC"          # low above the close
    assert _v(high=1010.0).status == "INCONSISTENT_OHLC"         # high below the close
    assert _v(open_=-1.0).status == "INCONSISTENT_OHLC"


def test_stated_change_must_match_its_closes():
    v = _v(change_pct=4.0)
    assert v.status == "INCONSISTENT_CHANGE" and not v.publishable


def test_split_signature_is_suspected_corporate_action():
    # Halved at the open and stayed there on ordinary volume: a 1:2 split / 1:1 bonus basis.
    v = validate_move(close=502.0, prev_close=1000.0, open_=499.0, high=505.0, low=496.0,
                      change_pct=-49.8, relative_volume=2.1)
    assert v.status == "CORPORATE_ACTION_SUSPECTED" and not v.publishable
    assert "1:2 split" in v.details["matched_ratio"]


def test_bonus_ratio_at_the_open_is_suspected_corporate_action():
    # 1:4 bonus -> close / prev = 0.8, gap at the open.
    v = validate_move(close=800.5, prev_close=1000.0, open_=801.0, high=806.0, low=795.0,
                      change_pct=-19.95, relative_volume=4.0)
    assert v.status == "CORPORATE_ACTION_SUSPECTED"


def test_extreme_move_beyond_single_source_limit_is_unresolved():
    # The shape found on a real session: opened -10%, closed on its low -36%, heavy volume.
    v = validate_move(close=1207.2, prev_close=1886.3, open_=1697.7, high=1697.7, low=1207.2,
                      change_pct=-36.0017, relative_volume=18.9)
    assert v.status == "UNRESOLVED_EXTREME_MOVE" and not v.publishable


def test_extreme_move_without_volume_is_a_discontinuity():
    v = validate_move(close=870.0, prev_close=1000.0, open_=880.0, high=890.0, low=865.0,
                      change_pct=-13.0, relative_volume=1.1)
    assert v.status == "PRICE_DISCONTINUITY"
    assert validate_move(close=870.0, prev_close=1000.0, change_pct=-13.0).status == \
        "PRICE_DISCONTINUITY"                                    # volume unknown


def test_volume_confirmed_extreme_move_is_published_and_flagged():
    v = validate_move(close=870.0, prev_close=1000.0, open_=985.0, high=990.0, low=860.0,
                      change_pct=-13.0, relative_volume=4.2)
    assert v.status == "VALIDATED_EXTREME" and v.publishable


def test_guard_is_deterministic_and_names_no_stock():
    a = validate_move(close=870.0, prev_close=1000.0, change_pct=-13.0, relative_volume=4.2)
    b = validate_move(close=870.0, prev_close=1000.0, change_pct=-13.0, relative_volume=4.2)
    assert a == b
    src = inspect.getsource(move_guard).upper()
    for name in ("VEDL", "POLICYBZR", "LTF", "RELIANCE", "INFY"):
        assert name not in src


# =========================================================================== acquisition
def test_get_movers_audited_records_coverage_and_holds_bad_rows(monkeypatch):
    import pandas as pd
    import market

    dates = pd.bdate_range(end=pd.Timestamp(SESSION), periods=40)
    frames = {}
    syms = [f"STOCK-{c}" for c in "ABCDEFGHIJKL"]
    for i, s in enumerate(syms):
        closes = [100.0 + i + 0.1 * k for k in range(40)]
        opens = [c - 0.05 for c in closes]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in opens]
        vol = [1e6] * 40
        if s == "STOCK-A":                          # -40% print: unresolvable single-source
            closes[-1] = closes[-2] * 0.6
            opens[-1], highs[-1], lows[-1] = closes[-2] * 0.9, closes[-2] * 0.9, closes[-1]
            vol[-1] = 2e7
        frames[(s + ".NS")] = pd.DataFrame({"Open": opens, "High": highs, "Low": lows,
                                            "Close": closes, "Volume": vol}, index=dates)
    raw = pd.concat(frames, axis=1)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: raw)
    universe = {s: s for s in syms + ["STOCK-Z"]}      # STOCK-Z never arrives
    g, lo, audit = market.get_movers_audited(universe, SESSION, PREV_SESSION, n=5)
    assert audit["universe_expected"] == 13
    assert audit["universe_observed"] == 12
    assert audit["universe_validated"] == 11
    assert audit["coverage_pct"] == round(100 * 11 / 13, 2)
    assert audit["missing"] == ["STOCK-Z"]
    held = audit["excluded"]
    assert [r["symbol"] for r in held] == ["STOCK-A"]
    assert held[0]["validation"]["status"] == "UNRESOLVED_EXTREME_MOVE"
    assert held[0]["close"] and held[0]["prev_close"]          # raw values kept, not deleted
    assert "STOCK-A" not in [r["symbol"] for r in g + lo]      # never ranked
    assert all(r["validation"]["publishable"] for r in g + lo)


# =========================================================================== coverage gate
@pytest.fixture
def movers():
    g = [{"symbol": f"STOCK-{c}", "name": f"Placeholder {c}", "close": 100.0 + i,
          "pct": 3.0 - 0.4 * i, "volx": 1.2} for i, c in enumerate("ABCDE")]
    lo = [{"symbol": f"STOCK-{c}", "name": f"Placeholder {c}", "close": 90.0 + i,
           "pct": -3.0 + 0.4 * i, "volx": 1.1} for i, c in enumerate("FGHIJ")]
    return g, lo


def _build(market_frame, movers, coverage):
    g, lo = movers
    return build_test_report(market_frame, gainers=g, losers=lo, movers_coverage=coverage)


def _scenes(plan):
    return {s.scene_type for s in plan.scenes}


def test_full_coverage_publishes_the_ranking(market_dict, movers):
    plan = plan_short(_build(market_dict, movers, full_movers_coverage()))
    assert {SceneType.GAINERS, SceneType.LOSERS} <= _scenes(plan)
    assert not [o for o in plan.omitted if o.get("reason") == "universe_coverage"]


@pytest.mark.parametrize("validated,status", [(89, "INSUFFICIENT"), (81, "INSUFFICIENT")])
def test_partial_universe_suppresses_every_ranking_claim(market_dict, movers, validated,
                                                         status):
    report = _build(market_dict, movers, full_movers_coverage(100, validated))
    plan = plan_short(report)
    assert not {SceneType.GAINERS, SceneType.LOSERS} & _scenes(plan)
    gate = [o for o in plan.omitted if o.get("reason") == "universe_coverage"]
    assert gate and gate[0]["status"] == status and gate[0]["coverage_pct"] == validated
    # nothing removed from the canonical report
    assert len(report.gainers) == 5 and len(report.losers) == 5


def test_coverage_exactly_at_the_line_publishes(market_dict, movers):
    plan = plan_short(_build(market_dict, movers, full_movers_coverage(100, 90)))
    assert SceneType.GAINERS in _scenes(plan)


def test_unknown_coverage_is_never_ranked(market_dict, movers):
    report = _build(market_dict, movers, None)
    assert movers_coverage_verdict(report)["status"] == "UNKNOWN"
    plan = plan_short(report)
    assert not {SceneType.GAINERS, SceneType.LOSERS} & _scenes(plan)


def test_gated_movers_never_reach_the_hook(market_dict, movers):
    from presentation import ReportPresentation
    report = _build(market_dict, movers, full_movers_coverage(100, 70))
    plan = plan_short(report)
    sheet = post_market_sheet(plan, ReportPresentation(report), [], {}, ["PULSE"], "Nifty 100")
    assert sheet.fact("mover.gainer") is None and sheet.fact("mover.loser") is None
    assert not [b for b in sheet.beats if b.beat_id.startswith("TOP_")]


def test_row_with_a_held_verdict_is_never_shown(market_dict, movers):
    g, lo = movers
    g = [dict(r) for r in g]
    g[0]["validation"] = {"status": "PRICE_DISCONTINUITY", "publishable": False}
    report = build_test_report(market_dict, gainers=g, losers=lo,
                               movers_coverage=full_movers_coverage())
    shown = plan_short(report).scene(SceneType.GAINERS)
    assert shown is not None and "STOCK-A" not in [i.title for i in shown.items]


# =========================================================================== Radar guard
def _ev(closes, dates, opens=None, highs=None, lows=None, rvol=None):
    return SimpleNamespace(close_series=closes, window_dates=dates, open_series=opens,
                           high_series=highs, low_series=lows, rvol=rvol)


def _rs(sym, pct, rvol):
    return ({"instrument": sym}, {"instrument": sym, "price_change_pct": pct,
                                  "volume_context": {"relative_volume": rvol}})


D = [dt.date(2026, 9, 16), dt.date(2026, 9, 17), dt.date(2026, 9, 18)]


def test_radar_guard_skips_held_story_and_keeps_selector_order():
    stories = [_rs("STOCK-A", 2.0, 2.5), _rs("STOCK-B", -36.0, 18.9), _rs("STOCK-C", 1.5, 1.9),
               _rs("STOCK-D", -0.5, 1.0)]
    ev = {"STOCK-A": _ev([100, 100, 102], D), "STOCK-B": _ev([100, 100, 64], D),
          "STOCK-C": _ev([100, 100, 101.5], D), "STOCK-D": _ev([100, 100, 99.5], D)}
    kept, audit = guard_radar_stories(stories, ev, D[-1], D[-2])
    assert [sp["instrument"] for sp, _ in kept] == ["STOCK-A", "STOCK-C", "STOCK-D"]
    held = [a for a in audit if not a["publishable"]]
    assert held[0]["instrument"] == "STOCK-B" and held[0]["status"] == "UNRESOLVED_EXTREME_MOVE"
    assert held[0]["selector_rank"] == 2


def test_radar_guard_catches_a_stale_window():
    stale = [dt.date(2026, 9, 15), dt.date(2026, 9, 16), dt.date(2026, 9, 18)]
    kept, audit = guard_radar_stories([_rs("STOCK-A", 2.0, 2.5)],
                                      {"STOCK-A": _ev([100, 100, 102], stale)}, D[-1], D[-2])
    assert not kept and audit[0]["status"] == "STALE_PREVIOUS_CLOSE"


def test_radar_guard_without_evidence_runs_magnitude_rules_only():
    kept, audit = guard_radar_stories([_rs("STOCK-A", 4.0, None), _rs("STOCK-B", -25.0, 9.0)],
                                      {}, D[-1], D[-2])
    assert [sp["instrument"] for sp, _ in kept] == ["STOCK-A"]
    assert "no chart evidence" in audit[0]["basis"]


# =========================================================================== hook priority
def _big_day(move=-6.1, rvol=4.1, signals=3):
    pres = fx_pres(-1.64, 23_063.10, [("Bank", -1.96), ("Auto", -1.20), ("IT", -0.44)],
                   seed=9, bank=-1.96, vix=16.0)
    specs = [("STOCK-A", "Placeholder Finance Ltd.", move, ["BREAK_BELOW_50D_RANGE"], "breakdown",
              rvol, -5.2, signals, ("STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"))]
    b = _post_bundle(pres, fx_plan(-1.64, [("STOCK-B", 0.84)], [("STOCK-D", -4.72)],
                                   context="Closed below its 20-day average",
                                   flows=[("FII", -3120.0), ("DII", 2640.0)]), specs, seed=40,
                     sections=["PULSE", "NIFTY", "FLOWS", "SECTORS", "RADAR"])
    return post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                             b["universe"])


def test_big_index_day_outranks_a_normal_unusual_stock():
    sheet = _big_day()
    cands = build_candidates(sheet)
    assert cands[0].archetype is Archetype.BIG_MOVE
    assert Archetype.UNUSUAL_ACTIVITY not in [c.archetype for c in cands]  # not even offered
    rec = sheet.metadata["hook_priority"]
    assert rec["applies"] and rec["suppressed"][0]["symbol"] == "STOCK-A"
    plan = plan_hook(sheet, use_ai=False)
    assert plan.archetype is Archetype.BIG_MOVE and plan.priority_rule["applies"]


def test_gemini_cannot_pick_the_suppressed_stock_hook():
    sheet = _big_day()
    offered = [c.candidate_id for c in build_candidates(sheet)]
    reply = {"candidate_id": "post-unusual-stock_a", "archetype": "UNUSUAL_ACTIVITY",
             "hero_visual": "SIGNAL_STACK_CHART", "teaser_beats": ["NIFTY_CLOSE", "FLOWS"],
             "curiosity_line": "STOCK-A -6.1%.", "summary_line": "Inside: the close."}
    assert "post-unusual-stock_a" not in offered
    plan = plan_hook(sheet, client=lambda p, s: json.dumps(reply))
    assert plan.source is HookSource.DETERMINISTIC and plan.archetype is Archetype.BIG_MOVE


def test_objectively_exceptional_stock_event_keeps_its_candidate():
    sheet = _big_day(move=-12.4, rvol=6.2)
    cands = build_candidates(sheet)
    assert Archetype.UNUSUAL_ACTIVITY in [c.archetype for c in cands]
    exc = sheet.metadata["hook_priority"]["exceptions"][0]
    assert exc["symbol"] == "STOCK-A" and all(exc["tests"].values())


@pytest.mark.parametrize("move,rvol", [(-9.9, 8.0), (-12.4, 4.9)])
def test_exception_needs_every_rarity_test(move, rvol):
    sheet = _big_day(move=move, rvol=rvol)
    assert Archetype.UNUSUAL_ACTIVITY not in [c.archetype for c in build_candidates(sheet)]


def test_priority_rule_is_idle_below_the_major_line():
    b = POST_EXAMPLES["post_radar_unusual"]()
    sheet = post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                              b["universe"])
    assert build_candidates(sheet)[0].archetype is Archetype.UNUSUAL_ACTIVITY
    assert sheet.metadata["hook_priority"]["applies"] is False


# =========================================================================== teaser diversity
def _post_sheets():
    for name, fn in POST_EXAMPLES.items():
        b = fn()
        yield name, post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"],
                                      b["sections"], b["universe"])
    yield "big_day", _big_day()


@pytest.mark.parametrize("name,sheet", list(_post_sheets()))
def test_every_post_candidate_teaser_is_entity_distinct(name, sheet):
    for c in build_candidates(sheet):
        assert not diversity.entity_clashes(sheet, c.default_beats), (name, c.candidate_id,
                                                                      c.default_beats)
        ranks = [diversity.family_rank(sheet.beat(b)) for b in c.default_beats]
        assert ranks == sorted(ranks), (name, c.default_beats)       # index -> breadth -> stock


def test_same_stock_twice_from_gemini_is_rejected():
    b = POST_EXAMPLES["post_quiet"]()
    sheet = post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                              b["universe"])
    top = build_candidates(sheet)[0]
    reply = {"candidate_id": top.candidate_id, "archetype": top.archetype.value,
             "hero_visual": top.default_hero.value,
             "teaser_beats": ["NIFTY_CLOSE", "VOLUME_SPIKE:STOCK-A", "RADAR_EVENT:STOCK-A"],
             "curiosity_line": top.curiosity_line, "summary_line": top.summary_line}
    plan = plan_hook(sheet, client=lambda p, s: json.dumps(reply))
    assert plan.source is HookSource.DETERMINISTIC
    assert any("both show STOCK-A" in i for i in plan.validation_issues)
    assert not diversity.entity_clashes(sheet, plan.teaser_beats)


def test_fewer_beats_rather_than_a_repeat():
    b = POST_EXAMPLES["post_quiet"]()
    sheet = post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                              b["universe"])
    # Keep only one Radar stock's beats and the Nifty line: two entity-distinct beats exist.
    sheet.beats = [x for x in sheet.beats
                   if x.beat_id in ("NIFTY_CLOSE", "RADAR_EVENT:STOCK-A", "VOLUME_SPIKE:STOCK-A")]
    from hooks.candidates import pick_beats
    beats = pick_beats(sheet, ["RADAR_EVENT:STOCK-A", "VOLUME_SPIKE:STOCK-A", "NIFTY_CLOSE"])
    assert beats == ("NIFTY_CLOSE", "RADAR_EVENT:STOCK-A")


def test_pre_and_custom_beats_are_unchanged_by_the_post_rule():
    from hooks import custom_stock_sheet, pre_market_sheet
    from hooks.fixtures import CUSTOM_EXAMPLES, PRE_EXAMPLES
    for fn in PRE_EXAMPLES.values():
        assert not diversity.applies(pre_market_sheet(fn()))
    for fn in CUSTOM_EXAMPLES.values():
        assert not diversity.applies(custom_stock_sheet(fn()))

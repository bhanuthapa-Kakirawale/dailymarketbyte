"""Editorial Planner V3 (P4): one deterministic arbitration for every story competing for a
POST / PRE Short's limited slots - materiality tiers, edition relevance, redundancy, one story
per family, slots and runtime - with a full decision trace.

Fully synthetic and offline: nothing here reads output/, SQLite, a cache or the network.
"""
import ast
import dataclasses
import datetime as dt
import inspect
import re
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import presentation.editorial_arbiter as ea
from core.content_safety import SafetyStatus, scan_publication
from editorial.models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from hooks.validation import _COMPILED
from market_structure.editorial import StructureInsight
from presentation.editorial_candidates import (cards_cost, exchange_candidate, ipo_candidate,
                                               ipo_cost, market_events_candidate,
                                               structure_candidate, structure_cost)
from presentation.post_plan import OPTIONAL_SLOTS as POST_SLOTS
from presentation.post_plan import plan_post_sections, post_policy
from presentation.pre_plan import OPTIONAL_SLOTS as PRE_SLOTS
from presentation.pre_plan import plan_pre_sections, pre_policy
from presentation.public_intelligence import PublicSections
from products.pre_fixtures import synthetic_brief

DAY = dt.date(2026, 10, 6)
NEXT = dt.date(2026, 10, 7)
LINES = ["SOURCE: NSE · NIFTY 200", "DATA AS OF: 06 OCT 2026 · 3:30 PM IST"]


# =========================================================================== builders
def C(cid, family, tier, section=None, cost=5.0, **kw):
    return ea.EditorialCandidate(cid, section or cid.split(".")[1], family, tier,
                                 f"{cid} reason", cost, **kw)


def _anchor(edition="POST"):
    sec = "PULSE" if edition == "POST" else "SETUP"
    return C(f"{edition}.{sec}", ea.STRUCTURE_ANCHOR, 3, section=sec, cost=5.4,
             role=ea.REQUIRED, topics=frozenset({"index_direction"}), as_of=DAY)


def _policy(slots=4, runtime=62.0, **kw):
    return dataclasses.replace(post_policy(runtime), optional_slots=slots, **kw)


def breadth(nifty_pct=0.30, higher=70, lower=128, cov=200, status="PUBLISHABLE"):
    """A BREADTH insight exactly as market_structure.editorial._breadth would word it."""
    up = nifty_pct > 0
    if (up and lower > higher) or (not up and higher > lower):
        n = lower if up else higher
        head = (f"Nifty 50 {'rose' if up else 'fell'} {abs(nifty_pct):.2f}%, but most NIFTY 200 "
                f"stocks {'fell' if up else 'rose'}")
        reason = f"divergence: Nifty {nifty_pct:+.2f}% vs {n} {'decliners' if up else 'advancers'}"
    else:
        n = max(higher, lower)
        head = f"A broad move: {n} of {cov} NIFTY 200 stocks {'rose' if higher > lower else 'fell'}"
        reason = f"breadth extreme: {n}/{cov}"
    return StructureInsight(
        kind="BREADTH", headline=head, hero_value=f"{n} / {cov}",
        hero_label="NIFTY 200 STOCKS CLOSED LOWER", definition="Close vs the previous session's close",
        split=[{"label": "HIGHER", "value": str(higher)}, {"label": "LOWER", "value": str(lower)},
               {"label": "UNCHANGED", "value": str(cov - higher - lower)}],
        takeaway=f"{min(higher, lower)} of {cov} closed the other way.", universe_label="NIFTY 200",
        denominator_text=f"{n} of {cov}", coverage_status=status,
        metric_keys=["ADVANCES", "DECLINES"], reason=reason)


def fifty_two(n=14, word="low", label=None, other=2):
    reason = f"{n} new 52-week {word}s (>= 5)" + (f"; {label}" if label else "")
    return StructureInsight(
        kind="FIFTY_TWO_WEEK", headline=f"{n} NIFTY 200 stocks closed at new 52-week {word}s",
        hero_value=f"{n} / 200", hero_label=f"NIFTY 200 STOCKS · NEW 52-WEEK {word.upper()}S",
        definition="Closed below its lowest close of the prior 252 sessions",
        rows=[{"name": "Financials", "value": str(n - 2), "n": n - 2},
              {"name": "Others", "value": "2", "n": 2}],
        takeaway=f"{other} closed at new 52-week {'highs' if word == 'low' else 'lows'}.",
        universe_label="NIFTY 200", denominator_text=f"{n} / 200",
        metric_keys=["NEW_52W_LOW", "NEW_52W_HIGH"], reason=reason)


def unusual(n=9, concentrated=False):
    return StructureInsight(
        kind="UNUSUAL_VOLUME", headline="Where unusual volume showed up in the NIFTY 200",
        hero_value=f"{n} / 200", hero_label="NIFTY 200 STOCKS WITH UNUSUAL VOLUME",
        definition="Volume at least 2x their own 20-session average",
        rows=[{"name": "IT", "value": str(n), "n": n}],
        takeaway="IT accounted for 5 of them." if concentrated else "",
        universe_label="NIFTY 200", denominator_text=f"{n} / 200",
        metric_keys=["UNUSUAL_VOLUME"], reason=f"{n} unusual-volume observations (>= 5)")


def market_event(family="EARNINGS", status="SCHEDULED", symbol="SYM1", sub_type=None, facts=None,
                 day=DAY):
    from market_events.models import SCHEMA_VERSION, SUCCESS, MarketEvent
    key = f"{family}:{symbol or 'MARKET'}:{sub_type or 'x'}:{day.isoformat()}"
    return MarketEvent(schema_version=SCHEMA_VERSION, family=family, event_key=key,
                       symbol=symbol, company="Co Ltd" if symbol else "Government of India",
                       status=status, sub_type=sub_type, data_as_of=day.isoformat(),
                       source_name="nse_corp_announcements" if symbol else "rbi_press_releases",
                       source_reference="https://example.invalid/x", facts=facts or [],
                       status_capture=SUCCESS)


def events_model(*evs, mode="POST"):
    from market_events.watch import build_model
    return build_model(list(evs), mode)


def gsec(status="SCHEDULED", day=DAY):
    return market_event("GOVT_SECURITIES_AUCTION", status, None, "TBILL_91",
                        [{"label": "notified_amount_crore", "value": 8000}], day=day)


def ipo_model(board="SME", chip="LISTED"):
    return {"layout": "CARD", "headline": f"Co: IPO {chip.lower()}",
            "card": {"name": "Co", "board": board, "chip": chip, "kind": "LISTED",
                     "rows": [{"label": "ISSUE PRICE", "value": "Rs 100"}]},
            "provenance_lines": ["SOURCE: NSE", "DATA AS OF: 06 OCT 2026"], "sources": ["nse"]}


def exchange_model(n=1):
    return {"headline": "One exchange update", "event_ids": [f"e{i}" for i in range(n)],
            "cards": [{"tag": "F&O BAN", "name": f"SYM{i}", "company": "Co", "status": "ENTERED",
                       "line": "Entered the F&O ban period", "change": "ENTERED"}
                      for i in range(n)],
            "provenance_lines": ["SOURCE: NSE", "LIST DATE: 06 OCT 2026"], "sources": ["nse"]}


def public(structure=(), market_events=None, ipo=None, exchange=None):
    return PublicSections(structure=[(i, LINES) for i in structure], market_events=market_events,
                          ipo=ipo, exchange=exchange,
                          audit={"omitted_sections": {}, "market_structure": {"present": True,
                                                                              "shown": []}})


# ---- POST report-side inputs (the same shapes tests/test_post_phase3.py uses)
def _df(n=40, cross=None):
    closes = [24000 + 30 * np.sin(i / 3) for i in range(n)]
    ema = [24040.0] * n
    if cross == "down":
        closes[-2], closes[-1] = 24045.0, 24020.0
    opens = [closes[0]] + closes[:-1]
    idx = pd.bdate_range(end=DAY.isoformat(), periods=n)
    return pd.DataFrame({"Open": opens, "High": [max(o, c) + 20 for o, c in zip(opens, closes)],
                         "Low": [min(o, c) - 20 for o, c in zip(opens, closes)], "Close": closes,
                         "ema20": ema}, index=idx)


def pres(pct=0.3, sectors=(("Metal", 1.2), ("IT", -0.4)), cross=None):
    df = _df(cross=cross)
    c = float(df["Close"].iloc[-1])
    m = {"pct": pct, "close": c, "chg": c * pct / 100, "open": float(df["Open"].iloc[-1]),
         "high": float(df["High"].iloc[-1]), "low": float(df["Low"].iloc[-1]), "chart_df": df}
    return SimpleNamespace(m=m, sec=[{"name": n, "pct": v} for n, v in sectors],
                           session_date=DAY, fd=None)


def _items(rows):
    return [EditorialItem(title=n, value=f"{v:+.2f}%", numeric=v, positive=v >= 0, rank=i + 1)
            for i, (n, v) in enumerate(rows)]


def flows_scene(fii, dii, materiality=None):
    sc = ScenePlan(scene_id="f", scene_type=SceneType.FLOWS,
                   primary_text="FII / DII FLOWS · PROVISIONAL",
                   metadata={"materiality": materiality or {}})
    for label, v in (("FII", fii), ("DII", dii)):
        sc.items.append(EditorialItem(title=label, value=f"{'+' if v >= 0 else '-'}Rs {abs(v):,.0f} cr",
                                      label="NET BUYERS" if v >= 0 else "NET SELLERS", numeric=v,
                                      positive=v >= 0))
    return sc


def large(subject, value):
    return {"subject": subject, "magnitude_state": "LARGE_NET_SELL" if value < 0 else
            "LARGE_NET_BUY", "streak_state": "DIRECTION_CONTINUES", "streak_milestone": False,
            "multiple_of_median": 3.1, "current_value": value, "streak_length": 2}


def normal(subject, value):
    return {"subject": subject, "magnitude_state": "NORMAL_RANGE",
            "streak_state": "DIRECTION_CONTINUES", "streak_milestone": False,
            "multiple_of_median": 0.8, "current_value": value, "streak_length": 2}


def plan(gainers=(("STOCK-E", 1.4),), losers=(("STOCK-F", -1.2),), flows=None, events=()):
    scenes = [ScenePlan(scene_id="g", scene_type=SceneType.GAINERS, items=_items(gainers)),
              ScenePlan(scene_id="l", scene_type=SceneType.LOSERS, items=_items(losers)),
              ScenePlan(scene_id="gl", scene_type=SceneType.GLOBAL, items=_items((("NASDAQ", 0.3),)))]
    if flows is not None:
        scenes.append(flows)
    if events:
        scenes.append(ScenePlan(scene_id="e", scene_type=SceneType.EVENTS,
                                items=[EditorialItem(title=t, label=tag) for t, tag in events]))
    return ShortsPlan(report_id="SYNTH", scenes=scenes)


class _AdmitAll:
    """The selection path under test is editorial, not rights: admit every fact (the existing
    market-events tests use the same stand-in)."""
    profile = type("P", (), {"value": "PRIVATE_ANALYTICS"})()

    def admit(self, fact):
        return True


def trace(p):
    return {t["candidate_id"]: t for t in p.editorial["trace"]}


# =========================================================================== A. PRE vs POST
def _same_pool():
    return [C("X.SECTORS", ea.SECTORS, 2, section="SECTORS", as_of=DAY),
            C("X.STRUCTURE", ea.BREADTH, 2, section="STRUCTURE", as_of=DAY),
            C("X.FLOWS", ea.FLOWS, 2, section="FLOWS", as_of=DAY),
            C("X.MARKET_EVENTS", ea.CALENDAR, 2, section="MARKET_EVENTS", as_of=NEXT),
            C("X.IPO", ea.IPO, 2, section="IPO", as_of=NEXT)]


def test_1_same_pool_gives_edition_appropriate_selection():
    post = ea.arbitrate(_same_pool() + [_anchor("POST")], post_policy())
    pre = ea.arbitrate(_same_pool() + [_anchor("PRE")], pre_policy(DAY))
    assert {"SECTORS", "STRUCTURE", "FLOWS"} <= set(post.order)        # completed-session story
    assert set(pre.order) - {"SETUP"} == {"FLOWS", "MARKET_EVENTS", "IPO"}   # before the open
    assert post.order != pre.order


def test_2_pre_prefers_an_upcoming_catalyst():
    p = plan_pre_sections(synthetic_brief("EVENT"))
    t = trace(p)
    assert t["PRE.EVENT"]["decision"] == "SELECTED"
    # a MATERIAL scheduled event is the lead and plays straight after the previous-session setup
    if t["PRE.EVENT"]["tier"] == 3:
        assert p.order.index("EVENT") == p.order.index("SETUP") + 1


def test_3_post_prefers_completed_session_evidence():
    pool = _same_pool() + [_anchor("POST")]
    d = ea.arbitrate(pool, _policy(slots=2))
    assert set(d.order) == {"PULSE", "SECTORS", "STRUCTURE"}
    assert d.decision("X.MARKET_EVENTS")["decision"] == ea.DROPPED_SLOTS


def test_4_pre_never_uses_current_session_india_data():
    late = C("PRE.SECTORS", ea.SECTORS, 3, section="SECTORS", as_of=NEXT)
    d = ea.arbitrate([_anchor("PRE"), late], pre_policy(DAY))
    assert d.decision("PRE.SECTORS")["decision"] == ea.TEMPORAL_EXCLUDED
    for kind in ("QUIET", "RISK_OFF", "RISK_ON", "EVENT"):
        b = synthetic_brief(kind)
        for row in plan_pre_sections(b).editorial["trace"]:
            if row["family"] in ea.INDIA_SESSION_FAMILIES and row["as_of"]:
                assert dt.date.fromisoformat(row["as_of"]) <= b.previous_session, row


# =========================================================================== B. competition
def test_5_market_structure_displaces_a_weak_mover():
    p = plan_post_sections(pres(pct=0.3), plan(gainers=(("STOCK-E", 7.4),)), [],
                           public=public([breadth(0.30)]))
    t = trace(p)
    assert t["POST.STRUCTURE.BREADTH"]["tier"] == 3 and p.show_movers is True
    d = ea.arbitrate([_anchor(), structure_candidate(breadth(0.30), 0.30, "POST", DAY),
                      C("POST.MOVERS", ea.MOVERS, 2, section="MOVERS")], _policy(slots=1))
    assert d.order == ["PULSE", "STRUCTURE"]
    assert d.decision("POST.MOVERS")["decision"] == ea.DROPPED_SLOTS
    assert d.decision("POST.MOVERS")["displaced_by"] == "POST.STRUCTURE.BREADTH"


def test_6_strong_institutional_flow_displaces_a_routine_event():
    p = plan_post_sections(pres(sectors=()), plan(flows=flows_scene(-9000, 8000, {
        "FII": large("FII", -9000), "DII": large("DII", 8000)})), [],
        public=public(market_events=events_model(gsec())))
    t = trace(p)
    assert t["POST.FLOWS"]["tier"] == 3 and p.show_flows
    assert t["POST.MARKET_EVENTS"]["decision"] == ea.BELOW_THRESHOLD
    assert "MARKET_EVENTS" not in p.order


def test_7_material_market_event_displaces_a_low_breadth_observation():
    p = plan_post_sections(pres(sectors=()), plan(), [],
                           public=public([unusual(9)],
                                         market_events=events_model(market_event("BUYBACK", "OPEN"))))
    t = trace(p)
    assert t["POST.MARKET_EVENTS"]["decision"] == ea.SELECTED
    assert t["POST.STRUCTURE.UNUSUAL_VOLUME"]["decision"] == ea.BELOW_THRESHOLD


def test_8_a_weak_event_receives_no_guaranteed_slot():
    p = plan_post_sections(pres(), plan(), [], public=public(market_events=events_model(gsec())))
    assert "MARKET_EVENTS" not in p.order
    assert "BELOW_EDITION_THRESHOLD" in p.reasons["MARKET_EVENTS"]


def test_9_a_weak_mover_receives_no_guaranteed_slot():
    p = plan_post_sections(pres(), plan(gainers=(("STOCK-E", 3.1),)), [])
    assert not p.show_movers and "MOVERS" not in p.order
    assert "POST.MOVERS" not in trace(p)      # below its value test: never even a candidate


# =========================================================================== C. redundancy
def test_10_same_sector_movers_are_a_repeat_of_the_sector_story():
    p = plan_post_sections(pres(sectors=(("IT", 2.1), ("Metal", -0.4))),
                           plan(gainers=(("STOCK-E", 7.6),)), [],
                           symbol_sectors={"STOCK-E": "IT"})
    row = trace(p)["POST.MOVERS"]
    assert row["decision"] == ea.SUPPRESSED_DUPLICATE and row["duplicate_of"] == "POST.SECTORS"
    other = plan_post_sections(pres(sectors=(("IT", 2.1), ("Metal", -0.4))),
                               plan(gainers=(("STOCK-E", 7.6),)), [],
                               symbol_sectors={"STOCK-E": "Power"})     # a different sector
    assert trace(other)["POST.MOVERS"]["decision"] == ea.SELECTED


def test_11_flow_variants_never_both_take_a_slot():
    pool = [_anchor("PRE"), C("PRE.FLOWS.CDSL", ea.FLOWS, 2, section="FLOWS"),
            C("PRE.FLOWS.NSE_CONTEXT", ea.FLOWS, 2, section="FLOWS")]
    d = ea.arbitrate(pool, pre_policy(DAY))
    assert d.decision("PRE.FLOWS.CDSL")["decision"] == ea.SELECTED          # existing order
    row = d.decision("PRE.FLOWS.NSE_CONTEXT")
    assert row["decision"] == ea.DROPPED_DIVERSITY and row["displaced_by"] == "PRE.FLOWS.CDSL"


def test_12_index_and_aligned_breadth_say_the_same_thing():
    p = plan_post_sections(pres(pct=-1.2), plan(), [],
                           public=public([breadth(-1.2, higher=20, lower=176)]))
    row = trace(p)["POST.STRUCTURE.BREADTH"]
    assert row["decision"] == ea.SUPPRESSED_DUPLICATE and row["duplicate_of"] == "POST.PULSE"
    # on a small index move the same broad move says something the headline does not
    small = plan_post_sections(pres(pct=-0.3), plan(), [],
                               public=public([breadth(-0.3, higher=20, lower=176)]))
    assert trace(small)["POST.STRUCTURE.BREADTH"]["tier"] == 2


# =========================================================================== D. diversity
def test_13_one_family_cannot_dominate():
    d = ea.arbitrate([_anchor(), C("POST.STRUCTURE.A", ea.BREADTH, 2, section="STRUCTURE"),
                      C("POST.STRUCTURE.B", ea.BREADTH, 2, section="STRUCTURE")], post_policy())
    assert d.decision("POST.STRUCTURE.B")["decision"] == ea.DROPPED_DIVERSITY


def test_14_significance_overrides_diversity():
    p = plan_post_sections(pres(pct=0.3), plan(), [],
                           public=public([breadth(0.30), fifty_two(12, "low",
                                                                    "INDEX_UP_BREADTH_WEAK")]))
    t = trace(p)
    assert t["POST.STRUCTURE.BREADTH"]["decision"] == ea.SELECTED
    assert t["POST.STRUCTURE.FIFTY_TWO_WEEK"]["decision"] == ea.SELECTED
    assert len(p.public["STRUCTURE"]) == 2


# =========================================================================== E. budget
def test_15_scene_limit_respected():
    pool = [_anchor()] + [C(f"POST.S{i}", f"F{i}", 2, section=f"S{i}") for i in range(7)]
    d = ea.arbitrate(pool, post_policy())
    shown = [r for r in d.trace if r["decision"] in ea.SHOWN and r["role"] == ea.OPTIONAL]
    assert len(shown) == POST_SLOTS
    dropped = [r for r in d.trace if r["decision"] == ea.DROPPED_SLOTS]
    assert len(dropped) == 7 - POST_SLOTS and all(r["displaced_by"] for r in dropped)


def test_16_duration_budget_respected():
    pol = _policy(runtime=30.0)
    d = ea.arbitrate([_anchor(), C("POST.BIG", "F1", 3, section="BIG", cost=20.0),
                      C("POST.MID", "F2", 2, section="MID", cost=9.0),
                      C("POST.SMALL", "F3", 2, section="SMALL", cost=4.0)], pol)
    assert d.decision("POST.BIG")["decision"] == ea.DROPPED_BUDGET
    assert d.decision("POST.SMALL")["decision"] == ea.SELECTED       # cheaper still fits
    assert d.runtime_estimate <= 30.0


def test_17_required_scenes_always_remain():
    d = ea.arbitrate([_anchor()] + [C(f"POST.S{i}", f"F{i}", 3, section=f"S{i}", cost=30)
                                    for i in range(3)], _policy(runtime=20.0))
    assert d.order[0] == "PULSE"
    assert d.decision("POST.PULSE")["decision"] == ea.SELECTED
    for kind in ("QUIET", "RISK_OFF", "RISK_ON", "EVENT"):
        p = plan_pre_sections(synthetic_brief(kind))
        assert "SETUP" in p.order and "WATCH" in p.order


def test_18_low_priority_trimmed_first():
    d = ea.arbitrate([_anchor(), C("POST.A", "F1", 1, section="A"), C("POST.B", "F2", 2, section="B"),
                      C("POST.C", "F3", 3, section="C")], _policy(slots=2))
    assert d.decision("POST.A")["decision"] == ea.BELOW_THRESHOLD
    assert {"B", "C"} <= set(d.order)
    assert d.lead == "POST.C"


def test_quiet_floor_tops_up_only_to_the_minimum_runtime():
    pol = _policy(min_runtime=20.0)          # fixed 7.6 + headline 5.4 = 13.0 s before stories
    d = ea.arbitrate([_anchor(), C("POST.A", "F1", 1, section="A"),
                      C("POST.A2", "F1", 1, section="A2"),         # same family: never doubled
                      C("POST.B", "F2", 1, section="B"), C("POST.C", "F3", 1, section="C")], pol)
    floor = [r["candidate_id"] for r in d.trace if r["decision"] == ea.QUIET_FLOOR]
    assert floor == ["POST.A", "POST.B"]                # 18.0 s < 20 -> one more, 23.0 s -> stop
    assert d.decision("POST.A2")["decision"] == ea.DROPPED_DIVERSITY
    assert d.decision("POST.C")["decision"] == ea.BELOW_THRESHOLD
    assert d.runtime_estimate >= 20.0


def test_a_refused_headline_is_never_budgeted():
    p = plan_post_sections(pres(sectors=()), plan(), [],
                           public=public(exchange=exchange_model(1)),
                           admit=lambda key, post: key != "PULSE")
    assert "POST.PULSE" not in trace(p) and "PULSE" not in p.order
    assert trace(p)["POST.EXCHANGE"]["decision"] == ea.QUIET_FLOOR


# =========================================================================== F. Market Structure V2
@pytest.mark.parametrize("pct,word,label", [(0.30, "low", "INDEX_UP_BREADTH_WEAK"),
                                            (-0.45, "high", "INDEX_DOWN_BREADTH_RESILIENT")])
def test_19_20_index_vs_breadth_divergence_wins_a_slot(pct, word, label):
    busy = plan(gainers=(("STOCK-E", 7.5),), flows=flows_scene(-1500, 1300))
    p = plan_post_sections(pres(pct=pct), busy, [], public=public([fifty_two(12, word, label)]))
    row = trace(p)["POST.STRUCTURE.FIFTY_TWO_WEEK"]
    assert row["tier"] == 3 and row["decision"] == ea.SELECTED and row["explains_headline"]
    assert p.editorial["lead"] == "POST.STRUCTURE.FIFTY_TWO_WEEK"
    assert p.order.index("STRUCTURE") == p.order.index("PULSE") + 1


def test_21_ordinary_low_count_observation_may_be_omitted():
    c = structure_candidate(fifty_two(6), None, "POST", DAY)
    assert c.tier == ea.ROUTINE
    p = plan_post_sections(pres(), plan(), [], public=public([fifty_two(6)]))
    assert trace(p)["POST.STRUCTURE.FIFTY_TWO_WEEK"]["decision"] == ea.BELOW_THRESHOLD


# =========================================================================== G. Market Events
def test_22_routine_event_omitted():
    assert market_events_candidate(events_model(gsec()), "POST", DAY).tier == ea.ROUTINE


def test_23_material_scheduled_pre_event_selected():
    b = synthetic_brief("EVENT")
    p = plan_pre_sections(b)
    assert p.event is not None and "EVENT" in p.order
    me = dataclasses.replace(synthetic_brief("QUIET"),
                             market_events=events_model(market_event(day=b.pre_date), mode="PRE"))
    assert "MARKET_EVENTS" in plan_pre_sections(me).order


def test_24_meaningful_post_result_selected():
    for fam, status in (("EARNINGS", "SCHEDULED"), ("BUYBACK", "OPEN"), ("OPEN_OFFER", "COMPLETED")):
        p = plan_post_sections(pres(sectors=(("Metal", 0.2), ("IT", -0.1))), plan(), [],
                               public=public(market_events=events_model(market_event(fam, status))))
        assert trace(p)["POST.MARKET_EVENTS"]["decision"] == ea.SELECTED, fam


def test_25_ipo_never_duplicated():
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections
    ipo_ev = market_event("IPO", "OPEN", "NEWCO")
    intel = PublicIntelligence(market_events={"IPO": [ipo_ev], "EARNINGS": [market_event()]},
                               universe_symbols={"SYM1", "NEWCO"})
    ps = plan_public_sections(_AdmitAll(), intel, DAY, "POST", max_structure=0)
    assert all(c["tag"] != "IPO" for c in ps.market_events["cards"])
    p = plan_post_sections(pres(), plan(), [], public=dataclasses.replace(ps, ipo=ipo_model()))
    ipo_rows = [r for r in p.editorial["trace"] if r["family"] == ea.IPO]
    assert len(ipo_rows) == 1 and ipo_rows[0]["section"] == "IPO"


def test_ipo_and_exchange_grading():
    assert ipo_candidate(ipo_model("MAINBOARD", "LISTED"), "POST", DAY).tier == ea.NOTABLE
    assert ipo_candidate(ipo_model("SME", "LISTED"), "POST", DAY).tier == ea.ROUTINE
    assert ipo_candidate(ipo_model("MAINBOARD", "OPENS TODAY"), "PRE", NEXT).tier == ea.NOTABLE
    assert ipo_candidate(ipo_model("MAINBOARD", "BIDDING OPENED"), "POST", DAY).tier == \
        ea.ROUTINE
    assert exchange_candidate(exchange_model(3), "POST", DAY).tier == ea.ROUTINE


def test_26_govt_securities_auction_stays_market_wide():
    m = events_model(gsec())
    assert m["cards"][0]["symbol"] == "" and m["cards"][0]["tag"] == "GOVT AUCTION"
    c = market_events_candidate(m, "POST", DAY)
    assert all(":MARKET:" in t for t in c.topics)
    assert not any(t.startswith("sector:") for t in c.topics)


# =========================================================================== H. data quality
def test_27_stale_candidate_loses_priority():
    d = ea.arbitrate([_anchor(), C("POST.A", "F1", 2, section="A", quality=ea.STALE),
                      C("POST.B", "F2", 2, section="B")], post_policy())
    assert d.decision("POST.A")["effective_tier"] == 1
    assert d.decision("POST.A")["decision"] == ea.BELOW_THRESHOLD
    assert d.decision("POST.B")["decision"] == ea.SELECTED


def test_28_source_failure_candidate_excluded():
    d = ea.arbitrate([_anchor(), C("POST.A", "F1", 3, section="A", quality=ea.SOURCE_FAILURE)],
                     post_policy())
    assert d.decision("POST.A")["decision"] == ea.DATA_QUALITY and "A" not in d.order


def test_29_partial_is_demoted_one_tier():
    p = structure_candidate(fifty_two(12, "low", "INDEX_UP_BREADTH_WEAK"), 0.3, "POST", DAY)
    partial = dataclasses.replace(p, quality=ea.PARTIAL, candidate_id="POST.STRUCTURE.P")
    d = ea.arbitrate([_anchor(), partial, C("POST.SECTORS", ea.SECTORS, 3, section="SECTORS")],
                     _policy(slots=1))
    assert d.decision("POST.STRUCTURE.P")["effective_tier"] == 2
    assert d.decision("POST.STRUCTURE.P")["decision"] == ea.DROPPED_SLOTS   # full data wins
    weak = dataclasses.replace(C("POST.W", "F9", 2, section="W"), quality=ea.PARTIAL)
    assert ea.arbitrate([_anchor(), weak, C("POST.B", "F2", 2, section="B")],
                        post_policy()).decision("POST.W")["decision"] == ea.BELOW_THRESHOLD


def test_30_healthy_empty_creates_no_fake_story():
    p = plan_post_sections(pres(), plan(), [], public=public())
    families = {r["family"] for r in p.editorial["trace"]}
    assert not families & {ea.BREADTH, ea.CALENDAR, ea.IPO, ea.EXCHANGE}
    assert p.public == {}


# =========================================================================== I. trace
def _busy_trace():
    return ea.arbitrate(
        [_anchor(), C("POST.SECTORS", ea.SECTORS, 2, topics=frozenset({"sector:IT", "x"})),
         C("POST.MOVERS", ea.MOVERS, 2, topics=frozenset({"sector:IT"})),
         C("POST.BIG", "F1", 3, section="BIG", cost=60),
         C("POST.LOW", "F2", 1, section="LOW")], post_policy())


def test_31_selected_reason_populated():
    row = _busy_trace().decision("POST.SECTORS")
    assert row["decision"] == ea.SELECTED and row["reason"] and row["runtime_after"]


def test_32_suppressed_reason_populated():
    d = _busy_trace()
    for cid in ("POST.MOVERS", "POST.BIG", "POST.LOW"):
        row = d.decision(cid)
        assert row["decision"] not in ea.SHOWN and row["reason"]
        assert "omitted" in ea.trace_sentence(row)


def test_33_duplicate_relationship_visible():
    row = _busy_trace().decision("POST.MOVERS")
    assert row["decision"] == ea.SUPPRESSED_DUPLICATE and row["duplicate_of"] == "POST.SECTORS"


def test_34_budget_rejection_visible():
    row = _busy_trace().decision("POST.BIG")
    assert row["decision"] == ea.DROPPED_BUDGET and row["runtime_after"] is not None
    assert row["cost_s"] == 60


def test_trace_rows_carry_every_documented_field():
    keys = {"candidate_id", "section", "family", "role", "tier", "effective_tier", "quality",
            "relevance", "explains_headline", "cost_s", "decision", "reason", "duplicate_of",
            "displaced_by", "runtime_after"}
    for row in _busy_trace().trace:
        assert keys <= set(row)


def test_arbiter_is_deterministic_and_order_independent():
    pool = [_anchor(), *[C(f"POST.S{i}", f"F{i % 3}", 1 + i % 3, section=f"S{i}") for i in range(8)]]
    a = ea.arbitrate(pool, post_policy()).to_dict()
    b = ea.arbitrate(list(reversed(pool)), post_policy()).to_dict()
    assert a["order"] == b["order"] and a["selected"] == b["selected"]


# =========================================================================== costs
def test_candidate_costs_equal_the_real_scene_durations():
    from daily_video.public_storyboard import (exchange_spec, ipo_spec, market_events_spec,
                                               structure_spec)
    from daily_video import storyboard as sbm
    from presentation import post_plan as pp
    for ins in (breadth(), fifty_two(), unusual(concentrated=True)):
        assert structure_cost(ins) == structure_spec(ins, LINES).duration
    m = events_model(market_event(), gsec())
    assert cards_cost(m) == market_events_spec(m).duration
    assert ipo_cost(ipo_model()) == ipo_spec(ipo_model()).duration
    assert cards_cost(exchange_model(2)) == exchange_spec(exchange_model(2)).duration
    p = plan_post_sections(pres(cross="down"), plan(gainers=(("STOCK-E", 7.5),)), [])
    assert pp.DUR["PULSE"] == sbm._pulse_scene(p.pulse).duration
    assert pp.sectors_cost(2) == sbm._sectors_scene(p.sectors).duration
    assert pp.DUR["MOVERS"] == sbm._movers_scene(p.movers).duration
    st = plan_post_sections(pres(cross="down"), plan(), []).structure
    assert pp.DUR["NIFTY"] == sbm._structure_scene(st).duration


# =========================================================================== scenarios (storyboard)
@pytest.fixture
def storyboard(monkeypatch):
    """build_storyboard with the public sections supplied (as plan_public_sections would have
    admitted them) - no snapshot files, no network."""
    import presentation.public_intelligence as pi
    from daily_video import storyboard as sbm

    def build(p, pr, ps, profile="PUBLIC_UNREGISTERED", symbol_sectors=None):
        monkeypatch.setattr(pi, "plan_public_sections", lambda *a, **k: ps)
        intel = SimpleNamespace(known_securities={}, symbol_sectors=symbol_sectors or {})
        return sbm.build_storyboard(p, pr, profile=profile, intelligence=intel)
    return build


def scenario_a():
    """Nifty +0.30%, most of the NIFTY 200 fell (INDEX_UP_BREADTH_WEAK), a routine SME IPO, weak
    movers, strong FII flows."""
    return (plan(flows=flows_scene(-6200, 5400, {"FII": large("FII", -6200),
                                                 "DII": large("DII", 5400)})),
            pres(pct=0.30, sectors=(("IT", 1.4), ("Bank", 0.6), ("Metal", -1.1))),
            public([breadth(0.30), fifty_two(12, "low", "INDEX_UP_BREADTH_WEAK")],
                   ipo=ipo_model("SME")))


def scenario_b():
    """Nifty -1.2%, broad 52-week lows, heavy FII selling, an RBI policy day, large losers and a
    completed T-Bill auction."""
    return (plan(losers=(("STOCK-F", -8.4),), events=(("RBI policy decision", "RBI"),),
                 flows=flows_scene(-9100, 7000, {"FII": large("FII", -9100),
                                                 "DII": large("DII", 7000)})),
            pres(pct=-1.2, cross="down", sectors=(("FMCG", -0.2), ("Bank", -1.9), ("Realty", -3.1))),
            public([breadth(-1.2, higher=18, lower=180), fifty_two(31, "low",
                                                                     "INDEX_AND_BREADTH_ALIGNED_WEAK")],
                   market_events=events_model(gsec("COMPLETED"))))


def scenario_c():
    """A quiet session: Nifty +0.05%, small sector moves, ordinary flows, no events."""
    return (plan(flows=flows_scene(-400, 350, {"FII": normal("FII", -400),
                                               "DII": normal("DII", 350)})),
            pres(pct=0.05, sectors=(("IT", 0.3), ("Metal", -0.1))), public())


def _optional(sb):
    return [s.section for s in sb.scenes if s.section not in ("HOOK", "PULSE", "CLOSING")]


def _assert_safe(sb):
    text = sb.public_text()
    assert scan_publication(text).status is SafetyStatus.SAFE
    joined = " ".join(text.values())
    for word in ("buy", "sell", "hold", "target", "stop loss", "accumulate", "avoid"):
        assert not re.search(rf"\b{word}\b", joined, re.I), word
    for fam in ("recommendation", "prediction", "causal"):
        for line in text.values():
            assert not any(p.search(line) for p in _COMPILED[fam]), (fam, line)


def test_scenario_a_coherent_limited_story(storyboard):
    sb = storyboard(*scenario_a())
    post = sb.post_plan
    t = {r["candidate_id"]: r for r in post["editorial"]["trace"]}
    assert t["POST.STRUCTURE.BREADTH"]["decision"] == ea.SELECTED          # the tension story
    assert t["POST.FLOWS"]["decision"] == ea.SELECTED                       # strong flows
    assert t["POST.IPO"]["decision"] == ea.BELOW_THRESHOLD                  # routine SME IPO
    assert "POST.MOVERS" not in t                                           # weak movers
    assert len(_optional(sb)) <= POST_SLOTS + 1 and "runtime safety net" not in str(post["notes"])
    assert sb.total_duration <= 62.0
    # Hook V3: the lead tension story opens the Short
    assert sb.hook_plan["candidate_id"] == "post-breadth-divergence", sb.hook_plan["candidate_id"]
    assert sb.public_audit["omitted_sections"]["IPO_WATCH"]["code"] == "EDITORIAL_CAP"
    _assert_safe(sb)


def test_scenario_b_redundancy_and_priority(storyboard):
    sb = storyboard(*scenario_b())
    t = {r["candidate_id"]: r for r in sb.post_plan["editorial"]["trace"]}
    # the RBI line comes from news headlines: never a public fact source, refused before it
    # could take a slot (it competes - and leads - only in a private render)
    assert t["POST.EVENT"]["decision"] == ea.BLOCKED and t["POST.EVENT"]["tier"] == 3
    assert t["POST.FLOWS"]["decision"] == ea.SELECTED and t["POST.FLOWS"]["tier"] == 3
    # a broad fall on a -1.2% day repeats the headline: suppressed, not shown twice
    assert t["POST.STRUCTURE.BREADTH"]["decision"] == ea.SUPPRESSED_DUPLICATE
    # named losers are a security ranking in public: refused BEFORE they could take a slot
    assert t["POST.MOVERS"]["decision"] == ea.BLOCKED
    assert t["POST.MARKET_EVENTS"]["decision"] in (ea.BELOW_THRESHOLD, ea.DROPPED_DIVERSITY)
    assert sum(1 for s in sb.scenes if s.kind == "STRUCTURE") <= 1
    assert sb.total_duration <= 62.0 and not sb.post_plan["notes"]
    _assert_safe(sb)


def test_scenario_c_quiet_market_is_shorter(storyboard):
    quiet = storyboard(*scenario_c())
    busy = storyboard(*scenario_a())
    assert len(_optional(quiet)) == 1                       # the quiet floor: one story, no padding
    assert quiet.total_duration < busy.total_duration
    assert quiet.total_duration >= 15.0                     # editorial.MIN_SHORT_DURATION
    t = {r["candidate_id"]: r for r in quiet.post_plan["editorial"]["trace"]}
    assert t["POST.SECTORS"]["decision"] == ea.QUIET_FLOOR
    assert t["POST.FLOWS"]["decision"] == ea.NOT_MATERIAL
    _assert_safe(quiet)


def test_private_profile_keeps_movers_and_events_competing(storyboard):
    sb = storyboard(*scenario_b(), profile="PRIVATE_ANALYTICS")
    t = {r["candidate_id"]: r for r in sb.post_plan["editorial"]["trace"]}
    assert t["POST.MOVERS"]["decision"] != ea.BLOCKED
    assert t["POST.EVENT"]["decision"] == ea.SELECTED
    lead = sb.post_plan["editorial"]["lead"]
    assert {r["candidate_id"]: r for r in sb.post_plan["editorial"]["trace"]}[lead]["tier"] == 3
    # the MATERIAL lead plays straight after the headline (the pulse, or first when the hook
    # already said Nifty's move and the pulse was de-duplicated)
    assert sb.post_plan["order"].index(sb.post_plan["editorial"]["lead_section"]) <= 1


def test_manifest_carries_the_editorial_trace(storyboard):
    from products.post_unified import manifest
    sb = storyboard(*scenario_a())
    m = manifest(entry_point="t", report_source="t", report_id="r", session=DAY, sb=sb,
                 audit={"final": "PASS", "failed_checks": []}, upload_status="NONE",
                 video_path=None, qa={})
    assert m["editorial_trace"]["policy"] == "POST" and m["editorial_trace"]["trace"]


# =========================================================================== PRE scenarios
def test_pre_quiet_morning_stays_short_and_unpadded():
    p = plan_pre_sections(synthetic_brief("QUIET"))
    assert p.order == ["OVERNIGHT", "SETUP", "WATCH"]
    assert not any(r["decision"] == ea.QUIET_FLOOR for r in p.editorial["trace"])


def test_pre_structure_and_catalysts_compete_in_one_budget():
    b = synthetic_brief("EVENT")
    b = dataclasses.replace(
        b, structure=[(fifty_two(12, "low", "INDEX_UP_BREADTH_WEAK"), LINES)],
        market_events=events_model(gsec(day=b.pre_date), mode="PRE"),
        exchange_watch=exchange_model(1), ipo_watch=ipo_model("MAINBOARD", "OPENS TODAY"))
    p = plan_pre_sections(b)
    optional = [k for k in p.order if k not in ("OVERNIGHT", "SETUP", "WATCH")]
    assert len(optional) <= PRE_SLOTS
    t = trace(p)
    assert t["PRE.MARKET_EVENTS"]["decision"] != ea.SELECTED       # a routine T-Bill auction
    assert t["PRE.EXCHANGE"]["decision"] != ea.SELECTED
    assert t["PRE.IPO"]["tier"] == 2
    from daily_video.pre_storyboard import build_pre_storyboard
    from presentation.pre_plan import pre_language_issues
    sb = build_pre_storyboard(b, p)
    assert pre_language_issues(sb.public_text()) == []
    assert sb.public_audit["omitted_sections"]["MARKET_EVENTS"]["code"] == "EDITORIAL_CAP"


def test_pre_unselected_story_is_merged_into_watch_when_a_card_carries_it():
    p = plan_pre_sections(synthetic_brief("EVENT"))
    merged = [r for r in p.editorial["trace"] if r["decision"] == ea.MERGED_INTO]
    cats = {w.category for w in p.watch}
    for r in merged:
        assert r["merged_into"] == "WATCH"
        assert {"PRE.VIX": "VIX", "PRE.SECTORS": "SECTOR", "PRE.EVENT": "EVENT",
                "PRE.STOCK_WATCH": "STOCK"}.get(r["candidate_id"], "FLOWS") in cats


# =========================================================================== hook V3
def test_divergence_hook_line_is_validated_and_factual():
    from hooks import plan_hook, post_market_sheet
    from hooks.candidates import build_candidates
    from publication.public_hooks import add_structure_facts
    p, pr, _ = scenario_a()
    sheet = post_market_sheet(p, pr, (), None, ["PULSE", "STRUCTURE", "FLOWS"], "Nifty 100")
    add_structure_facts(sheet, [breadth(0.30)], nifty_pct=0.30)
    c = next(c for c in build_candidates(sheet) if c.candidate_id == "post-breadth-divergence")
    assert c.curiosity_line == "Nifty rose 0.30%. 128 of 200 NIFTY 200 stocks fell."
    for fam in ("causal", "prediction", "recommendation"):
        assert not any(pat.search(c.curiosity_line) for pat in _COMPILED[fam])
    hp = plan_hook(sheet)
    assert hp.candidate_id


def test_lead_bonus_only_for_a_material_lead():
    from hooks import post_market_sheet
    from hooks.candidates import LEAD_STORY_BONUS, build_candidates
    p, pr, _ = scenario_a()
    base = post_market_sheet(p, pr, (), None, ["PULSE", "SECTORS", "FLOWS"], "Nifty 100")
    scores = {c.candidate_id: c.score for c in build_candidates(base)}
    led = post_market_sheet(p, pr, (), None, ["PULSE", "SECTORS", "FLOWS"], "Nifty 100")
    led.metadata = dict(led.metadata, lead_section="FLOWS")
    boosted = {c.candidate_id: c.score for c in build_candidates(led)}
    for cid, s in boosted.items():
        want = scores[cid] + (LEAD_STORY_BONUS if cid == "post-flow-contrast" else 0)
        assert s == pytest.approx(want), cid


def test_big_move_priority_is_preserved():
    from hooks import fixtures as F
    from hooks.candidates import build_candidates
    from hooks import post_market_sheet
    fx = F.post_big_move()
    sheet = post_market_sheet(fx["plan"], fx["pres"], fx["stories"], fx["evidence"],
                              fx["sections"])
    sheet.metadata = dict(sheet.metadata, lead_section="SECTORS")
    found = build_candidates(sheet)
    assert found[0].archetype.value == "BIG_MOVE"


# =========================================================================== K. replay
def test_38_39_arbiter_and_candidates_are_pure():
    import presentation.editorial_candidates as ec
    for mod in (ea, ec):
        tree = ast.parse(inspect.getsource(mod))
        names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(n and n.split(".")[0] in {"requests", "news", "providers", "storage",
                                                 "market_events", "institutional_flows",
                                                 "official_snapshots", "sqlite3", "urllib",
                                                 "hooks", "os"} for n in names), names
    for row in plan_post_sections(pres(), plan(), [], public=public([breadth(0.3)])).editorial["trace"]:
        assert row["as_of"] is None or dt.date.fromisoformat(row["as_of"]) <= DAY


def test_40_a_later_revision_never_leaks_into_an_earlier_replay(tmp_path):
    from test_market_events_boundary import _gsa_synthetic_fetcher

    from market_events.context import load_market_events
    from market_events.service import MarketEventsService
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    svc = MarketEventsService(str(tmp_path),
                              fetchers={"GOVT_SECURITIES_AUCTION": _gsa_synthetic_fetcher()})
    t0 = dt.datetime(2026, 10, 1, 19, 30, tzinfo=IST)
    t1 = dt.datetime(2026, 10, 3, 19, 30, tzinfo=IST)
    t2 = dt.datetime(2026, 10, 9, 19, 30, tzinfo=IST)
    for t in (t0, t1, t2):
        svc.capture(t, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    day = dt.date(2026, 10, 9)
    ctx = load_market_events(str(tmp_path), cutoff=t1 + dt.timedelta(hours=12), live=False,
                             on_or_before=day, families=("GOVT_SECURITIES_AUCTION",))
    intel = PublicIntelligence(market_events=ctx.events)
    ps = plan_public_sections(_AdmitAll(), intel, day, "PRE", max_structure=0)
    text = " ".join(c["line"] for c in (ps.market_events or {}).get("cards", []))
    assert "cut-off yield" not in text                        # the result is not known yet
    b = dataclasses.replace(synthetic_brief("QUIET"), market_events=ps.market_events)
    p = plan_pre_sections(b)
    row = trace(p).get("PRE.MARKET_EVENTS")
    assert row is None or "COMPLETED" not in row["reason"]


# =========================================================================== Private Desk
def test_private_desk_card_reads_the_latest_traces_read_only(tmp_path, storyboard):
    import json

    from private_desk.repository import DeskRepository
    from private_desk.services.quality import editorial_decisions
    sb = storyboard(*scenario_a())
    post_dir = tmp_path / "post" / "2026-10-06"
    post_dir.mkdir(parents=True)
    (post_dir / "production_manifest.json").write_text(
        json.dumps({"editorial_trace": sb.post_plan["editorial"]}), encoding="utf-8")
    pre_dir = tmp_path / "pre_shadow" / "2026-10-07"
    pre_dir.mkdir(parents=True)
    plan_dict = plan_pre_sections(synthetic_brief("EVENT")).to_dict()
    (pre_dir / "pre_section_plan_2026-10-07.json").write_text(json.dumps(plan_dict, default=str),
                                                              encoding="utf-8")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    cards = {c["edition"]: c for c in editorial_decisions(DeskRepository(str(tmp_path)))}
    assert sorted(p.name for p in tmp_path.rglob("*")) == before        # nothing written
    assert cards["POST"]["status"] == "OK" and cards["PRE"]["status"] == "OK"
    rows = {r["candidate_id"]: r for r in cards["POST"]["rows"]}
    assert rows["POST.IPO"]["decision"] == ea.BELOW_THRESHOLD and rows["POST.IPO"]["reason"]
    assert cards["PRE"]["lead"]
    empty = editorial_decisions(DeskRepository(str(tmp_path / "nothing")))
    assert {c["status"] for c in empty} == {"NO_ARTIFACT"}

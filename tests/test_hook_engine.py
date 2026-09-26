"""Dynamic Hook Engine (hooks/): candidates, strict validation, Gemini choice + fallback.

Fully offline. Gemini is always a scripted client; the one test that exercises the real client
wiring stubs `news.ask_gemini`, and a guard asserts no HTTP call is ever made.
"""
import dataclasses
import datetime as dt
import json
import os

import pytest

import hooks
from hooks import (Archetype, HookMode, HookSource, custom_stock_sheet, plan_hook,
                   post_market_sheet, pre_market_sheet, timing_for)
from hooks import gemini as hg
from hooks import policy
from hooks.candidates import build_candidates, emergency_candidate
from hooks.fixtures import CUSTOM_EXAMPLES, POST_EXAMPLES, PRE_EXAMPLES
from hooks.models import HookFactSheet
from hooks.sheet_common import session_fact
from hooks.validation import check_line, check_structure

REPORT = "output/reports/premarket_2026-09-23.json"
RADAR_RESULT = "output/radar/daily_radar_2026-09-21.json"

EXPECTED = {
    "post_quiet": Archetype.QUIET_MARKET_HIDDEN_ACTION,
    "post_sector_contrast": Archetype.CONTRAST,
    "post_radar_unusual": Archetype.UNUSUAL_ACTIVITY,
    "post_big_move": Archetype.BIG_MOVE,
    "pre_overnight": Archetype.OVERNIGHT_CUE,
    "pre_event": Archetype.EVENT_LED,
    "custom_breakout": Archetype.UNUSUAL_ACTIVITY,
    "custom_weak_tech_strong_fund": Archetype.CONTRAST,
}


def _sheet(name):
    if name in POST_EXAMPLES:
        b = POST_EXAMPLES[name]()
        return post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                                 b["universe"])
    if name in PRE_EXAMPLES:
        return pre_market_sheet(PRE_EXAMPLES[name]())
    return custom_stock_sheet(CUSTOM_EXAMPLES[name]())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("the hook engine must not reach the network in tests")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def real_sheet():
    if not (os.path.exists(REPORT) and os.path.exists(RADAR_RESULT)):
        pytest.skip("real validation artifacts not present")
    import render_daily_market_byte as r
    plan, pres, rp, rr, ev, uni, _ = r.load_inputs(REPORT, "output/radar", "2026-09-21")
    by = {s["instrument"]: s for s in rr["stories"]}
    stories = [(sc["story"], by[sc["story"]["instrument"]]) for sc in rp["scenes"]
               if sc.get("role") == "STORY"]
    return post_market_sheet(plan, pres, stories, ev,
                             ["PULSE", "NIFTY", "SECTORS", "MOVERS", "RADAR", "AHEAD"], uni)


def _reply(cand, **over):
    obj = {"candidate_id": cand.candidate_id, "archetype": cand.archetype.value,
           "hero_visual": cand.default_hero.value, "teaser_beats": list(cand.default_beats),
           "curiosity_line": cand.curiosity_line, "summary_line": cand.summary_line,
           "fact_ids_used": list(cand.fact_ids)}
    obj.update(over)
    return json.dumps(obj, ensure_ascii=False)


# --------------------------------------------------------------------------- candidates
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_example_selects_its_archetype(name):
    sheet = _sheet(name)
    cands = build_candidates(sheet)
    assert 1 <= len(cands) <= policy.MAX_CANDIDATES
    assert cands[0].archetype is EXPECTED[name]
    assert [c.score for c in cands] == sorted((c.score for c in cands), reverse=True)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_offered_template_passes_the_same_validator(name):
    sheet = _sheet(name)
    for c in build_candidates(sheet):
        assert not check_line(c.curiosity_line, "curiosity", sheet, c, c.default_beats)
        assert not check_line(c.summary_line, "summary", sheet, c, c.default_beats)
        assert c.default_hero in c.heroes and c.heroes[c.default_hero]
        assert all(sheet.beat(b) for b in c.default_beats)
        assert len(set(c.default_beats)) == len(c.default_beats)


def test_real_post_is_quiet_market_hidden_action(real_sheet):
    hp = plan_hook(real_sheet, use_ai=False)
    assert hp.archetype is Archetype.QUIET_MARKET_HIDDEN_ACTION
    assert hp.curiosity_line == "Nifty moved just +0.29%. Five stocks underneath didn't."
    assert hp.source is HookSource.DETERMINISTIC
    assert 2 <= len(hp.teaser_beats) <= 3


def test_candidate_facts_come_from_the_sheet_only(real_sheet):
    ids = {f.fact_id for f in real_sheet.facts}
    for c in build_candidates(real_sheet):
        assert set(c.fact_ids) <= ids


def test_plan_is_deterministic(real_sheet):
    a = plan_hook(real_sheet, use_ai=False).to_dict()
    b = plan_hook(real_sheet, use_ai=False).to_dict()
    assert a == b


# --------------------------------------------------------------------------- validator: text
@pytest.mark.parametrize("line,needle", [
    ("Nifty +0.3%. MANKIND jumped 6.0%.", "not in any supplied fact"),           # rounding
    ("Pharma +6.0% led the market.", "not a fact about"),                       # misattributed
    ("Sensex barely moved. Five stocks didn't.", "not an entity"),              # unknown entity
    ("Metal led while Nifty moved just +0.29%.", "names sector 'Metal'"),       # absent sector
    ("Nifty rose 0.29% as Pharma rallied on hopes.", "causal"),
    ("Nifty moved +0.29% because MANKIND broke out.", "causal"),
    ("MANKIND could break out further tomorrow.", "prediction"),
    ("MANKIND will be a stock worth watching.", "recommendation"),
    ("Buy MANKIND after its 6.0% move.", "recommendation"),
    ("MANKIND surged 6.0% in a massive breakout.", "hype"),
    ("OFSS -8.3%: the biggest drop in 20 sessions.", "needs a"),                # superlative
    ("Nifty fell 0.29% on Monday.", "contradicts"),                            # direction
    ("Nifty -0.29% while five stocks moved more.", "sign"),                    # sign
    ("Five sector indices fell while Nifty moved just +0.29%.", "not a fact"), # count misuse
    ("Nifty moved just +0.29%. Five stocks underneath didn't, and that is really something.",
     "chars"),
    ("Nifty moved just +0.29% 🚀", "characters"),
])
def test_curiosity_rejections(real_sheet, line, needle):
    c = build_candidates(real_sheet)[0]
    issues = check_line(line, "curiosity", real_sheet, c, c.default_beats)
    assert any(needle in i for i in issues), issues


@pytest.mark.parametrize("line", [
    "Nifty looked quiet. Activity underneath was not.",
    "Nifty +0.29%, but MANKIND jumped 6.0% on 4.1× volume.",
    "It was a quiet day for Nifty, just +0.29%.",
])
def test_curiosity_acceptances(real_sheet, line):
    c = build_candidates(real_sheet)[0]
    assert not check_line(line, "curiosity", real_sheet, c, c.default_beats)


def test_summary_may_only_promise_sections_the_short_contains(real_sheet):
    c = build_candidates(real_sheet)[0]
    bad = check_line("Inside: FII/DII flows and 5 Radar stocks.", "summary", real_sheet, c)
    assert any("promises" in i for i in bad)
    none = check_line("Inside: some interesting things.", "summary", real_sheet, c)
    assert any("does not say" in i for i in none)
    assert not check_line("Inside: every sector, top movers and 5 Radar stocks.", "summary",
                          real_sheet, c)


def test_brief_example_wording_is_rejected_for_recommendation_tone(real_sheet):
    """The brief's own sample ends "... 3 Radar stocks worth seeing" - "worth seeing" reads as a
    recommendation, and the day had 5 Radar stocks, not 3."""
    c = build_candidates(real_sheet)[0]
    issues = check_line("Here's what moved, which sectors led, and 3 Radar stocks worth seeing.",
                        "summary", real_sheet, c)
    assert any("recommendation" in i for i in issues)
    assert any("'3'" in i for i in issues)


# --------------------------------------------------------------------------- validator: structure
@pytest.mark.parametrize("over,needle", [
    ({"candidate_id": "made-up"}, "unknown candidate_id"),
    ({"archetype": "BIG_MOVE"}, "archetype"),
    ({"hero_visual": "NEON_ROCKET"}, "hero_visual"),
    ({"teaser_beats": ["NIFTY_CLOSE", "SENSEX_CHART"]}, "unknown teaser beat"),
    ({"teaser_beats": ["NIFTY_CLOSE"]}, "need 2-3"),
    ({"teaser_beats": ["NIFTY_CLOSE", "SECTOR_LEADER", "TOP_GAINER", "TOP_LOSER"]}, "need 2-3"),
    ({"teaser_beats": ["NIFTY_CLOSE", "NIFTY_CLOSE"]}, "repeats"),
    ({"fact_ids_used": ["fact.invented"]}, "unknown fact id"),
])
def test_structure_rejections(real_sheet, over, needle):
    cands = build_candidates(real_sheet)
    issues, _ = check_structure(json.loads(_reply(cands[0], **over)), real_sheet, cands)
    assert any(needle in i for i in issues), issues


# --------------------------------------------------------------------------- engine paths
def test_valid_gemini_reply_is_used(real_sheet):
    cands = build_candidates(real_sheet)
    alt = next(c for c in cands if c.archetype is Archetype.UNUSUAL_ACTIVITY)
    reply = _reply(alt, curiosity_line="Nifty +0.29%. MANKIND +6.0% on 4.1× normal volume.",
                   teaser_beats=["NIFTY_CLOSE", "SECTOR_CONTRAST", "RADAR_SWEEP"])
    hp = plan_hook(real_sheet, client=lambda p, s: reply)
    assert hp.source is HookSource.GEMINI
    assert hp.archetype is Archetype.UNUSUAL_ACTIVITY
    assert hp.curiosity_line.startswith("Nifty +0.29%. MANKIND")
    assert hp.teaser_beats == ["NIFTY_CLOSE", "SECTOR_CONTRAST", "RADAR_SWEEP"]


def test_valid_choice_with_bad_lines_keeps_choice_and_uses_template_lines(real_sheet):
    cands = build_candidates(real_sheet)
    alt = cands[1]
    reply = _reply(alt, curiosity_line="MANKIND rallied on hopes and could run further.")
    hp = plan_hook(real_sheet, client=lambda p, s: reply)
    assert hp.source is HookSource.GEMINI_CHOICE_TEMPLATE_TEXT
    assert hp.candidate_id == alt.candidate_id
    assert hp.curiosity_line == alt.curiosity_line
    assert hp.validation_issues


@pytest.mark.parametrize("client", [
    None,                                                   # no key configured
    lambda p, s: None,                                      # 429 / network / API error
    lambda p, s: "Sure! Nifty flat, stocks wild!",          # not JSON
    lambda p, s: "[1, 2, 3]",                               # JSON, not an object
    lambda p, s: json.dumps({"candidate_id": "x"}),         # structurally invalid
])
def test_gemini_failures_fall_back_to_deterministic(real_sheet, client):
    hp = plan_hook(real_sheet, client=client)
    top = build_candidates(real_sheet)[0]
    assert hp.source is HookSource.DETERMINISTIC
    assert hp.candidate_id == top.candidate_id and hp.curiosity_line == top.curiosity_line
    assert hp.fallback_reason


def test_gemini_exception_never_costs_the_video(real_sheet):
    def explode(prompt, schema):
        raise RuntimeError("503 from Gemini")
    hp = plan_hook(real_sheet, client=explode)
    assert hp.source is HookSource.DETERMINISTIC and "RuntimeError" in hp.fallback_reason


def test_no_key_means_no_model_call(real_sheet):
    hp = plan_hook(real_sheet)             # default client, GEMINI_API_KEY unset
    assert hp.source is HookSource.DETERMINISTIC
    assert hp.fallback_reason == "no Gemini key configured"


def test_default_client_is_structured_and_ungrounded(monkeypatch, real_sheet):
    import news
    seen = {}

    def fake(prompt, **kw):
        seen.update(kw)
        return None
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(news, "ask_gemini", fake)
    plan_hook(real_sheet)
    assert seen["search"] is False
    assert seen["generation_config"]["responseMimeType"] == "application/json"
    assert "responseSchema" in seen["generation_config"]
    assert seen["tries"] <= 2


def test_ask_gemini_defaults_unchanged(monkeypatch):
    """The two new keyword arguments are optional; existing callers send the same body."""
    import news
    import requests
    bodies = []

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(requests, "post", lambda url, json=None, **kw: bodies.append((json, kw)) or R())
    assert news.ask_gemini("hi") == "ok"
    body, kw = bodies[0]
    assert body["generationConfig"] == {"temperature": 0.2}
    assert body["tools"] == [{"google_search": {}}] and kw["timeout"] == 180


def test_prompt_and_schema_offer_only_approved_choices(real_sheet):
    cands = build_candidates(real_sheet)
    schema = hg.response_schema(real_sheet, cands)
    props = schema["properties"]
    assert props["candidate_id"]["enum"] == [c.candidate_id for c in cands]
    assert set(props["teaser_beats"]["items"]["enum"]) == {b.beat_id for b in real_sheet.beats}
    assert props["teaser_beats"]["maxItems"] == 3 and props["teaser_beats"]["minItems"] == 2
    prompt = hg.build_prompt(real_sheet, cands)
    payload = json.loads(prompt.split("INPUT:\n", 1)[1])
    assert {f["fact_id"] for f in payload["facts"]} == {f.fact_id for f in real_sheet.facts}
    assert "series" not in prompt and "chart_df" not in prompt      # no raw data to compute from
    for word in ("Never predict", "Never recommend", "Never say why"):
        assert word in prompt


# --------------------------------------------------------------------------- timing / edges
@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_timing_stays_within_five_seconds(n):
    tm = timing_for(n)
    assert tm.total <= policy.MAX_TOTAL_SECONDS
    assert tm.total - tm.settle_start >= 1.8          # the settled hook holds long enough to read


def test_empty_sheet_still_produces_a_hook():
    import datetime as dt
    sheet = HookFactSheet(mode=HookMode.POST_MARKET, session_date=dt.date(2026, 9, 21),
                          facts=[session_fact(dt.date(2026, 9, 21))], beats=[],
                          sections=["PULSE"], section_labels=["Market"])
    hp = plan_hook(sheet, use_ai=False)
    assert hp.curiosity_line and hp.summary_line
    assert hp.teaser_beats == [] and hp.timing.total <= policy.MAX_TOTAL_SECONDS


def test_emergency_candidate_passes_validation():
    import datetime as dt
    sheet = HookFactSheet(mode=HookMode.POST_MARKET, session_date=dt.date(2026, 9, 21),
                          facts=[session_fact(dt.date(2026, 9, 21))], beats=[],
                          sections=["PULSE"], section_labels=["Market"])
    c = emergency_candidate(sheet)
    assert not check_line(c.curiosity_line, "curiosity", sheet, c)


def test_synthetic_fixtures_use_placeholder_stocks():
    for name in EXPECTED:
        sheet = _sheet(name)
        for f in sheet.facts:
            if f.kind != "COUNT" and (f.kind.startswith("STOCK") or
                                      f.fact_id.startswith(("radar.", "mover.", "stock."))):
                assert f.entity.startswith("STOCK-"), f.entity


def test_hook_package_acquires_nothing():
    pkg = os.path.dirname(hooks.__file__)
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            src = open(os.path.join(pkg, fn), encoding="utf-8").read()
            for banned in ("import requests", "yfinance", "import market", "from market",
                           "urllib", "providers"):
                assert banned not in src, (fn, banned)


# --------------------------------------------------------------------------- multi-session finding
def _sector_day(pcts):
    from types import SimpleNamespace
    pres = SimpleNamespace(m={"pct": -1.6, "close": 23063.1}, session_date=dt.date(2026, 9, 24),
                           sec=[{"name": n, "pct": p} for n, p in pcts])
    return post_market_sheet(SimpleNamespace(scenes=[]), pres, [], None,
                             ["PULSE", "SECTORS", "RADAR"], "Nifty 100")


def test_a_falling_sector_is_never_licensed_as_leader():
    """Found on the real 24 Sep 2026 session: every sector fell, and the hook's teaser slip
    said LEADER over IT -0.44% - and the 'leader' claim would have licensed "IT led"."""
    sheet = _sector_day([("IT", -0.44), ("Pharma", -0.45), ("Bank", -1.96)])
    it = sheet.fact("sector.IT")
    assert "leader" not in it.claims
    assert sheet.beat("SECTOR_CONTRAST").payload["left"]["tag"] == "FELL LEAST"
    assert sheet.beat("SECTOR_CONTRAST").payload["right"]["tag"] == "FELL MOST"
    issues = check_line("IT led while Bank fell.", "curiosity", sheet, None)
    assert any("needs a" in i for i in issues), issues
    mixed = _sector_day([("Metal", 1.2), ("IT", -0.4)])
    assert "leader" in mixed.fact("sector.METAL").claims
    assert mixed.beat("SECTOR_CONTRAST").payload["left"]["tag"] == "LEADER"
    assert not check_line("Metal led, IT lagged.", "curiosity", mixed, None)

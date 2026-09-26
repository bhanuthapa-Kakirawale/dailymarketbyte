"""Dynamic hook rendering (daily_video/hook_scene.py + hook_kit.py) and its storyboard
integration. Phase 1A: the editorial collage visual language."""
import itertools
import json
import os
import re

import numpy as np
import pytest

from core.content_safety import SafetyStatus, classify_text, scan_publication
from daily_video import Composer
from daily_video.hook_scene import STAGE, DynamicHookScene, paint_beat, slip_slots
from daily_video.storyboard import HOOK_STAMPS, Storyboard, build_storyboard, dynamic_hook_spec
from daily_video.typography import Recorder
from daily_video.annotations import Ctx
from daily_video.scenes import new_layer
from daily_video import theme
from hooks import (HookSource, custom_stock_sheet, plan_hook, policy, post_market_sheet,
                   pre_market_sheet)
from hooks.candidates import build_candidates
from hooks.fixtures import CUSTOM_EXAMPLES, POST_EXAMPLES, PRE_EXAMPLES

REPORT = "output/reports/premarket_2026-09-23.json"
RADAR_RESULT = "output/radar/daily_radar_2026-09-21.json"
CHROME_ROLES = {"brand", "disclaimer"}
PKG = os.path.dirname(__import__("daily_video").__file__)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("hook rendering must not reach the network")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _sheet(name):
    if name in POST_EXAMPLES:
        b = POST_EXAMPLES[name]()
        return post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                                 b["universe"])
    if name in PRE_EXAMPLES:
        return pre_market_sheet(PRE_EXAMPLES[name]())
    return custom_stock_sheet(CUSTOM_EXAMPLES[name]())


ALL = sorted(list(POST_EXAMPLES) + list(PRE_EXAMPLES) + list(CUSTOM_EXAMPLES))


def _composer(name, hp=None):
    sheet = _sheet(name)
    hp = hp or plan_hook(sheet, use_ai=False)
    spec = dynamic_hook_spec(hp, sheet)
    sb = Storyboard(session_date=sheet.session_date, date_label="TUE 22 SEP 2026",
                    kicker="SESSION RECAP", scenes=[spec])
    return sb, Composer(sb), hp


def _declared(sb):
    strings = list(sb.public_text().values())
    tokens = {w for s in strings for w in s.split(" ")}
    return strings, tokens


def _is_declared(text, strings, tokens):
    return text in strings or text in tokens or (len(text) >= 8 and any(text in s for s in strings))


def _times(hp, sb):
    tm = hp.timing
    ts = [0.0] + [i * tm.beat_seconds + tm.beat_seconds * 0.7 for i in range(len(hp.teaser_beats))]
    return ts + [sb.scenes[0].freeze["t"]]


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(b):
    return max(1, (b[2] - b[0]) * (b[3] - b[1]))


# --------------------------------------------------------------------------- every archetype
@pytest.mark.parametrize("name", ALL)
def test_settled_frame_passes_freeze_qa(name):
    sb, comp, hp = _composer(name)
    _, rec, qa = comp.freeze(0)
    assert qa["passed"], qa["issues"]
    assert "hook_hero" in qa["marks"] or "hook_card" in qa["marks"]
    roles = {tb.role for tb in rec.texts}
    assert {"eyebrow", "curiosity", "summary"} <= roles      # the frame explains the Short alone
    assert qa["brand_in_frame"]
    assert sb.scenes[0].duration <= policy.MAX_TOTAL_SECONDS
    assert "agenda" not in roles                              # settled frame: no chip clutter


@pytest.mark.parametrize("name", ALL)
def test_every_drawn_string_is_declared(name):
    """Beats (base card, slips, stamps) and hero: nothing on screen the plan didn't declare."""
    sb, comp, hp = _composer(name)
    strings, tokens = _declared(sb)
    for t in _times(hp, sb):
        rec = Recorder()
        comp.frame(t, rec)
        for tb in rec.texts:
            if tb.role in CHROME_ROLES:
                continue
            assert _is_declared(tb.text, strings, tokens), (name, t, tb.role, tb.text)


@pytest.mark.parametrize("name", ALL)
def test_public_text_passes_content_safety(name):
    sb, _, _ = _composer(name)
    assert scan_publication(sb.public_text()).status is SafetyStatus.SAFE


@pytest.mark.parametrize("name", ALL)
def test_frame_zero_has_brand_hook_and_a_visual_figure(name):
    sb, comp, hp = _composer(name)
    rec = Recorder()
    img = comp.frame(0.0, rec).convert("RGB")
    roles = {tb.role for tb in rec.texts}
    assert "curiosity" in roles and "brand" in roles
    if hp.teaser_beats:
        assert "card_value" in roles or "event_title" in roles or "card_name" in roles, roles
    stage = np.asarray(img.crop(tuple(int(v) for v in STAGE))).astype(float)
    assert stage.std() > 12


@pytest.mark.parametrize("name", ALL)
def test_settled_text_is_not_clipped_and_cards_do_not_collide(name):
    sb, comp, hp = _composer(name)
    _, rec, qa = comp.freeze(0)
    containers = [b for k, b in rec.marks if k in ("hook_card", "hook_hero", "hook_tag", "hook_stamp")]
    top_zone = (0, 0, theme.CANVAS_W, STAGE[1])
    bottom_zone = (0, STAGE[3], theme.CANVAS_W, theme.RESERVED_BOTTOM)
    for tb in rec.texts:
        if tb.role in CHROME_ROLES:
            continue
        inside = any(tb.box[0] >= c[0] - 6 and tb.box[2] <= c[2] + 6 and tb.box[1] >= c[1] - 6
                     and tb.box[3] <= c[3] + 6 for c in containers + [top_zone, bottom_zone])
        assert inside, (name, tb.role, tb.text, tb.box)
    for kind in ("hook_card", "hook_tag"):
        boxes = [b for k, b in rec.marks if k == kind]
        for a, b in itertools.combinations(boxes, 2):
            # rotated cards' bounding boxes may kiss at the corners; real overlap is a bug
            assert _overlap(a, b) <= 0.04 * min(_area(a), _area(b)), (name, kind, a, b)


@pytest.mark.parametrize("name", ALL)
def test_animations_finish_before_the_settled_hold(name):
    """After settle + 0.9s the hook only breathes (neon pulse): layout and text are final."""
    sb, comp, hp = _composer(name)
    tm = hp.timing
    t1, t2 = tm.settle_start + 0.9, tm.total - 0.3
    r1, r2 = Recorder(), Recorder()
    a = np.asarray(comp.frame(t1, r1).convert("L")).astype(float)
    b = np.asarray(comp.frame(t2, r2).convert("L")).astype(float)
    assert [(x.text, x.box) for x in r1.texts] == [(x.text, x.box) for x in r2.texts]
    assert np.abs(a - b).mean() < 1.5
    assert tm.total - t1 >= 1.0


def test_every_beat_kind_renders_as_base_card_and_as_slip():
    seen = set()
    for name in ALL:
        sheet = _sheet(name)
        beats = [{"id": b.beat_id, "kind": b.kind.value, "payload": b.payload} for b in sheet.beats]
        slots = slip_slots([beats[0]] + beats) if beats else {}
        for b in beats:
            for i in (0, 1):
                rec = Recorder()
                ctx = Ctx(new_layer(), rec)
                paint_beat(ctx, i, b, 0.8, 0.8, slot=slots.get(1) if i else None)
                ctx.flush()
                arr = np.asarray(ctx.layer.getchannel("A"))
                assert arr.max() > 0, (name, b["id"], i)
                assert rec.texts, (name, b["id"], i)
            seen.add(b["kind"])
    assert seen >= {"LINE", "SECTOR_PAIR", "SECTOR_TILE", "MOVER_BAR", "BREAKOUT", "VOLUME",
                    "RADAR", "CUE", "EVENT", "METRIC", "FLOWS"}


@pytest.mark.parametrize("i", [0, 1])
def test_event_titles_are_never_truncated(i):
    title = "Why India's Biggest Stock Exchange Is Going Public After Two Decades Of Waiting"
    beat = {"kind": "EVENT", "payload": {"tag": "IPO", "title": title, "when": "10:00 IST",
                                         "day": "23", "month": "SEP"}}
    rec = Recorder()
    ctx = Ctx(new_layer(), rec)
    paint_beat(ctx, i, beat, 0.8, 0.8, slot=slip_slots([beat, beat])[1] if i else None)
    drawn = " ".join(tb.text for tb in rec.texts if tb.role == "event_title")
    assert drawn == title


def test_slips_are_placed_by_direction():
    up = {"kind": "MOVER_BAR", "payload": {"positive": True}}
    down = {"kind": "MOVER_BAR", "payload": {"positive": False}}
    slots = slip_slots([up, down, up])
    assert slots[1]["cy"] > slots[2]["cy"]                      # the fall is pinned lower
    slots = slip_slots([up, up, down])
    assert slots[1]["cy"] < slots[2]["cy"]                      # the gain is pinned higher


def test_stamps_are_fixed_safe_and_licensed():
    words = [w for table in HOOK_STAMPS.values() for w in table.values()]
    from hooks.validation import _COMPILED
    for w in words:
        assert classify_text(w).status is SafetyStatus.SAFE
        for fam, pats in _COMPILED.items():
            assert not any(p.search(w) for p in pats), (w, fam)
        assert not set(re.findall(r"[a-z]+", w.lower())) & policy.HYPE_WORDS
    # "QUIET" appears only for the archetype that REQUIRES a quiet index claim
    for (mode, arch), table in HOOK_STAMPS.items():
        if any("QUIET" in v for v in table.values()):
            assert arch == "QUIET_MARKET_HIDDEN_ACTION"


def test_renderer_has_no_stock_specific_code():
    real = ("MANKIND", "ETERNAL", "OFSS", "ALKEM", "ATGL", "STOCK-", "NIFTY 50\"", "RELIANCE")
    for fn in ("hook_scene.py", "hook_kit.py"):
        src = open(os.path.join(PKG, fn), encoding="utf-8").read()
        for s in real:
            assert s not in src, (fn, s)
        assert not re.search(r"symbol\"?\]?\s*==", src)
        assert not re.search(r"archetype", src.split('"""', 2)[-1]), fn   # visuals key on kind


def test_gemini_selected_plan_uses_the_same_visual_system(real_sheet_and_sb):
    sheet, _ = real_sheet_and_sb
    cands = build_candidates(sheet)
    alt = next(c for c in cands if c.archetype.value == "UNUSUAL_ACTIVITY")
    reply = json.dumps({"candidate_id": alt.candidate_id, "archetype": alt.archetype.value,
                        "hero_visual": alt.default_hero.value,
                        "teaser_beats": ["NIFTY_CLOSE", "SECTOR_CONTRAST", "RADAR_SWEEP"],
                        "curiosity_line": "Nifty +0.29%. MANKIND +6.0% on 4.1× normal volume.",
                        "summary_line": alt.summary_line, "fact_ids_used": ["nifty.move"]},
                       ensure_ascii=False)
    gem = plan_hook(sheet, client=lambda p, s: reply)
    det = plan_hook(sheet, use_ai=False)
    assert gem.source is HookSource.GEMINI and det.source is HookSource.DETERMINISTIC
    for hp in (gem, det):
        spec = dynamic_hook_spec(hp, sheet)
        sb = Storyboard(session_date=sheet.session_date, date_label="MON 21 SEP 2026",
                        kicker="SESSION RECAP", scenes=[spec])
        comp = Composer(sb)
        assert isinstance(comp.scenes[0], DynamicHookScene)
        _, _, qa = comp.freeze(0)
        assert qa["passed"], qa["issues"]


# --------------------------------------------------------------------------- real integration
@pytest.fixture(scope="module")
def real():
    if not (os.path.exists(REPORT) and os.path.exists(RADAR_RESULT)):
        pytest.skip("real validation artifacts not present")
    import render_daily_market_byte as r
    return r.load_inputs(REPORT, "output/radar", "2026-09-21")


@pytest.fixture(scope="module")
def real_sheet_and_sb(real):
    plan, pres, rp, rr, ev, uni, src = real
    by = {s["instrument"]: s for s in rr["stories"]}
    stories = [(sc["story"], by[sc["story"]["instrument"]]) for sc in rp["scenes"]
               if sc.get("role") == "STORY"]
    sheet = post_market_sheet(plan, pres, stories, ev,
                              ["PULSE", "NIFTY", "SECTORS", "MOVERS", "RADAR", "AHEAD"], uni)
    return sheet, build_storyboard(plan, pres, rp, rr, ev, uni, src)


def test_real_storyboard_opens_with_the_dynamic_hook(real_sheet_and_sb):
    _, sb = real_sheet_and_sb
    assert sb.scenes[0].kind == "DYNAMIC_HOOK"
    assert sb.hook_plan["archetype"] == "QUIET_MARKET_HIDDEN_ACTION"
    assert sb.hook_plan["curiosity_line"] == "Nifty moved just +0.29%. Three stocks underneath didn't."
    assert sb.scenes[0].texts["stamps"] == ["QUIET DAY?", "", "BUT LOOK UNDERNEATH"]
    assert scan_publication(sb.public_text()).status is SafetyStatus.SAFE
    _, _, qa = Composer(sb).freeze(0)
    assert qa["passed"], qa["issues"]


def test_legacy_hook_still_available(real):
    plan, pres, rp, rr, ev, uni, src = real
    sb = build_storyboard(plan, pres, rp, rr, ev, uni, src, dynamic_hook=False)
    assert sb.scenes[0].kind == "HOOK" and sb.hook_plan is None


def test_storyboard_passes_hook_ai_through_to_the_client(real):
    plan, pres, rp, rr, ev, uni, src = real
    calls = []
    sb = build_storyboard(plan, pres, rp, rr, ev, uni, src, hook_ai=True,
                          hook_client=lambda p, s: calls.append(p) or None)
    assert len(calls) == 1 and sb.hook_plan["source"] == "DETERMINISTIC"
    assert "Gemini returned nothing" in sb.hook_plan["fallback_reason"]

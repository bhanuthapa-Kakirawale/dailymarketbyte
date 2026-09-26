"""Unified Daily Market Byte renderer (daily_video/): storyboard facts, renderer boundaries,
design-system consistency, freeze-frame QA, generic Radar templates, and a small real encode.

Uses the repository's real 2026-09-21 artifacts where present (skipped otherwise) plus
synthetic fixtures for everything that must work tomorrow with different stocks.
"""
import ast
import datetime as dt
import inspect
import os
import re
import subprocess

import pytest

import daily_video
from config import fmt_in
from core.content_safety import SafetyStatus, scan_publication
from daily_video import chrome, composer, storyboard as sbm, theme
from daily_video.composer import Composer, REQUIRED_MARKS
from daily_video.storyboard import SceneSpec, Storyboard, build_storyboard
from daily_video.typography import font, font_report
from radar.visual_evidence import RadarVisualEvidence

REPORT = "output/reports/premarket_2026-09-23.json"
RADAR_RESULT = "output/radar/daily_radar_2026-09-21.json"
REAL_SYMBOLS = ("MANKIND", "ETERNAL", "OFSS", "ALKEM", "ATGL")
PKG_DIR = os.path.dirname(daily_video.__file__)


def _pkg_sources():
    for name in sorted(os.listdir(PKG_DIR)):
        if name.endswith(".py"):
            with open(os.path.join(PKG_DIR, name), encoding="utf-8") as fh:
                yield name, fh.read()


@pytest.fixture(scope="module")
def real():
    if not (os.path.exists(REPORT) and os.path.exists(RADAR_RESULT)):
        pytest.skip("real validation artifacts not present")
    import render_daily_market_byte as r
    plan, pres, radar_pres, radar_result, evidence, universe, sources = r.load_inputs(
        REPORT, "output/radar", "2026-09-21")
    sb = build_storyboard(plan, pres, radar_pres, radar_result, evidence, universe, sources, profile="PRIVATE_ANALYTICS")
    return {"plan": plan, "pres": pres, "radar_pres": radar_pres, "radar_result": radar_result,
            "evidence": evidence, "sb": sb, "comp": Composer(sb)}


# --------------------------------------------------------------------------- 1-2 facts unchanged
def test_main_facts_are_formatted_report_values(real):
    sb, pres = real["sb"], real["pres"]
    pulse = next(s for s in sb.scenes if s.kind == "PULSE")
    assert pulse.texts["value"] == fmt_in(pres.m["close"], 2)
    assert pulse.texts["change"] == f"{pres.m['pct']:+.2f}%"
    sectors = next(s for s in sb.scenes if s.kind == "SECTORS")
    expected = sorted(pres.sec, key=lambda r: -r["pct"])
    assert [(r["name"], r["value"]) for r in sectors.texts["rows"]] ==         [(r["name"], f"{r['pct']:+.2f}%") for r in expected]


def test_radar_facts_and_order_unchanged(real):
    """Phase 2: the first RADAR_PUBLISH_LIMIT stories in the selector's own order; every
    published figure is the detector's own value, formatted."""
    sb, rr, rp = real["sb"], real["radar_result"], real["radar_pres"]
    stories = [s for s in sb.scenes if s.kind == "RADAR_STORY"]
    pres_order = [sc["story"]["instrument"] for sc in rp["scenes"] if sc["role"] == "STORY"]
    assert [s.texts["symbol"] for s in stories] == pres_order[:sbm.RADAR_PUBLISH_LIMIT]
    by = {s["instrument"]: s for s in rr["stories"]}
    for s in stories:
        src = by[s.texts["symbol"]]
        vol = src.get("volume_context") or {}
        if vol.get("level") in ("ELEVATED", "UNUSUAL", "EXTREME"):
            assert s.texts["support_label"] == f"{vol['relative_volume']:.1f}× normal volume"
        assert "vs Nifty" not in " ".join(v for v in s.texts.values() if isinstance(v, str))


def test_storyboard_public_text_passes_content_safety(real):
    assert scan_publication(real["sb"].public_text()).status is SafetyStatus.SAFE


# --------------------------------------------------------------------------- 3-4 boundaries
def _imports(src):
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_renderer_makes_no_network_or_provider_imports():
    banned = {"market", "yfinance", "requests", "urllib", "urllib.request", "news", "providers",
              "storage.ohlcv_repository", "radar.relative_acquisition"}
    for name, src in _pkg_sources():
        assert not (_imports(src) & banned), name


def test_renderer_performs_no_detector_calculation():
    banned = {"radar.technical", "radar.relative", "radar.volume", "radar.composite",
              "radar.novelty", "radar.visual_evidence", "radar.ohlcv_service", "intelligence"}
    for name, src in _pkg_sources():
        assert not (_imports(src) & banned), name
        assert ".rolling(" not in src and ".mean(" not in src, name


# --------------------------------------------------------------------------- 5-7 design system
def test_primary_brand_is_daily_market_byte_and_radar_is_a_section(real):
    assert (chrome.BRAND_PART1 + chrome.BRAND_PART2).strip() == "DAILY MARKET BYTE"
    comp = real["comp"]
    for i in range(len(real["sb"].scenes)):
        _, rec, _ = comp.freeze(i)
        brands = [tb.text for tb in rec.texts if tb.role == "brand"]
        assert brands == ["DAILY MARKET BYTE"]
        assert not any(tb.text == "MARKET RADAR" and tb.role != "chip" for tb in rec.texts)
    labels = [lab for _, lab, _, _ in real["sb"].sections() if lab]
    assert "MARKET RADAR" in labels


def test_fonts_resolve_through_one_chain():
    rep = font_report()
    assert rep["bold"]["loaded_path"] == getattr(font(40, True), "path", None)
    assert rep["regular"]["loaded_path"] == getattr(font(40, False), "path", None)
    assert rep["bold"]["resolved_path"] == rep["bold"]["loaded_path"]


def test_one_header_for_every_scene(real):
    comp = real["comp"]
    assert isinstance(comp.chrome, chrome.Chrome)
    base = chrome.header_base(real["sb"].date_label, real["sb"].kicker)
    assert base.tobytes() == comp.chrome.base.tobytes()


# --------------------------------------------------------------------------- 8-14 freeze QA
def test_every_freeze_frame_passes_geometry_qa(real):
    comp = real["comp"]
    for i, spec in enumerate(real["sb"].scenes):
        _, rec, qa = comp.freeze(i)
        assert qa["passed"], (spec.kind, spec.texts.get("symbol"), qa["issues"])
        assert qa["min_font"] >= theme.MIN_FONT


def test_progress_sections_contiguous_and_complete(real):
    secs = real["sb"].sections()
    assert secs[0][2] == 0.0
    assert abs(secs[-1][3] - real["sb"].total_duration) < 1e-6
    for a, b in zip(secs, secs[1:]):
        assert abs(a[3] - b[2]) < 1e-6 and a[0] != b[0]


def test_freeze_metadata_answers_what_where_why(real):
    for spec in real["sb"].scenes:
        for k in ("what", "where", "why"):
            assert spec.freeze.get(k), (spec.kind, k)
        assert 0 < spec.freeze["t"] < spec.duration


# --------------------------------------------------------------------------- 15-16 generic templates
def _synthetic_evidence(symbol, n=38, trend=1.0, last_jump=0.0):
    d0 = dt.date(2026, 1, 1)
    dates = [d0 + dt.timedelta(days=i) for i in range(n)]
    closes = [100 + i * 0.2 * trend + (3 if i % 3 == 0 else 0) for i in range(n)]
    closes[-1] += last_jump
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.8 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.8 for o, c in zip(opens, closes)]
    sma = [None] * 10 + [100 + i * 0.2 * trend + 1 for i in range(10, n)]
    rel_s = [100 + i * 0.4 for i in range(21)]
    rel_b = [100 - i * 0.1 for i in range(21)]
    return RadarVisualEvidence(
        instrument=symbol, session_date=dates[-1], calculation_version="1.0", window_dates=dates,
        close_series=closes, volume_series=[1000.0] * (n - 1) + [4000.0], sma20_series=sma,
        sma50_series=sma, range_20_high=max(closes[-21:-1]) + 1,
        range_20_low=min(closes[-21:-1]) - 1, range_50_high=max(closes) + 2,
        range_50_low=min(closes) - 2, highlight_events=[], rvol=4.0, rvol_level="UNUSUAL",
        stock_return_5d_pct=1.0, stock_return_20d_pct=2.0, market_relative_5d_pp=3.0,
        market_relative_20d_pp=4.0, relative_window_sessions=20, relative_dates=dates[-21:],
        relative_stock_normalized=rel_s, relative_benchmark_normalized=rel_b,
        benchmark_symbol="^NSEI", source_session_dates=dates, open_series=opens,
        high_series=highs, low_series=lows)


# (event, volume level or None, jump) -> expected event family
SYNTH_CASES = {
    "RANGE_UP": (["BREAK_ABOVE_20D_RANGE"], "UNUSUAL", 8.0),
    "RANGE_UP_50": (["BREAK_ABOVE_50D_RANGE"], None, 8.0),
    "RANGE_DOWN": (["BREAK_BELOW_20D_RANGE"], "ELEVATED", -12.0),
    "MA_DOWN": (["CROSS_BELOW_SMA50"], None, -3.0),
    "MA_UP": (["CROSS_ABOVE_SMA20"], "EXTREME", 3.0),
}


@pytest.mark.parametrize("case", list(SYNTH_CASES))
def test_generic_templates_work_on_synthetic_symbols(case):
    events, level, jump = SYNTH_CASES[case]
    sym = f"SYNTH-{case[:3]}"
    sp = {"instrument": sym, "company_name": "Synthetic Co", "price_change_display": "+1.0%"}
    story = {"instrument": sym, "technical_context": {"events": events}, "price_change_pct": jump,
             "volume_context": {"level": level, "relative_volume": 3.0} if level else None,
             "relative_context": {"market_relative_20d_pp": 4.0}}
    spec = sbm._radar_story(1, 3, sp, story, _synthetic_evidence(sym, last_jump=jump))
    fam = case.split("_50")[0]
    assert spec.data["event_family"] == fam
    sb = Storyboard(session_date=dt.date(2026, 1, 1), date_label="THU 01 JAN 2026",
                    kicker="SESSION RECAP", scenes=[spec])
    _, rec, qa = Composer(sb).freeze(0)
    assert qa["passed"], qa["issues"]
    assert REQUIRED_MARKS[fam] <= set(qa["marks"])


def test_no_symbol_specific_branching():
    for name, src in _pkg_sources():
        for sym in REAL_SYMBOLS:
            assert sym not in src, (name, sym)


# --------------------------------------------------------------------------- 17-21 real stories
EXPECTED = {"MANKIND": ("RANGE_UP", "VOLUME"), "ETERNAL": ("RANGE_UP", "DAY_RANGE"),
            "OFSS": ("RANGE_DOWN", "VOLUME")}


@pytest.mark.parametrize("symbol", list(EXPECTED))
def test_real_story_uses_expected_visual_mechanism(real, symbol):
    idx, spec = next((i, s) for i, s in enumerate(real["sb"].scenes)
                     if s.kind == "RADAR_STORY" and s.texts["symbol"] == symbol)
    assert (spec.data["event_family"], spec.data["support"]["kind"]) == EXPECTED[symbol]
    _, _, qa = real["comp"].freeze(idx)
    assert REQUIRED_MARKS[spec.data["event_family"]] <= set(qa["marks"])


def test_missing_evidence_falls_back_to_text_mode():
    spec = sbm._radar_story(1, 1, {"instrument": "SYNTH-X", "visual_type": "QUIET_SIGNAL_CARD"},
                            {"instrument": "SYNTH-X"}, None)
    assert spec.data["event_family"] == "TEXT"
    assert spec.data["candles"] is None


def test_fii_dii_is_omitted_not_fabricated_when_report_has_no_flows(real):
    assert real["pres"].fd is None
    assert not any(s.kind == "FLOWS" for s in real["sb"].scenes)
    assert any(o["section"] == "FII / DII" for o in real["sb"].omitted)


# --------------------------------------------------------------------------- 22-24 encode
def test_small_storyboard_encodes_valid_h264(tmp_path):
    scenes = [SceneSpec(kind="HOOK", section="HOOK", duration=0.5, headline="+1.00%",
                        subline="How the session closed",
                        texts={"kicker": "YOUR DAILY MARKET BRIEFING", "label": "NIFTY",
                               "value": "+1.00%", "agenda": "Nifty • Radar"},
                        data={"positive": True}, freeze={"t": 0.4}),
              SceneSpec(kind="CLOSING", section="CLOSING", duration=0.5,
                        texts={"cta": "SUBSCRIBE", "cadence": "Every trading day", "note": ""},
                        freeze={"t": 0.4})]
    sb = Storyboard(session_date=dt.date(2026, 1, 1), date_label="THU 01 JAN 2026",
                    kicker="SESSION RECAP", scenes=scenes)
    out = str(tmp_path / "t.mp4")
    res = Composer(sb).render(out)
    assert res["ok"] and os.path.getsize(out) > 0
    from video import _ffmpeg
    info = subprocess.run([_ffmpeg(), "-i", out], capture_output=True, text=True).stderr
    assert "h264" in info and "1080x1920" in info
    assert "Audio:" not in info


# --------------------------------------------------------------------------- sectors heatmap
def test_sectors_heatmap_shows_every_report_sector(real):
    sectors = next(s for s in real["sb"].scenes if s.kind == "SECTORS")
    assert len(sectors.texts["rows"]) == len(real["pres"].sec)
    assert sectors.texts["leader_tag"] == "LEADER" and sectors.texts["laggard_tag"] == "LAGGARD"
    assert not any(o.get("scene") == "sectors" for o in real["sb"].omitted)


@pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 12])
def test_sectors_heatmap_layout_scales_with_sector_count(n):
    """Every sector stays visible (ranked heat strip) at any count; leader/laggard lead."""
    from types import SimpleNamespace
    from presentation.post_plan import plan_post_sections
    names = [f"Sector{i}" for i in range(n)]
    pres = SimpleNamespace(sec=[{"name": nm, "pct": 2.0 - i * 0.4} for i, nm in enumerate(names)],
                           m={}, session_date=dt.date(2026, 1, 1))
    post = plan_post_sections(pres, SimpleNamespace(scenes=[]))
    spec = sbm._sectors_scene(post.sectors)
    assert [r["name"] for r in spec.texts["rows"]] == names
    sb = Storyboard(session_date=dt.date(2026, 1, 1), date_label="THU 01 JAN 2026",
                    kicker="SESSION RECAP", scenes=[spec])
    _, rec, qa = Composer(sb).freeze(0)
    assert qa["passed"], qa["issues"]
    tiles = sum(1 for k, _ in rec.marks if k == "heat_tile")
    assert tiles == (1 if n == 1 else n + 2)          # hero cards + one strip tile per sector
    shown = {tb.text for tb in rec.texts if tb.role == "tile_name"}
    assert n == 1 or shown == set(names)

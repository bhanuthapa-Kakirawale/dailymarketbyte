"""SYNTHETIC hook fixtures - for previews and tests only, never for publication.

Every stock is a placeholder (STOCK-A, STOCK-B ...) so a preview can never be mistaken for a
real session, and every preview render carries a "SYNTHETIC DATA - NOT REAL" badge. Series are
generated from fixed seeds (deterministic), shaped to the scenario (a range then a breakout, a
slide under the average, ...). Sector and global-index NAMES are real categories so the layout
is tested with realistic label lengths; their NUMBERS are invented.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd

from editorial.models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from radar.visual_evidence import RadarVisualEvidence

from .sheet_custom import CustomStockInputs
from .sheet_pre import PreMarketInputs

SESSION = dt.date(2026, 9, 22)        # a Tuesday
NEXT_SESSION = dt.date(2026, 9, 23)


def _walk(seed, n, start, vol, drift=0.0):
    rng = np.random.RandomState(seed)
    steps = rng.normal(drift, vol, n)
    return list(start * np.cumprod(1 + steps))


def _ohlc_df(closes, seed, end=SESSION):
    rng = np.random.RandomState(seed + 1)
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) * (1 + abs(rng.normal(0, 0.003))) for o, c in zip(opens, closes)]
    lows = [min(o, c) * (1 - abs(rng.normal(0, 0.003))) for o, c in zip(opens, closes)]
    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=dates)
    df["ema20"] = df["Close"].ewm(span=20).mean()
    return df


def _sma(values, n):
    out = []
    for i in range(len(values)):
        out.append(None if i + 1 < n else float(np.mean(values[i + 1 - n:i + 1])))
    return out


def _stock_series(seed, shape, n=60, start=1000.0):
    """closes + volumes shaped to a story: 'breakout', 'breakdown', 'cross_below', 'flat'."""
    rng = np.random.RandomState(seed)
    base = list(start * np.cumprod(1 + rng.normal(0.0, 0.008, n - 1)))
    lo, hi = min(base[-20:]), max(base[-20:])
    if shape == "breakout":
        last = hi * 1.045
    elif shape == "breakdown":
        last = lo * 0.95
    elif shape == "cross_below":
        base = [v * (1 + 0.0025 * i) for i, v in enumerate(base)]
        base[-8:] = [base[-9] * (1 - 0.006 * k) for k in range(1, 9)]
        last = base[-1] * 0.996
    else:
        last = base[-1] * 1.002
    closes = [float(v) for v in base + [last]]
    vols = list(np.abs(rng.normal(1.0, 0.25, n)) * 1e6)
    return closes, vols


def _evidence(sym, closes, vols, session=SESSION, rvol=None, rel=None):
    dates = list(pd.bdate_range(end=pd.Timestamp(session), periods=len(closes)).date)
    prior = closes[:-1]
    return RadarVisualEvidence(
        instrument=sym, session_date=session, calculation_version="synthetic",
        window_dates=dates, close_series=closes, volume_series=vols,
        sma20_series=_sma(closes, 20), sma50_series=_sma(closes, 50),
        range_20_high=max(prior[-20:]), range_20_low=min(prior[-20:]),
        range_50_high=max(prior[-50:]), range_50_low=min(prior[-50:]),
        highlight_events=[], rvol=rvol, rvol_level=None, stock_return_5d_pct=None,
        stock_return_20d_pct=None, market_relative_5d_pp=None, market_relative_20d_pp=rel,
        relative_window_sessions=20, relative_dates=None, relative_stock_normalized=None,
        relative_benchmark_normalized=None, benchmark_symbol="NIFTY 50",
        source_session_dates=dates)


def _story(sym, company, chg, events, rvol=None, rel=None, signals=1, families=()):
    sp = {"instrument": sym, "company_name": company, "price_change_display": f"{chg:+.1f}%",
          "visual_type": "CHART"}
    story = {"instrument": sym, "price_change_pct": chg, "independent_signal_count": signals,
             "active_families": list(families), "direction": "ALIGNED_POSITIVE" if chg > 0 else
             "ALIGNED_NEGATIVE", "technical_context": {"events": list(events)},
             "volume_context": {"relative_volume": rvol},
             "relative_context": {"market_relative_20d_pp": rel}, "selection_id": f"synth-{sym}"}
    return sp, story


def _items(rows):
    return [EditorialItem(title=n, value=f"{v:+.2f}%", numeric=v, positive=v >= 0, rank=i + 1,
                          source_fact_ids=[f"synth-{n}"]) for i, (n, v) in enumerate(rows)]


def _plan(nifty_pct, gainers, losers, context="Closed above its 20-day average", flows=None,
          globals_=None):
    scenes = [ScenePlan(scene_id="hook", scene_type=SceneType.HOOK, primary_text="NIFTY",
                        primary_value=f"{nifty_pct:+.2f}%", source_fact_ids=["synth-nifty"]),
              ScenePlan(scene_id="nifty", scene_type=SceneType.NIFTY, primary_text="NIFTY 50",
                        secondary_text=context, source_fact_ids=["synth-nifty"]),
              ScenePlan(scene_id="gainers", scene_type=SceneType.GAINERS, items=_items(gainers)),
              ScenePlan(scene_id="losers", scene_type=SceneType.LOSERS, items=_items(losers))]
    if flows:
        scenes.append(ScenePlan(scene_id="flows", scene_type=SceneType.FLOWS, items=[
            EditorialItem(title=n, value=f"{'+' if v >= 0 else '-'}Rs {abs(v):,.0f} cr", numeric=v,
                          positive=v >= 0, source_fact_ids=[f"synth-{n}"]) for n, v in flows]))
    if globals_:
        scenes.append(ScenePlan(scene_id="global", scene_type=SceneType.GLOBAL,
                                items=_items(globals_)))
    return ShortsPlan(report_id="SYNTHETIC", session_date=SESSION, scenes=scenes)


def _pres(nifty_pct, close, sectors, seed, bank=None, vix=None):
    drift = nifty_pct / 100 / 40
    closes = _walk(seed, 59, close / (1 + nifty_pct / 100) * 0.99, 0.006, drift)
    closes.append(closes[-1] * (1 + nifty_pct / 100))
    scale = close / closes[-1]
    closes = [c * scale for c in closes]
    df = _ohlc_df(closes, seed)
    m = {"pct": nifty_pct, "close": close, "chg": close * nifty_pct / 100, "open": df["Open"].iloc[-1],
         "high": df["High"].iloc[-1], "low": df["Low"].iloc[-1], "bank_pct": bank, "vix": vix,
         "chart_df": df, "ema20": float(df["ema20"].iloc[-1])}
    return SimpleNamespace(m=m, sec=[{"name": n, "pct": v} for n, v in sectors], session_date=SESSION,
                           fd=None)


def post_quiet():
    pres = _pres(0.12, 24_118.35, [("Pharma", 0.62), ("Bank", 0.18), ("Auto", -0.22), ("IT", -0.41)],
                 seed=3, bank=0.18, vix=12.4)
    specs = [("STOCK-A", "Placeholder Industries Ltd.", 5.4, ["BREAK_ABOVE_20D_RANGE"], "breakout", 3.6, 4.1, 3,
              ("STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE")),
             ("STOCK-B", "Sample Motors Ltd.", 3.1, ["CROSS_ABOVE_SMA50"], "breakout", None, 2.2, 1, ("STRUCTURE",)),
             ("STOCK-C", "Example Finance Ltd.", -4.6, ["BREAK_BELOW_20D_RANGE"], "breakdown", 2.4, -3.9, 2,
              ("STRUCTURE", "VOLUME")),
             ("STOCK-D", "Demo Power Ltd.", -2.8, ["CROSS_BELOW_SMA50"], "cross_below", None, -1.2, 1,
              ("STRUCTURE",))]
    return _post_bundle(pres, _plan(0.12, [("STOCK-E", 1.44), ("STOCK-F", 1.21)],
                                    [("STOCK-G", -1.62), ("STOCK-H", -1.10)]), specs, seed=10)


def post_sector_contrast():
    pres = _pres(-0.62, 23_874.10, [("Metal", 2.41), ("Realty", 1.18), ("Bank", 0.36), ("Auto", -0.31),
                                    ("Pharma", -0.92), ("IT", -1.87)], seed=5, bank=0.36, vix=13.1)
    specs = [("STOCK-A", "Placeholder Metals Ltd.", 2.2, ["CROSS_ABOVE_SMA20"], "breakout", None, 1.4, 1,
              ("STRUCTURE",))]
    return _post_bundle(pres, _plan(-0.62, [("STOCK-B", 3.84), ("STOCK-C", 2.95)],
                                    [("STOCK-D", -3.21), ("STOCK-E", -2.66)],
                                    context="Closed below its 20-day average"), specs, seed=20)


def post_radar_unusual():
    pres = _pres(0.55, 24_310.80, [("Auto", 1.12), ("Bank", 0.48), ("FMCG", 0.05), ("IT", -0.28)],
                 seed=7, bank=0.48, vix=12.0)
    specs = [("STOCK-A", "Placeholder Pharma Ltd.", 7.4, ["BREAK_ABOVE_50D_RANGE", "BREAK_ABOVE_20D_RANGE"],
              "breakout", 5.2, 6.8, 3, ("STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE")),
             ("STOCK-B", "Sample Cement Ltd.", 1.9, ["CROSS_ABOVE_SMA50"], "breakout", None, 1.1, 1, ("STRUCTURE",)),
             ("STOCK-C", "Example Retail Ltd.", -2.3, ["CROSS_BELOW_SMA20"], "cross_below", None, -0.8, 1,
              ("STRUCTURE",))]
    return _post_bundle(pres, _plan(0.55, [("STOCK-A", 7.40), ("STOCK-F", 2.31)],
                                    [("STOCK-G", -2.02), ("STOCK-H", -1.44)]), specs, seed=30)


def post_big_move():
    pres = _pres(-1.84, 23_418.65, [("Bank", -1.12), ("Auto", -1.64), ("IT", -2.08), ("Metal", -2.95),
                                    ("Realty", -3.40)], seed=9, bank=-1.12, vix=16.8)
    specs = [("STOCK-A", "Placeholder Realty Ltd.", -6.1, ["BREAK_BELOW_50D_RANGE"], "breakdown", 3.1, -5.2, 2,
              ("STRUCTURE", "VOLUME"))]
    return _post_bundle(pres, _plan(-1.84, [("STOCK-B", 0.84), ("STOCK-C", 0.52)],
                                    [("STOCK-A", -6.10), ("STOCK-D", -4.72)],
                                    context="Closed below its 20-day average",
                                    flows=[("FII", -3120.0), ("DII", 2640.0)]), specs, seed=40,
                        sections=["PULSE", "NIFTY", "FLOWS", "SECTORS", "MOVERS", "RADAR"])


def _post_bundle(pres, plan, specs, seed, sections=None):
    stories, evidence = [], {}
    for i, (sym, co, chg, events, shape, rvol, rel, sig, fam) in enumerate(specs):
        closes, vols = _stock_series(seed + i, shape)
        closes[-1] = closes[-2] * (1 + chg / 100)
        if rvol:
            vols[-1] = float(np.mean(vols[-21:-1]) * rvol)
        evidence[sym] = _evidence(sym, closes, vols, rvol=rvol, rel=rel)
        stories.append(_story(sym, co, chg, events, rvol, rel, sig, fam))
    sections = sections or ["PULSE", "NIFTY", "SECTORS", "MOVERS", "RADAR"]
    return {"plan": plan, "pres": pres, "stories": stories, "evidence": evidence,
            "sections": sections, "universe": "Nifty 100"}


# --------------------------------------------------------------------------- PRE
def pre_overnight():
    closes = _walk(11, 40, 24_050, 0.005)
    return PreMarketInputs(
        session_date=NEXT_SESSION, previous_session=SESSION, nifty_close=24_118.35, nifty_pct=0.12,
        nifty_series=closes[:-1] + [24_118.35],
        gift_nifty={"value": 23_905.0, "vs_close_pct": -0.88},
        global_cues=[{"name": "NASDAQ", "pct": -2.14}, {"name": "DOW JONES", "pct": -1.02},
                     {"name": "NIKKEI", "pct": -1.46}, {"name": "USD / INR", "pct": 0.21}],
        events=[{"tag": "IPO", "title": "Placeholder Tech IPO opens for subscription"}],
        sections=["GLOBAL", "GIFT", "EVENTS", "PREV"])


def pre_event():
    closes = _walk(12, 40, 24_000, 0.005)
    return PreMarketInputs(
        session_date=NEXT_SESSION, previous_session=SESSION, nifty_close=24_031.90, nifty_pct=-0.21,
        nifty_series=closes[:-1] + [24_031.90],
        global_cues=[{"name": "NASDAQ", "pct": 0.34}, {"name": "DOW JONES", "pct": 0.12},
                     {"name": "GOLD", "pct": -0.41}],
        events=[{"tag": "RBI", "title": "RBI monetary policy decision", "when": "10:00 IST",
                 "impact": "HIGH"}],
        sections=["EVENTS", "GLOBAL", "PREV"])


# --------------------------------------------------------------------------- CUSTOM
def custom_breakout():
    closes, vols = _stock_series(21, "breakout", start=1480.0)
    closes[-1] = closes[-2] * 1.052
    vols[-1] = float(np.mean(vols[-21:-1]) * 3.2)
    prior = closes[:-1]
    return CustomStockInputs(
        symbol="STOCK-A", company="Placeholder Industries Ltd.", session_date=SESSION,
        close=closes[-1], change_pct=5.2, closes=closes, volumes=vols,
        sma20=_sma(closes, 20), sma50=_sma(closes, 50),
        range20=(min(prior[-20:]), max(prior[-20:])), range50=(min(prior[-50:]), max(prior[-50:])),
        events=["BREAK_ABOVE_50D_RANGE", "BREAK_ABOVE_20D_RANGE"], rvol=3.2, rel20_pp=4.4,
        families=["STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE"],
        sections=["TECHNICAL", "VOLUME", "RELATIVE"])


def custom_weak_tech_strong_fund():
    closes, vols = _stock_series(22, "cross_below", start=820.0)
    return CustomStockInputs(
        symbol="STOCK-B", company="Sample Consumer Ltd.", session_date=SESSION,
        close=closes[-1], change_pct=-0.4, closes=closes, volumes=vols,
        sma20=_sma(closes, 20), sma50=_sma(closes, 50),
        events=["CROSS_BELOW_SMA50"], rvol=1.1, rel20_pp=-2.6, families=["STRUCTURE"],
        fundamentals=[
            {"key": "rev_growth", "label": "Revenue growth", "display": "+18.2% YoY", "value": 18.2,
             "claims": ["strong_fundamental"], "period": "Q1 FY27"},
            {"key": "roe", "label": "Return on equity", "display": "24.6%", "value": 24.6,
             "claims": ["strong_fundamental"], "period": "FY26"},
            {"key": "de", "label": "Debt to equity", "display": "0.12", "value": 0.12, "claims": [],
             "period": "FY26"}],
        sections=["TECHNICAL", "FUNDAMENTALS", "VOLUME"])


POST_EXAMPLES = {"post_quiet": post_quiet, "post_sector_contrast": post_sector_contrast,
                 "post_radar_unusual": post_radar_unusual, "post_big_move": post_big_move}
PRE_EXAMPLES = {"pre_overnight": pre_overnight, "pre_event": pre_event}
CUSTOM_EXAMPLES = {"custom_breakout": custom_breakout,
                   "custom_weak_tech_strong_fund": custom_weak_tech_strong_fund}

__all__ = ["POST_EXAMPLES", "PRE_EXAMPLES", "CUSTOM_EXAMPLES", "SESSION", "NEXT_SESSION"]

"""Market-level metrics for ONE session, computed from `RegimeData.until(session)` only.

Every function reads the LAST row of the data it is given (the session being classified) and
rows before it - never a row after it, because `until()` has already cut them off. Each metric
states its own window and coverage; a metric that cannot meet its window or coverage is `None`
with a reason, never a shorter window under the same name (CLAUDE.md: a statistic defined as N
sessions makes no statement with fewer than N).

Definitions reuse the existing DMB ones:
* unusual volume = `market.relative_volume()` (current / mean of the PRIOR 20 sessions,
  definition 2.0) at least `market_structure.observations.UNUSUAL_VOLUME_RVOL` (2.0x) - the
  Market Structure UNUSUAL_VOLUME definition;
* advance / decline = close vs the previous canonical session's close (Market Structure
  ADVANCES / DECLINES);
* coverage floor = `market_structure.aggregator.MIN_COVERAGE_PCT` (95%), the same floor the
  Market Structure statistics use.
"""
from __future__ import annotations

import math
import statistics

import numpy as np

from market_structure.aggregator import MIN_COVERAGE_PCT
from market_structure.observations import UNUSUAL_VOLUME_RVOL

from .data import RegimeData

SMA_SHORT = 20
SMA_LONG = 50
RETURN_WINDOW = 20             # sessions, for the trend return, sector returns and relative strength
BREADTH_PERSISTENCE = 10       # sessions averaged for the persistent advance share
VOLUME_WINDOW = 5              # sessions of unusual-volume events
RV_WINDOW = 20                 # daily NIFTY returns in the realised-volatility estimate
RV_LAG = 5                     # sessions back for the realised-volatility change
SECTOR_MIN_COVERED = 3         # constituents with a 20-session return before a sector is counted
UNCLASSIFIED = "Unclassified"


def _r(v, nd=4):
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else round(v, nd)


def _pct(n, d):
    return None if not d else 100.0 * n / d


def _coverage_ok(covered: int, members: int) -> bool:
    return members > 0 and 100.0 * covered / members >= MIN_COVERAGE_PCT - 1e-9


# ------------------------------------------------------------------ building blocks
def _day_change(data: RegimeData, j: int) -> np.ndarray:
    """% change session j vs session j-1 per symbol (NaN when either bar is missing)."""
    if j < 1:
        return np.full(len(data.symbols), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (data.close[j] / data.close[j - 1] - 1.0) * 100.0


def _breadth_day(data: RegimeData, j: int) -> dict:
    chg = _day_change(data, j)
    m = data.member[j]
    covered = m & ~np.isnan(chg)
    adv = int(np.sum(covered & (chg > 0)))
    dec = int(np.sum(covered & (chg < 0)))
    cov = int(covered.sum())
    return {"advances": adv, "declines": dec, "unchanged": cov - adv - dec, "covered": cov,
            "members": int(m.sum())}


def _rvol(data: RegimeData, j: int, k: int):
    """`market.relative_volume` over the 21 sessions ending at j (memoised per (session, symbol);
    the value only ever depends on rows <= j)."""
    key = (data.sessions[j], data.symbols[k])
    if key in data.rvol_memo:
        return data.rvol_memo[key]
    import pandas as pd

    import market
    lb = market.RELATIVE_VOLUME_LOOKBACK
    val = None
    if j >= lb:
        val = market.relative_volume(pd.Series(data.volume[j - lb:j + 1, k]), lookback=lb)
    data.rvol_memo[key] = val
    return val


def _sma_state(data: RegimeData, window: int):
    """(above, covered) over today's members: close above its own `window`-session SMA."""
    j = len(data.sessions) - 1
    if j + 1 < window:
        return 0, 0
    block = data.close[j - window + 1:j + 1]
    full = ~np.isnan(block).any(axis=0) & data.member[j]
    with np.errstate(invalid="ignore"):
        sma = np.nanmean(block, axis=0)
        above = full & (data.close[j] > sma)
    return int(above.sum()), int(full.sum())


def _returns(data: RegimeData, window: int) -> np.ndarray:
    j = len(data.sessions) - 1
    if j < window:
        return np.full(len(data.symbols), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (data.close[j] / data.close[j - window] - 1.0) * 100.0


# ------------------------------------------------------------------ dimension metrics
def trend_metrics(data: RegimeData) -> dict:
    j = len(data.sessions) - 1
    n = data.nifty
    out = {"close": _r(n[j], 2), "sma20": None, "sma50": None, "dist_sma20_pct": None,
           "dist_sma50_pct": None, "sma20_vs_sma50_pct": None, "return_20d_pct": None,
           "high_20": None, "low_20": None, "reason": ""}
    if math.isnan(n[j]):
        out["reason"] = "no NIFTY 50 bar on this session"
        return out
    if j + 1 >= SMA_SHORT and not np.isnan(n[j - SMA_SHORT + 1:]).any():
        w = n[j - SMA_SHORT + 1:]
        out["sma20"], out["high_20"], out["low_20"] = _r(w.mean(), 2), _r(w.max(), 2), _r(w.min(), 2)
        out["dist_sma20_pct"] = _r((n[j] / w.mean() - 1) * 100)
    if j + 1 >= SMA_LONG and not np.isnan(n[j - SMA_LONG + 1:]).any():
        sma50 = n[j - SMA_LONG + 1:].mean()
        out["sma50"] = _r(sma50, 2)
        out["dist_sma50_pct"] = _r((n[j] / sma50 - 1) * 100)
        if out["sma20"] is not None:
            out["sma20_vs_sma50_pct"] = _r((n[j - SMA_SHORT + 1:].mean() / sma50 - 1) * 100)
    if j >= RETURN_WINDOW and not math.isnan(n[j - RETURN_WINDOW]):
        out["return_20d_pct"] = _r((n[j] / n[j - RETURN_WINDOW] - 1) * 100)
    if out["sma50"] is None:
        out["reason"] = f"fewer than {SMA_LONG} complete NIFTY 50 sessions"
    return out


def breadth_metrics(data: RegimeData) -> dict:
    j = len(data.sessions) - 1
    today = _breadth_day(data, j)
    out = {**today, "universe_label": data.universe_label,
           "coverage_pct": _r(_pct(today["covered"], today["members"]), 2),
           "advance_share_pct": None, "decline_share_pct": None,
           "advance_share_10d_pct": None, "pct_above_sma50": None, "above_sma50": None,
           "sma50_covered": None, "pct_above_sma20": None, "reason": ""}
    if _coverage_ok(today["covered"], today["members"]):
        out["advance_share_pct"] = _r(_pct(today["advances"], today["covered"]), 2)
        out["decline_share_pct"] = _r(_pct(today["declines"], today["covered"]), 2)
    else:
        out["reason"] = (f"day-change coverage {today['covered']}/{today['members']} below "
                         f"{MIN_COVERAGE_PCT:g}%")
    if j >= BREADTH_PERSISTENCE:
        shares = []
        for jj in range(j - BREADTH_PERSISTENCE + 1, j + 1):
            b = _breadth_day(data, jj)
            if not _coverage_ok(b["covered"], b["members"]):
                shares = None
                break
            shares.append(_pct(b["advances"], b["covered"]))
        if shares:
            out["advance_share_10d_pct"] = _r(sum(shares) / len(shares), 2)
    above, cov = _sma_state(data, SMA_LONG)
    if _coverage_ok(cov, today["members"]):
        out["above_sma50"], out["sma50_covered"] = above, cov
        out["pct_above_sma50"] = _r(_pct(above, cov), 2)
    a20, c20 = _sma_state(data, SMA_SHORT)
    if _coverage_ok(c20, today["members"]):
        out["pct_above_sma20"] = _r(_pct(a20, c20), 2)
    return out


def sector_metrics(data: RegimeData) -> dict:
    j = len(data.sessions) - 1
    ret = _returns(data, RETURN_WINDOW)
    m = data.member[j]
    groups: dict = {}
    for k, sym in enumerate(data.symbols):
        if m[k]:
            groups.setdefault(data.sector.get(sym, UNCLASSIFIED), []).append(ret[k])
    rows = []
    for sector in sorted(groups):
        vals = [float(v) for v in groups[sector] if not math.isnan(v)]
        counted = sector != UNCLASSIFIED and len(vals) >= SECTOR_MIN_COVERED
        rows.append({"sector": sector, "members": len(groups[sector]), "covered": len(vals),
                     "median_return_20d_pct": _r(statistics.median(vals)) if vals else None,
                     "counted": counted})
    counted = [r for r in rows if r["counted"]]
    pos = sum(1 for r in counted if r["median_return_20d_pct"] > 0)
    neg = sum(1 for r in counted if r["median_return_20d_pct"] < 0)
    covered = int(np.sum(m & ~np.isnan(ret)))
    ok = _coverage_ok(covered, int(m.sum())) and len(counted) >= 3
    return {"window_sessions": RETURN_WINDOW, "sectors_counted": len(counted) if ok else None,
            "sectors_positive": pos if ok else None, "sectors_negative": neg if ok else None,
            "positive_share_pct": _r(_pct(pos, len(counted)), 2) if ok else None,
            "rows": rows, "covered": covered, "members": int(m.sum()),
            "reason": "" if ok else (f"20-session return coverage {covered}/{int(m.sum())} below "
                                     f"{MIN_COVERAGE_PCT:g}% or fewer than 3 sectors")}


def volume_metrics(data: RegimeData) -> dict:
    j = len(data.sessions) - 1
    up = down = flat = 0
    days = []
    ok = j >= VOLUME_WINDOW
    for jj in range(max(1, j - VOLUME_WINDOW + 1), j + 1):
        chg = _day_change(data, jj)
        m = data.member[jj]
        covered = hits_up = hits_down = hits = 0
        for k in np.nonzero(m)[0]:
            rv = _rvol(data, jj, int(k))
            if rv is None or math.isnan(chg[k]):
                continue
            covered += 1
            if rv >= UNUSUAL_VOLUME_RVOL:
                hits += 1
                hits_up += chg[k] > 0
                hits_down += chg[k] < 0
        days.append({"session": data.sessions[jj].isoformat(), "unusual": hits,
                     "up": int(hits_up), "down": int(hits_down), "covered": covered,
                     "members": int(m.sum())})
        if not _coverage_ok(covered, int(m.sum())):
            ok = False
        up, down, flat = up + int(hits_up), down + int(hits_down), flat + hits - int(hits_up) - int(hits_down)
    total = up + down + flat
    return {"window_sessions": VOLUME_WINDOW, "rvol_threshold": UNUSUAL_VOLUME_RVOL,
            "up_events": up if ok else None, "down_events": down if ok else None,
            "flat_events": flat if ok else None, "events": total if ok else None,
            "up_share_pct": _r(_pct(up, up + down), 2) if ok and (up + down) else None,
            "days": days,
            "reason": "" if ok else (f"RVOL / day-change coverage below {MIN_COVERAGE_PCT:g}% on "
                                     f"at least one of the last {VOLUME_WINDOW} sessions, or "
                                     "fewer sessions on record")}


def _realised(n: np.ndarray, j: int):
    if j < RV_WINDOW:
        return None
    w = n[j - RV_WINDOW:j + 1]
    if np.isnan(w).any():
        return None
    r = np.diff(np.log(w))
    return float(np.std(r, ddof=1) * math.sqrt(252) * 100)


def volatility_metrics(data: RegimeData) -> dict:
    j = len(data.sessions) - 1
    rv = _realised(data.nifty, j)
    rv_lag = _realised(data.nifty, j - RV_LAG) if j >= RV_LAG else None
    ctx = data.context.get(data.sessions[j]) or {}
    return {"realised_20d_pct": _r(rv, 2), "realised_20d_pct_lag": _r(rv_lag, 2),
            "lag_sessions": RV_LAG,
            "realised_change_pct": _r((rv / rv_lag - 1) * 100, 2) if rv and rv_lag else None,
            "india_vix": _r(ctx.get("india_vix"), 2), "vix_status": ctx.get("vix_status"),
            "vix_report_id": ctx.get("report_id"),
            "reason": "" if rv is not None else
            f"fewer than {RV_WINDOW + 1} complete NIFTY 50 sessions"}


def relative_metrics(data: RegimeData) -> dict:
    j = len(data.sessions) - 1
    ret = _returns(data, RETURN_WINDOW)
    m = data.member[j]
    n = data.nifty
    nret = None
    if j >= RETURN_WINDOW and not (math.isnan(n[j]) or math.isnan(n[j - RETURN_WINDOW])):
        nret = (n[j] / n[j - RETURN_WINDOW] - 1) * 100
    covered = m & ~np.isnan(ret)
    cov = int(covered.sum())
    ok = nret is not None and _coverage_ok(cov, int(m.sum()))
    out_n = int(np.sum(covered & (ret > nret))) if ok else None
    return {"window_sessions": RETURN_WINDOW, "nifty_return_20d_pct": _r(nret),
            "outperforming": out_n, "covered": cov, "members": int(m.sum()),
            "outperform_share_pct": _r(_pct(out_n, cov), 2) if ok else None,
            "reason": "" if ok else "NIFTY 50 20-session return or constituent coverage missing"}


def flow_metrics(data: RegimeData) -> dict:
    ctx = data.context.get(data.sessions[-1]) or {}
    return {"fii_net_cr": _r(ctx.get("fii_net_cr"), 2), "dii_net_cr": _r(ctx.get("dii_net_cr"), 2),
            "fii_status": ctx.get("fii_status"), "report_id": ctx.get("report_id"),
            "reason": "" if ctx.get("fii_net_cr") is not None else
            "no canonical report with FII/DII for this session"}


def session_metrics(data: RegimeData) -> dict:
    """All metrics for `data.last` - `data` must already be cut with `until()`."""
    j = len(data.sessions) - 1
    return {"session": data.last.isoformat(),
            "universe": {"label": data.universe_label, "members": int(data.member[j].sum()),
                         "membership_from": data.universe_session[j].isoformat(),
                         "membership_backdated": data.universe_session[j] > data.last},
            "trend": trend_metrics(data), "breadth": breadth_metrics(data),
            "sectors": sector_metrics(data), "volume": volume_metrics(data),
            "volatility": volatility_metrics(data), "relative": relative_metrics(data),
            "flows": flow_metrics(data)}


__all__ = ["session_metrics", "trend_metrics", "breadth_metrics", "sector_metrics",
           "volume_metrics", "volatility_metrics", "relative_metrics", "flow_metrics",
           "SMA_SHORT", "SMA_LONG", "RETURN_WINDOW", "BREADTH_PERSISTENCE", "VOLUME_WINDOW",
           "RV_WINDOW", "RV_LAG", "SECTOR_MIN_COVERED", "MIN_COVERAGE_PCT", "UNUSUAL_VOLUME_RVOL"]

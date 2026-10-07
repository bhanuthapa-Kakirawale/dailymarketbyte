"""One stock: price/volume series, structure, volume intelligence, relative performance, sector
context, Radar history and official events.

The series is the stored OHLCV, filtered to canonical sessions BEFORE anything positional
(`radar.ohlcv_service.apply_session_spine` - the pipeline's own rule), so a provider holiday
placeholder can never appear as a candle or enter an average. Overlays are only what the Radar
itself computes: SMA20/SMA50 (`radar.technical._sma`, evaluated per session), and the prior
20/50-session high/low it compares the close against. No EMA/200-day line: no Radar detector
computes one for stocks. RVOL history uses `market.relative_volume` per session.
"""
from __future__ import annotations

import datetime as dt
import math

from ..repository import DeskRepository
from . import candidates as cs

CHART_DAYS = 400          # calendar days of history loaded for the chart
RVOL_HISTORY_SESSIONS = 60


def aligned_series(repo: DeskRepository, symbol: str, session: dt.date, spine: list) -> list:
    from radar import ohlcv_service
    from storage.ohlcv_models import QualityStatus
    rows = [r for r in repo.ohlcv_rows([symbol], session - dt.timedelta(days=CHART_DAYS), session)
            if r.quality_status == QualityStatus.OK]
    by_symbol = {symbol: rows}
    ohlcv_service.apply_session_spine(by_symbol, frozenset(spine))
    return [{"date": r.session_date, "open": r.open, "high": r.high, "low": r.low,
             "close": r.close, "volume": r.volume}
            for r in sorted(by_symbol[symbol], key=lambda r: r.session_date)]


def chart_payload(series: list) -> dict:
    """Arrays for the Plotly chart. Only aligned sessions; a missing session is simply absent."""
    import pandas as pd

    import market
    from radar.technical import _sma
    from radar.thresholds import DEFAULT_TECHNICAL_THRESHOLDS as T
    dates = [p["date"].isoformat() for p in series]
    sma20 = [_sma(series[:i + 1], T.sma_short) for i in range(len(series))]
    sma50 = [_sma(series[:i + 1], T.sma_long) for i in range(len(series))]
    vols = pd.Series([p["volume"] for p in series], dtype="float64")
    rvol = [market.relative_volume(vols.iloc[:i + 1]) for i in range(len(series))]
    def clean(values):
        return [None if v is None or (isinstance(v, float) and math.isnan(v)) else v
                for v in values]
    return {"dates": dates, "open": clean(p["open"] for p in series),
            "high": clean(p["high"] for p in series), "low": clean(p["low"] for p in series),
            "close": clean(p["close"] for p in series), "volume": clean(p["volume"] for p in series),
            "sma20": clean(sma20), "sma50": clean(sma50), "rvol": clean(rvol)}


def stock_detail(repo: DeskRepository, symbol: str, session: dt.date, *, replay: dict,
                 spine: list, candidate_view: dict | None, official: dict) -> dict:
    universe, uni_session = repo.universe(session)
    meta = universe.get(symbol) or {}
    sym_r = (replay.get("symbols") or {}).get(symbol)
    history_rows = repo.symbol_candidate_history(symbol)
    all_rows = repo.candidates_between(dt.date(1900, 1, 1), session)
    novelty = cs.classify_recorded(spine, all_rows)
    selections = repo.symbol_selections(symbol)
    hist_dates = [r.session_date for r in history_rows]

    timeline = []
    for r in history_rows:
        if r.session_date > session:
            continue
        n = novelty.get((r.session_date, symbol))
        app = cs.appearance(spine, hist_dates, r.session_date)
        sel = selections.get(r.session_date)
        timeline.append({
            "session": r.session_date, "attention_level": r.attention_level,
            "families": list(r.active_families), "reason_codes": list(r.reason_codes),
            "direction": r.direction_compatibility, "persistence": r.persistence_state,
            "price_change_pct": r.price_change_pct,
            "novelty_type": n.novelty_type.value if n else None,
            "novelty_reason": n.reason if n else "",
            "appearance": app["label"], "sessions_since_prior": app["sessions_since"],
            "selected": sel is not None, "selection_bucket": (sel or {}).get("selection_bucket"),
        })
    timeline.reverse()

    series = aligned_series(repo, symbol, session, spine)
    latest = series[-1] if series else None
    rvol_hist = []
    if series:
        import pandas as pd

        import market
        vols = pd.Series([p["volume"] for p in series], dtype="float64")
        for i in range(max(0, len(series) - RVOL_HISTORY_SESSIONS), len(series)):
            rvol_hist.append({"date": series[i]["date"], "volume": series[i]["volume"],
                              "rvol": market.relative_volume(vols.iloc[:i + 1])})

    tech = (sym_r or {}).get("technical") or {}
    structure = None
    if tech:
        close = tech.get("close")
        def pos(lo, hi):
            if None in (close, lo, hi) or hi == lo:
                return None
            return (close - lo) / (hi - lo) * 100
        structure = {**tech,
                     "range_position_20_pct": pos(tech.get("prior_20_low"), tech.get("prior_20_high")),
                     "range_position_50_pct": pos(tech.get("prior_50_low"), tech.get("prior_50_high"))}

    return {
        "symbol": symbol, "company": meta.get("company"), "sector": meta.get("sector"),
        "industry": meta.get("industry"), "universe_session": uni_session,
        "in_universe": bool(meta), "session": session,
        "series_latest": latest, "series_sessions": len(series),
        "series_ends_at_session": bool(latest and latest["date"] == session),
        "replay_status": replay.get("status"), "replay_reason": replay.get("reason"),
        "replay_skip_reason": (replay.get("skipped_symbols") or {}).get(symbol),
        "symbol_replay": sym_r, "structure": structure,
        "volume": {"session_volume": (sym_r or {}).get("current_volume"),
                   "prior20_avg_volume": (sym_r or {}).get("prior20_avg_volume"),
                   "relative_volume": (sym_r or {}).get("relative_volume"),
                   "anomaly": (sym_r or {}).get("volume_anomaly"),
                   "history": rvol_hist},
        "relative": (sym_r or {}).get("relative"),
        "candidate": candidate_view,
        "timeline": timeline,
        "first_appearance": hist_dates[0] if hist_dates else None,
        "last_appearance": max((d for d in hist_dates if d <= session), default=None),
        "appearance_count": sum(1 for d in hist_dates if d <= session),
        "official": official["by_symbol"].get(symbol, []),
        "official_status": official["status"],
        "market_events_status": official.get("market_events_status", {}),
    }


__all__ = ["stock_detail", "chart_payload", "aligned_series"]

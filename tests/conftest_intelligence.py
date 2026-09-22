"""Shared builders for historical-intelligence tests.

A synthetic run of trading sessions with controlled patterns, so tests assert against known
answers rather than against whatever the fixtures happened to produce.
"""
import datetime as dt

import pandas as pd

from conftest import NOW, build_test_report

BASE_SESSION = dt.date(2026, 9, 21)


def trading_sessions(count, last=BASE_SESSION, skip=()):
    """`count` weekday sessions ending at `last`, oldest first.

    Weekends are skipped because they are not sessions; `skip` removes specific dates to
    simulate a holiday, which must also not count as a missing session.
    """
    out, day = [], last
    while len(out) < count:
        if day.weekday() < 5 and day not in skip:
            out.append(day)
        day -= dt.timedelta(days=1)
    return list(reversed(out))


def candle_frame(close=25000.0):
    index = pd.to_datetime(["2026-09-16", "2026-09-17", "2026-09-18"])
    return pd.DataFrame({"Open": [close] * 3, "High": [close + 50] * 3,
                         "Low": [close - 50] * 3, "Close": [close] * 3,
                         "ema20": [close - 100] * 3, "ema50": [close - 200] * 3}, index=index)


def market(session, pct=0.3, vix=13.0, close=25000.0):
    return {"chart_df": candle_frame(close), "recap_date": session, "prev_date": session,
            "open": close, "high": close + 100, "low": close - 100, "close": close,
            "prev": close - pct * 10, "chg": pct * 10, "pct": pct,
            "ema20": close - 100, "ema50": close - 200, "rsi": 55.0,
            "bank_pct": 0.3, "vix": vix,
            "levels": {"res": [close + 300], "sup": [close - 300]}, "trend": None,
            "pivot": {"P": close, "R1": close + 100, "S1": close - 100}}


def movers(symbols, volx=2.0, definition_version="2.0"):
    """Mover rows shaped as get_movers produces them, with a chosen relative-volume version."""
    rows = []
    for symbol in symbols:
        rows.append({"symbol": symbol, "name": f"{symbol} Ltd", "close": 100.0, "pct": 3.0,
                     "volx": volx, "reason": "", "reason_source": "NO_VERIFIED_CATALYST",
                     "_definition_version": definition_version})
    return rows


def session_report(session, pct=0.3, fii=None, dii=None, vix=13.0, sectors=None,
                   gainers=None, losers=None, report_date=None, demo=False,
                   definition_version="2.0"):
    """One canonical report for `session`, published the next day."""
    flows = None
    if fii is not None or dii is not None:
        flows = {"fii": fii, "dii": dii, "source": "NSE"}
    report = build_test_report(
        market(session, pct=pct, vix=vix), gainers=list(gainers or []),
        losers=list(losers or []), sectors=list(sectors or []), flows=flows,
        report_date=report_date or (session + dt.timedelta(days=1)), demo=demo, now=NOW)
    if definition_version != "2.0":
        _rewrite_relative_volume_version(report, definition_version)
    return report


def _rewrite_relative_volume_version(report, version):
    """Re-stamp relative-volume observations as an older definition, to prove that
    incompatible readings are excluded rather than silently compared."""
    from dataclasses import replace

    from core import Metric
    for fact in report.facts:
        if fact.metric is not Metric.STOCK_RELATIVE_VOLUME:
            continue
        fact.observations = [
            replace(o, metadata={**o.metadata, "definition_version": version})
            for o in fact.observations]


def seed(history, reports):
    for report in reports:
        history.save_report(report, artifact_path=f"{report.report_id}.json",
                            is_demo=bool(report.metadata.get("demo")))
    return history

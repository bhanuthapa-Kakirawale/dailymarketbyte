"""SYNTHETIC PRE-MARKET briefs - for previews, tests and the scenario types no stored real
morning covers. NEVER for publication.

Every stock is a placeholder (STOCK-A, STOCK-B); every quote's source is `demo_fixture`; every
brief has `synthetic=True`, which puts "Source: synthetic fixture" on the event card, and the
render script stamps "SYNTHETIC DATA - NOT REAL" on every frame. Index/market NAMES are real so
the layout is tested with real label lengths; their NUMBERS are invented. Freshness verdicts are
computed by the same `core.freshness` rules real readings face.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from core.event_calendar import EventValidation, Importance, ScheduledEvent
from core.freshness import IST, QuoteKind, live_freshness, us_close_freshness
from core.sources import GROUP_DEMO, SRC_DEMO
from presentation.pre_plan import PreMarketBrief
from providers.premarket import PreMarketQuote, VixReading
from core.freshness import FreshnessStatus

PRE_DATE = dt.date(2026, 10, 6)          # a Tuesday
PREV = dt.date(2026, 10, 5)              # Monday
US_SESSION = dt.date(2026, 10, 5)
AS_OF = dt.datetime(2026, 10, 6, 7, 45, tzinfo=IST)


def _chart(closes, end=PREV):
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) * 1.0025 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.9975 for o, c in zip(opens, closes)]
    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=dates)
    df["ema20"] = df["Close"].ewm(span=20).mean()
    df["ema50"] = df["Close"].ewm(span=50).mean()
    return df


def _nifty(closes, open_, high, low):
    df = _chart(closes)
    c, p = closes[-1], closes[-2]
    df.iloc[-1, df.columns.get_loc("Open")] = open_
    df.iloc[-1, df.columns.get_loc("High")] = high
    df.iloc[-1, df.columns.get_loc("Low")] = low
    return {"close": c, "pct": (c / p - 1) * 100, "chg": c - p, "open": open_, "high": high,
            "low": low, "prev": p, "chart_df": df, "fact_ids": ["synthetic-nifty"]}


def _us(key, name, pct, level):
    st, why = us_close_freshness(US_SESSION, PRE_DATE, AS_OF)
    return PreMarketQuote(key, name, "US", QuoteKind.SESSION_CLOSE, level, pct,
                          level / (1 + pct / 100), US_SESSION - dt.timedelta(days=3), US_SESSION,
                          None, AS_OF, st, why, source=SRC_DEMO, source_type="DERIVED",
                          independence_group=GROUP_DEMO, validation_status="SYNTHETIC")


def _live(key, name, pct, level, minute=40, region="ASIA"):
    ts = dt.datetime(2026, 10, 6, 7, minute, tzinfo=IST)
    st, why = live_freshness(ts, PRE_DATE, AS_OF, region)
    return PreMarketQuote(key, name, region, QuoteKind.LIVE, level, pct, level / (1 + pct / 100),
                          PREV, PRE_DATE, ts, AS_OF, st, why, source=SRC_DEMO,
                          source_type="DERIVED", independence_group=GROUP_DEMO,
                          validation_status="SYNTHETIC",
                          reference_label="its previous settlement" if region == "GIFT" else "")


def _vix(latest, prev):
    return VixReading(latest, PREV, prev, dt.date(2026, 10, 1), (latest / prev - 1) * 100,
                      FreshnessStatus.FRESH, "synthetic previous-session VIX", source=SRC_DEMO,
                      validation_status="SYNTHETIC")


def _stock(sym, chg, takeaway, trend):
    closes = [1000 * (1 + trend * i / 20 + 0.01 * math.sin(i)) for i in range(20)]
    return {"symbol": sym, "change": f"{chg:+.2f}%", "positive": chg >= 0, "takeaway": takeaway,
            "support": "", "event_family": "SYNTHETIC", "closes": closes}


def _wave(n, base, amp, drift=0.0, seed=0.0):
    return [base * (1 + drift * i) + amp * math.sin(i / 2.7 + seed) for i in range(n)]


SECTORS = ["BANK", "IT", "AUTO", "PHARMA", "FMCG", "METAL"]


def synthetic_brief(kind: str) -> PreMarketBrief:
    """QUIET (A), RISK_OFF (B), RISK_ON (C), EVENT (D), BIG_PREV (E)."""
    kind = kind.upper()
    notes = [f"SYNTHETIC scenario {kind} - invented numbers, placeholder stocks"]
    if kind == "QUIET":
        closes = _wave(40, 24000, 60, seed=0.4)
        closes[-2], closes[-1] = 24010.0, 24043.0
        nifty = _nifty(closes, 24015.0, 24090.0, 23980.0)
        cues = [_us("SP500", "S&P 500", 0.21, 6620), _us("NASDAQ", "NASDAQ", 0.34, 22410),
                _us("DOW", "DOW JONES", -0.12, 46210), _live("NIKKEI", "NIKKEI 225", 0.18, 44100),
                _live("HANGSENG", "HANG SENG", -0.27, 26800)]
        return PreMarketBrief(PRE_DATE, AS_OF, PREV, nifty,
                              sectors=[{"name": n, "pct": p} for n, p in
                                       zip(SECTORS, (0.31, 0.12, -0.08, 0.22, -0.15, 0.05))],
                              flows={"fii": -420.0, "dii": 610.0}, global_cues=cues,
                              vix=_vix(11.42, 11.60), synthetic=True, notes=notes,
                              sources={"fixture": "products.pre_fixtures.QUIET"})
    if kind == "RISK_OFF":
        closes = _wave(40, 24500, 90, drift=-0.0006, seed=1.2)
        closes[-2], closes[-1] = 24080.0, 23864.0
        nifty = _nifty(closes, 24060.0, 24110.0, 23830.0)
        cues = [_us("SP500", "S&P 500", -1.42, 6480), _us("NASDAQ", "NASDAQ", -2.13, 21760),
                _us("DOW", "DOW JONES", -0.91, 45800), _live("NIKKEI", "NIKKEI 225", -1.64, 43210),
                _live("HANGSENG", "HANG SENG", -0.82, 26420)]
        gift = _live("GIFT", "GIFT NIFTY", -0.74, 23688, minute=38, region="GIFT")
        return PreMarketBrief(PRE_DATE, AS_OF, PREV, nifty,
                              sectors=[{"name": n, "pct": p} for n, p in
                                       zip(SECTORS, (-1.38, -1.92, -0.64, 0.41, 0.12, -1.05))],
                              flows={"fii": -3240.0, "dii": 2875.0}, global_cues=cues, gift=gift,
                              vix=_vix(14.86, 13.57), synthetic=True, notes=notes,
                              stock_facts=[_stock("STOCK-A", -4.12, "Price fell below its recent "
                                                  "range with unusually high volume.", -0.05)],
                              sources={"fixture": "products.pre_fixtures.RISK_OFF"})
    if kind == "RISK_ON":
        closes = _wave(40, 24000, 70, drift=0.0004, seed=2.0)
        closes[-2], closes[-1] = 24210.0, 24302.0
        nifty = _nifty(closes, 24190.0, 24330.0, 24170.0)
        cues = [_us("SP500", "S&P 500", 1.21, 6720), _us("NASDAQ", "NASDAQ", 1.84, 22910),
                _us("DOW", "DOW JONES", 0.77, 46700), _live("NIKKEI", "NIKKEI 225", 1.12, 44800),
                _live("HANGSENG", "HANG SENG", 0.46, 26990)]
        gift = _live("GIFT", "GIFT NIFTY", 0.52, 24428, minute=40, region="GIFT")
        return PreMarketBrief(PRE_DATE, AS_OF, PREV, nifty,
                              sectors=[{"name": n, "pct": p} for n, p in
                                       zip(SECTORS, (0.62, 1.41, 0.35, -0.18, 0.09, 0.77))],
                              flows={"fii": 1850.0, "dii": 940.0}, global_cues=cues, gift=gift,
                              vix=_vix(11.05, 11.48), synthetic=True, notes=notes,
                              sources={"fixture": "products.pre_fixtures.RISK_ON"})
    if kind == "EVENT":
        closes = _wave(40, 24100, 50, seed=0.9)
        closes[-2], closes[-1] = 24120.0, 24266.0
        nifty = _nifty(closes, 24130.0, 24281.0, 24101.0)
        cues = [_us("SP500", "S&P 500", 0.38, 6640), _us("NASDAQ", "NASDAQ", 0.61, 22500),
                _us("DOW", "DOW JONES", 0.14, 46300), _live("NIKKEI", "NIKKEI 225", -0.44, 43900)]
        ev = ScheduledEvent(event_name="RBI monetary policy decision", event_date=PRE_DATE,
                            event_time=dt.time(10, 0), time_label="10:00 AM IST",
                            event_type="RBI", tag="RBI POLICY",
                            source="synthetic fixture (not an RBI schedule)",
                            importance=Importance.HIGH,
                            validation_status=EventValidation.OFFICIAL_SCHEDULE,
                            note="SYNTHETIC")
        return PreMarketBrief(PRE_DATE, AS_OF, PREV, nifty,
                              sectors=[{"name": n, "pct": p} for n, p in
                                       zip(SECTORS, (0.94, 0.21, 0.48, -0.12, 0.33, 0.27))],
                              flows={"fii": 760.0, "dii": 1120.0}, global_cues=cues, events=[ev],
                              vix=_vix(12.10, 11.52), synthetic=True, notes=notes,
                              stock_facts=[_stock("STOCK-B", 3.41, "Price moved above its 50-day "
                                                  "average with above-normal volume.", 0.04)],
                              sources={"fixture": "products.pre_fixtures.EVENT"})
    raise ValueError(f"unknown synthetic PRE scenario {kind!r}")


SCENARIOS = ("QUIET", "RISK_OFF", "RISK_ON", "EVENT")

__all__ = ["synthetic_brief", "SCENARIOS", "PRE_DATE", "PREV", "AS_OF"]

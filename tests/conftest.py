"""Shared deterministic fixtures. Nothing here touches the network, a clock, or a provider."""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Metric, Observation, SourceType                      # noqa: E402
from core.models import UNIT_PERCENT, UNIT_POINTS                     # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

SESSION = dt.date(2026, 9, 18)
PREV_SESSION = dt.date(2026, 9, 17)
REPORT_DATE = dt.date(2026, 9, 21)
NOW = dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST)


def observation(value, source="yahoo_finance", kind=SourceType.SECONDARY,
                metric=Metric.INDEX_CLOSE, instrument="NIFTY 50",
                market_date=SESSION, unit=UNIT_POINTS, retrieved_at=NOW, **meta):
    """Build one Observation with everything but the interesting field held constant."""
    return Observation(metric=metric, instrument=instrument, value=value, unit=unit,
                       market_date=market_date, source_name=source, source_type=kind,
                       retrieved_at=retrieved_at, metadata=meta)


@pytest.fixture
def yahoo_close():
    return observation(25140.35, "yahoo_finance", SourceType.SECONDARY)


@pytest.fixture
def nse_close():
    return observation(25141.10, "nse_website", SourceType.PRIMARY)


@pytest.fixture
def gemini_close():
    return observation(25140.80, "gemini", SourceType.AI)


@pytest.fixture
def market_dict():
    """Shaped exactly like market.analyze()'s return, minus the chart DataFrame."""
    return {
        "recap_date": SESSION, "prev_date": PREV_SESSION,
        "open": 25010.0, "high": 25190.4, "low": 24985.1, "close": 25140.35,
        "prev": 25067.9, "chg": 72.45, "pct": 0.289,
        "ema20": 24980.2, "ema50": 24810.6, "rsi": 58.4,
        "bank_pct": 0.62, "vix": 13.4,
        "levels": {"res": [25320.0, 25480.0], "sup": [24980.0, 24820.0]},
        "trend": {"kind": "support", "now": 24900.0, "slope": 3.1,
                  "x0": 10, "y0": 24700.0, "x1": 99, "y1": 24900.0},
        "pivot": {"P": 25105.28, "R1": 25225.46, "S1": 25020.16},
    }


@pytest.fixture
def movers():
    # The gainer's reason is exactly its headline, which is what ai_pass() writes when it
    # falls back to Google News; the loser carries ai_pass()'s no-catalyst sentinel.
    gainers = [{"symbol": "STOCK-A", "name": "Alpha Ltd", "close": 168.4, "pct": 4.8,
                "volx": 2.6, "reason": "Alpha Ltd wins large infrastructure order",
                "headlines": [{"title": "Alpha Ltd wins large infrastructure order",
                               "source": "Wire", "date": SESSION}]}]
    losers = [{"symbol": "STOCK-F", "name": "Foxtrot Ltd", "close": 2310.0, "pct": -3.4,
               "volx": 2.1, "reason": "No major company-specific news; moved with sector trend.",
               "headlines": []}]
    return gainers, losers


@pytest.fixture
def tiles():
    return [
        {"label": "GIFT NIFTY", "value": 25215.0, "pct": 0.30, "dec": 0, "prefix": ""},
        {"label": "DOW JONES", "value": 44120.0, "pct": 0.42, "dec": 0, "prefix": ""},
        {"label": "USD / INR", "value": 86.35, "pct": 0.12, "dec": 2, "prefix": ""},
    ]


@pytest.fixture
def ai_facts():
    return {"gift": {"value": 25215.0, "pct": 0.30},
            "nifty_close": 25140.80,
            "brent": {"value": 78.2, "pct": -1.10}}


@pytest.fixture
def sectors():
    return [{"name": "IT", "pct": 1.24}, {"name": "Bank", "pct": 0.41},
            {"name": "Metal", "pct": -0.88}]


@pytest.fixture
def events():
    return [{"tag": "F&O", "text": "Nifty weekly F&O expiry today"},
            {"tag": "IPO", "text": "Example IPO opens for subscription"}]

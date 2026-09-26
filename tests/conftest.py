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


@pytest.fixture(autouse=True)
def _isolate_config_out_dir(tmp_path, monkeypatch):
    """Point `config.OUT_DIR` at a throwaway per-test directory, autouse and unconditional.

    Several acquisition paths (e.g. `market.get_universe_technical_series`'s OHLCV
    write-through, Phase 4.2 Packet 5.2) resolve their on-disk location from `config.OUT_DIR`
    at call time rather than taking it as a parameter, and a test that has no reason to know
    about that write-through would otherwise silently create/modify real files under this
    repo's own `output/` directory. Tests stay "fully offline" only if they are also
    "fully filesystem-isolated" from the real project output - a test that wants the real
    default may still override this with its own `monkeypatch.setattr(config, "OUT_DIR", ...)`.
    """
    import config
    monkeypatch.setattr(config, "OUT_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _offline_official_lists(monkeypatch):
    """The public-intelligence fetchers (F&O ban file, ASM/GSM, NSE IPO lists) never reach the
    network in tests: each returns the fail-closed UNAVAILABLE result it returns in production
    when the exchange cannot be reached. Tests that need events inject synthetic ones."""
    import exchange_watch
    import exchange_watch.sources as exs
    import ipo_watch
    import ipo_watch.sources as ips
    from exchange_watch.models import SourceResult

    def _ban(now_iso, get=None):
        return SourceResult("nse_fo_secban", "UNAVAILABLE", reason="network disabled in tests")

    def _surv(now_iso, nse=None):
        return [SourceResult("nse_surveillance", "UNAVAILABLE", reason="network disabled in tests")]

    def _ipo(data_as_of, now_iso, nse=None):
        return [], ["NSE client unavailable: network disabled in tests"]   # production's note

    for mod in (exchange_watch, exs):
        monkeypatch.setattr(mod, "fetch_fo_ban", _ban)
        monkeypatch.setattr(mod, "fetch_surveillance", _surv)
    for mod in (ipo_watch, ips):
        monkeypatch.setattr(mod, "fetch_nse_issues", _ipo)


@pytest.fixture(autouse=True)
def _offline_index_fallback(monkeypatch):
    """Benchmark gap recovery (`market.recover_index_gaps`) fetches NSE's end-of-day index file
    whenever a synthetic series skips a canonical weekday. Tests stay offline: the default
    fallback source is "unavailable"; a test that exercises recovery passes its own `fetch`
    or patches `market.nse_index_close_archive`."""
    import market
    from radar import relative_acquisition
    monkeypatch.setattr(market, "_ARCHIVE_CACHE", {})
    monkeypatch.setattr(market, "nse_index_close_archive",
                        lambda session_date: {"error": "network disabled in tests"})
    monkeypatch.setattr(relative_acquisition, "_LAST_RECOVERY", [])


def observation(value, source="yahoo_finance", kind=SourceType.SECONDARY,
                metric=Metric.INDEX_CLOSE, instrument="NIFTY 50",
                market_date=SESSION, unit=UNIT_POINTS, retrieved_at=NOW,
                observed_at=None, independence_group="", **meta):
    """Build one Observation with everything but the interesting field held constant.

    `independence_group` is explicit rather than swept into metadata: a test about source
    independence that silently set no group would pass for the wrong reason.
    """
    return Observation(metric=metric, instrument=instrument, value=value, unit=unit,
                       market_date=market_date, source_name=source, source_type=kind,
                       retrieved_at=retrieved_at, observed_at=observed_at,
                       independence_group=independence_group, metadata=meta)


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
def market_dict(candle_frame):
    """Shaped exactly like market.analyze()'s return, including the chart frame."""
    return {
        "chart_df": candle_frame,
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
    # Shaped as ai_pass() now returns movers: the reason plus the provenance recorded at the
    # moment that branch was taken. The gainer fell back to a Google News headline; the
    # loser hit the no-catalyst sentinel.
    gainers = [{"symbol": "STOCK-A", "name": "Alpha Ltd", "close": 168.4, "pct": 4.8,
                "volx": 2.6, "reason": "Alpha Ltd wins large infrastructure order",
                "reason_source": "GOOGLE_NEWS_RSS", "reason_publisher": "Wire",
                "reason_headline_date": str(SESSION),
                "headlines": [{"title": "Alpha Ltd wins large infrastructure order",
                               "source": "Wire", "date": SESSION}]}]
    losers = [{"symbol": "STOCK-F", "name": "Foxtrot Ltd", "close": 2310.0, "pct": -3.4,
               "volx": 2.1, "reason": "No major company-specific news; moved with sector trend.",
               "reason_source": "NO_VERIFIED_CATALYST", "reason_publisher": None,
               "reason_headline_date": None, "headlines": []}]
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
    return [{"tag": "F&O", "text": "Nifty weekly F&O expiry today",
             "source": "RULE_FNO_EXPIRY", "publisher": None},
            {"tag": "IPO", "text": "Example IPO opens for subscription",
             "source": "GEMINI_SEARCH", "publisher": None}]


@pytest.fixture
def candle_frame():
    """Minimal OHLC+EMA frame in the shape market.analyze() puts in m["chart_df"]."""
    import pandas as pd
    index = pd.to_datetime(["2026-09-16", "2026-09-17", "2026-09-18"])
    return pd.DataFrame({"Open": [24900.0, 25010.0, 25060.0],
                         "High": [25050.0, 25120.0, 25190.4],
                         "Low": [24850.0, 24960.0, 24985.1],
                         "Close": [25000.0, 25067.9, 25140.35],
                         "ema20": [24930.0, 24955.0, 24980.2],
                         "ema50": [24780.0, 24795.0, 24810.6]}, index=index)


def build_test_report(market_dict, gainers=None, losers=None, events=None, sectors=None,
                      tiles=None, ai_facts=None, nse_idx=None, flows=None,
                      nifty_reason=None, report_date=REPORT_DATE, demo=False,
                      content_safety=None, now=NOW, movers_coverage="FULL"):
    """Build a MarketReport through the real provider path, exactly as main.build_report does.

    Tests go through the providers rather than hand-assembling a report, so they exercise
    the acquisition-to-report wiring that Phase 2 is actually about.

    `movers_coverage` defaults to a complete, healthy universe (the state every pre-freeze
    test assumed); pass None for a report with no coverage record, or a dict to test the gate.
    """
    from adapters import report_builder
    from providers import GeminiProvider, NewsProvider, NseProvider, YahooProvider

    gainers, losers = list(gainers or []), list(losers or [])
    events = list(events or [])
    tiles, sectors = list(tiles or []), list(sectors or [])
    ai_facts, nse_idx = dict(ai_facts or {}), dict(nse_idx or {})
    session_date = market_dict["recap_date"]

    yahoo = YahooProvider(session_date, report_date, now, demo=demo)
    nse = NseProvider(session_date, report_date, now, demo=demo)
    gemini = GeminiProvider(session_date, report_date, now, demo=demo)
    narrator = NewsProvider(session_date, report_date, now, demo=demo)

    ai_labels = frozenset({label for label, key in (("GIFT NIFTY", "gift"), ("BRENT CRUDE", "brent"))
                           if ai_facts.get(key)})
    session_result = yahoo.from_market_dict(market_dict)
    results = [session_result, nse.indices(nse_idx), yahoo.globals(tiles, ai_labels=ai_labels),
               yahoo.sectors(sectors, nse_idx), nse.fii_dii(flows),
               yahoo.movers(gainers, losers), gemini.facts(ai_facts)]
    observations = [o for r in results for o in r.observations]
    narrative = narrator.from_ai_pass(nifty_reason, events, gainers, losers,
                                      ai_facts).payload["narrative"]

    return report_builder.build_report(
        session=session_result.payload["index_session"], narrative=narrative,
        observations=observations, tiles=tiles, flows=flows, sectors=sectors,
        gainers=gainers, losers=losers, report_date=report_date,
        universe_label="Nifty 100", demo=demo, now=now, content_safety=content_safety,
        movers_coverage=(full_movers_coverage() if movers_coverage == "FULL" else movers_coverage))


def full_movers_coverage(expected=100, observed=None, validated=None, excluded=()):
    """A `market.get_movers_audited` coverage record (all 100 Nifty 100 stocks by default)."""
    observed = expected if observed is None else observed
    validated = observed if validated is None else validated
    return {"universe_expected": expected, "universe_observed": observed,
            "universe_validated": validated,
            "coverage_pct": round(100.0 * validated / expected, 2) if expected else 0.0,
            "excluded": list(excluded), "coverage_basis": "validated / expected"}


@pytest.fixture
def declare_nse_holiday(monkeypatch):
    """Register synthetic dates as official NSE trading holidays for one test.

    Since the session-alignment patch the canonical calendar (`core.trading_calendar`) is NSE's
    published holiday list plus the benchmark's own bars - a weekday merely ABSENT from a
    synthetic benchmark is now a benchmark gap on a real session (the 2026-09-22 case), not a
    holiday. A test that means "NSE was closed that day" must say so."""
    from core import trading_calendar as tc

    def _declare(*dates):
        hol = {y: set(v) for y, v in tc.NSE_TRADING_HOLIDAYS.items()}
        for d in dates:
            hol.setdefault(d.year, set()).add(d)
        monkeypatch.setattr(tc, "NSE_TRADING_HOLIDAYS", {y: frozenset(v) for y, v in hol.items()})
    return _declare

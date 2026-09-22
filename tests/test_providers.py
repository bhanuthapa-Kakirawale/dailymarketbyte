"""Acquisition-time provenance: recorded where it is known, never reconstructed later."""
import datetime as dt

from conftest import IST, NOW, REPORT_DATE, SESSION

import news
from core import Metric, SourceType
from core.sources import GROUP_NSE, SRC_GOOGLE_NEWS, SRC_NSE
from providers import GeminiProvider, NewsProvider, NseProvider, YahooProvider


def _nse():
    return NseProvider(SESSION, REPORT_DATE, NOW)


def _narrator(demo=False):
    return NewsProvider(SESSION, REPORT_DATE, NOW, demo=demo)


# --------------------------------------------------------------------- NSE timestamps
def test_nse_market_timestamp_reaches_observed_at():
    """NSE is the only source here that publishes its own 'as of' time. Keeping it is what
    makes a freshness check mean "how old is this number" rather than "when did we fetch"."""
    stamp = dt.datetime(2026, 9, 18, 15, 30, tzinfo=IST)
    result = _nse().indices({"NIFTY 50": {"last": 25141.10, "pct": 0.29, "observed_at": stamp,
                                          "source_timestamp": "18-Sep-2026 15:30:00"}})
    close = next(o for o in result.observations if o.metric is Metric.INDEX_CLOSE)
    assert close.observed_at == stamp
    assert close.observed_at.tzinfo is not None
    assert close.metadata["source_timestamp"] == "18-Sep-2026 15:30:00"


def test_nse_observations_are_primary_and_grouped_to_nse():
    result = _nse().indices({"NIFTY 50": {"last": 25141.10, "pct": 0.29, "observed_at": None}})
    assert {o.source_type for o in result.observations} == {SourceType.PRIMARY}
    assert {o.independence_group for o in result.observations} == {GROUP_NSE}
    assert {o.source_name for o in result.observations} == {SRC_NSE}


def test_nse_parses_its_timestamp_from_the_real_payload_shape():
    import market
    parsed = market._nse_timestamp("18-Sep-2026 15:30:00")
    assert parsed.date() == dt.date(2026, 9, 18) and parsed.hour == 15 and parsed.minute == 30
    assert parsed.tzinfo is not None


def test_missing_nse_data_yields_no_observations():
    assert _nse().indices({}).observations == []


# --------------------------------------------------------------------- catalyst provenance
def test_gemini_catalyst_provenance_is_retained(market_dict):
    rows = [{"symbol": "ABC", "reason": "Order win lifted the stock.",
             "reason_source": news.ORIGIN_GEMINI, "reason_publisher": None}]
    bundle = _narrator().from_ai_pass({"text": "", "source": news.ORIGIN_NONE},
                                      [], rows, [], {}).payload["narrative"]
    catalyst = bundle.catalysts["ABC"].to_dict()
    assert catalyst["origin"] == "GEMINI"
    assert catalyst["source"] == "gemini" and catalyst["source_type"] == SourceType.AI.value
    assert catalyst["inferred"] is False


def test_google_news_catalyst_provenance_is_retained():
    """The publisher, not Google, determines the independence group."""
    rows = [{"symbol": "ABC", "reason": "Alpha wins order", "reason_source": news.ORIGIN_GOOGLE_NEWS,
             "reason_publisher": "Reuters", "reason_headline_date": "2026-09-18"}]
    bundle = _narrator().from_ai_pass(None, [], rows, [], {}).payload["narrative"]
    catalyst = bundle.catalysts["ABC"].to_dict()
    assert catalyst["origin"] == "GOOGLE_NEWS_RSS"
    assert catalyst["source"] == SRC_GOOGLE_NEWS
    assert catalyst["publisher"] == "Reuters"
    assert catalyst["independence_group"] == "PUBLISHER:reuters"
    assert catalyst["headline_date"] == "2026-09-18"


def test_absent_catalyst_is_reported_as_such():
    rows = [{"symbol": "ABC", "reason": "No major company-specific news; moved with sector trend.",
             "reason_source": news.ORIGIN_NONE}]
    catalyst = _narrator().from_ai_pass(None, [], rows, [], {}).payload["narrative"] \
        .catalysts["ABC"].to_dict()
    assert catalyst["type"] == "NO_VERIFIED_CATALYST"
    assert catalyst["source"] is None


# --------------------------------------------------------------------- event provenance
def test_rule_generated_event_provenance_is_retained():
    events = [{"tag": "F&O", "text": "Nifty weekly F&O expiry today",
               "source": news.ORIGIN_RULE_EXPIRY, "publisher": None}]
    item = _narrator().from_ai_pass(None, events, [], [], {}).payload["narrative"].events[0].to_dict()
    assert item["origin"] == "RULE_FNO_EXPIRY"
    assert item["source"] == "expiry_calendar_rule"
    assert item["provenance_resolved"] is True


def test_gemini_and_news_event_provenance_is_retained():
    events = [{"tag": "IPO", "text": "Example IPO opens", "source": news.ORIGIN_GEMINI_SEARCH,
               "publisher": None},
              {"tag": "RESULTS", "text": "Company results today", "source": news.ORIGIN_GOOGLE_NEWS,
               "publisher": "Mint"}]
    items = [e.to_dict() for e in
             _narrator().from_ai_pass(None, events, [], [], {}).payload["narrative"].events]
    assert items[0]["source"] == "gemini" and items[0]["provenance_resolved"] is True
    assert items[1]["publisher"] == "Mint"
    assert items[1]["independence_group"] == "PUBLISHER:mint"


def test_no_event_is_left_with_unresolved_provenance():
    """Phase 1 marked Gemini-vs-RSS events unresolved because ai_pass discarded the
    distinction. Phase 2 records it at acquisition, so nothing should be unresolved."""
    events = [{"tag": "IPO", "text": "A", "source": news.ORIGIN_GEMINI_SEARCH, "publisher": None},
              {"tag": "F&O", "text": "B", "source": news.ORIGIN_RULE_EXPIRY, "publisher": None},
              {"tag": "RESULTS", "text": "C", "source": news.ORIGIN_GOOGLE_NEWS, "publisher": "Mint"}]
    items = [e.to_dict() for e in
             _narrator().from_ai_pass(None, events, [], [], {}).payload["narrative"].events]
    assert all(i["provenance_resolved"] for i in items)


def test_ai_pass_records_provenance_for_every_branch():
    """Guards the acquisition contract itself: ai_pass must stamp each reason and event with
    the branch that produced it, since nothing downstream can recover it afterwards."""
    import inspect
    source = inspect.getsource(news.ai_pass)
    for marker in ("reason_source", "reason_publisher", "ORIGIN_GOOGLE_NEWS", "ORIGIN_NONE"):
        assert marker in source, marker


# --------------------------------------------------------------------- yahoo session payload
def test_yahoo_session_payload_is_typed_not_a_raw_dict(market_dict):
    result = YahooProvider(SESSION, REPORT_DATE, NOW).from_market_dict(market_dict)
    session = result.payload["index_session"]
    assert session.close == market_dict["close"]
    assert session.session_date == SESSION
    assert session.candles and session.candles[-1]["close"] == market_dict["close"]


def test_yahoo_session_carries_the_candle_series_the_chart_draws(market_dict):
    session = YahooProvider(SESSION, REPORT_DATE, NOW).from_market_dict(market_dict) \
        .payload["index_session"]
    assert len(session.candles) == len(market_dict["chart_df"])
    for key in ("date", "open", "high", "low", "close", "ema20", "ema50"):
        assert key in session.candles[0], key


def test_demo_mode_relabels_provider_sources(market_dict):
    result = YahooProvider(SESSION, REPORT_DATE, NOW, demo=True).from_market_dict(market_dict)
    assert {o.source_name for o in result.observations} == {"demo_fixture"}


def test_gemini_provider_marks_everything_ai(ai_facts):
    result = GeminiProvider(SESSION, REPORT_DATE, NOW).facts(ai_facts)
    assert result.observations
    assert {o.source_type for o in result.observations} == {SourceType.AI}

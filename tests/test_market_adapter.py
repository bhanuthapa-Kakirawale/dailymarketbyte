"""Adapters must recover provenance the legacy dicts discard - and invent none."""
from conftest import NOW, SESSION

from adapters.market_adapter import MarketAdapter
from adapters.news_adapter import NO_CATALYST_TEXT, NewsAdapter
from core import Metric, SourceType


def _adapter(demo=False):
    return MarketAdapter(SESSION, NOW, demo=demo)


def _news(demo=False):
    return NewsAdapter(SESSION, SESSION, NOW, demo=demo)


def _by(observations, metric, instrument=None):
    return next(o for o in observations
                if o.metric is metric and (instrument is None or o.instrument == instrument))


# --------------------------------------------------------------------- index observations
def test_nifty_observations_are_secondary_from_yahoo(market_dict):
    obs = _adapter().observe_nifty(market_dict)
    close = _by(obs, Metric.INDEX_CLOSE)
    assert close.value == 25140.35
    assert close.source_name == "yahoo_finance"
    assert close.source_type is SourceType.SECONDARY
    assert close.market_date == SESSION
    assert close.retrieved_at.tzinfo is not None


def test_nifty_observations_skip_absent_optional_values(market_dict):
    market_dict["vix"] = market_dict["bank_pct"] = None
    metrics = {o.metric for o in _adapter().observe_nifty(market_dict)}
    assert Metric.VOLATILITY_INDEX not in metrics


def test_nse_indices_are_primary():
    obs = _adapter().observe_nse_indices({"NIFTY 50": {"last": 25141.10, "pct": 0.29}})
    assert {o.source_type for o in obs} == {SourceType.PRIMARY}
    assert _by(obs, Metric.INDEX_CLOSE).value == 25141.10


def test_nse_indices_absent_yields_nothing():
    assert _adapter().observe_nse_indices({}) == []
    assert _adapter().observe_nse_indices(None) == []


def test_technicals_are_derived(market_dict):
    obs = _adapter().observe_technicals(market_dict)
    assert obs and {o.source_type for o in obs} == {SourceType.DERIVED}
    assert any(o.instrument == "NIFTY 50 PIVOT R1" for o in obs)


# --------------------------------------------------------------------- global cue provenance
def test_ai_sourced_tiles_are_marked_ai(tiles):
    """GIFT Nifty exists only because Gemini found it; the tile dict itself cannot say so."""
    obs = _adapter().observe_globals(tiles, ai_labels=frozenset({"GIFT NIFTY"}))
    gift = _by(obs, Metric.INDEX_LEVEL, "GIFT NIFTY")
    dow = _by(obs, Metric.INDEX_LEVEL, "DOW JONES")
    assert gift.source_type is SourceType.AI and gift.source_name == "gemini"
    assert dow.source_type is SourceType.SECONDARY and dow.source_name == "yahoo_finance"


def test_global_tiles_map_to_the_right_metric(tiles):
    obs = _adapter().observe_globals(tiles)
    assert _by(obs, Metric.FX_RATE, "USD / INR").value == 86.35


def test_unknown_tile_label_is_left_unobserved():
    """Honest gap beats a fabricated source: an unrecognised tile produces no observation."""
    assert _adapter().observe_globals([{"label": "MYSTERY INDEX", "value": 1.0, "pct": 0.1}]) == []


# --------------------------------------------------------------------- sector provenance
def test_sectors_split_between_nse_and_yahoo(sectors):
    """get_sectors() prefers NSE per sector and records nothing about which it used."""
    obs = _adapter().observe_sectors(sectors, {"NIFTY IT": {"last": 1.0, "pct": 1.24}})
    by_name = {o.instrument: o for o in obs}
    assert by_name["IT"].source_type is SourceType.PRIMARY
    assert by_name["Bank"].source_type is SourceType.SECONDARY


# --------------------------------------------------------------------- flows
def test_fii_dii_from_nse_is_primary():
    obs = _adapter().observe_fii_dii({"fii": -1240.5, "dii": 2105.3, "source": "NSE"})
    assert {o.source_type for o in obs} == {SourceType.PRIMARY}
    assert _by(obs, Metric.FII_NET_CASH).value == -1240.5


def test_fii_dii_from_gemini_fallback_is_ai():
    """The legacy "web" tag is the Gemini fallback and must not be mistaken for NSE."""
    obs = _adapter().observe_fii_dii({"fii": -1240.5, "dii": 2105.3, "source": "web"})
    assert {o.source_type for o in obs} == {SourceType.AI}


def test_absent_flows_yield_nothing():
    assert _adapter().observe_fii_dii(None) == []


# --------------------------------------------------------------------- movers
def test_movers_produce_price_change_and_volume(movers):
    gainers, _ = movers
    obs = _adapter().observe_movers(gainers, "gainer")
    assert {o.metric for o in obs} == {Metric.STOCK_CLOSE, Metric.STOCK_CHANGE_PCT,
                                       Metric.STOCK_RELATIVE_VOLUME}
    assert _by(obs, Metric.STOCK_CHANGE_PCT).metadata["bucket"] == "gainer"


def test_relative_volume_records_its_definition(movers):
    gainers, _ = movers
    volume = _by(_adapter().observe_movers(gainers, "gainer"), Metric.STOCK_RELATIVE_VOLUME)
    assert volume.metadata["lookback_sessions"] == 10
    assert volume.source_type is SourceType.DERIVED


# --------------------------------------------------------------------- demo isolation
def test_demo_mode_relabels_every_source(market_dict):
    """Synthetic numbers must be unmistakable in an archive, as they are on screen."""
    obs = _adapter(demo=True).observe_nifty(market_dict)
    assert {o.source_name for o in obs} == {"demo_fixture"}
    assert {o.source_type for o in obs} == {SourceType.DERIVED}


# --------------------------------------------------------------------- news adapter
def test_gemini_facts_are_all_ai(ai_facts):
    obs = _news().observe_facts(ai_facts)
    assert obs and {o.source_type for o in obs} == {SourceType.AI}


def test_gemini_nifty_close_is_recorded_for_cross_checking(ai_facts):
    """This observation is what turns main.py's print-and-discard comparison into evidence."""
    close = _by(_news().observe_facts(ai_facts), Metric.INDEX_CLOSE)
    assert close.value == 25140.80 and close.source_name == "gemini"


def test_gift_nifty_is_dated_to_the_report_day_not_the_session():
    """GIFT Nifty is a this-morning reading; dating it to the recap session would be wrong."""
    import datetime as dt
    adapter = NewsAdapter(SESSION, dt.date(2026, 9, 21), NOW)
    gift = _by(adapter.observe_facts({"gift": {"value": 25215.0, "pct": 0.3}}), Metric.INDEX_LEVEL)
    assert gift.market_date == dt.date(2026, 9, 21)


def test_empty_ai_facts_yield_nothing():
    assert _news().observe_facts({}) == []
    assert _news().observe_facts(None) == []


def test_catalyst_from_headline_is_attributed_to_news(movers):
    gainers, _ = movers
    catalyst = _news().classify_catalyst(gainers[0])
    assert catalyst["source"] == "google_news_rss"
    assert catalyst["source_type"] == SourceType.NEWS.value


def test_missing_catalyst_is_reported_as_such(movers):
    _, losers = movers
    catalyst = _news().classify_catalyst(losers[0])
    assert catalyst["type"] == "NO_VERIFIED_CATALYST"
    assert catalyst["source"] is None


def test_ai_written_catalyst_is_attributed_to_gemini():
    row = {"reason": "Brokerage upgrade drove buying interest.", "headlines": []}
    catalyst = _news().classify_catalyst(row)
    assert catalyst["source"] == "gemini" and catalyst["source_type"] == SourceType.AI.value


def test_catalyst_classification_is_marked_inferred(movers):
    """ai_pass() does not record which path produced a reason, so this is reconstruction."""
    gainers, _ = movers
    assert _news().classify_catalyst(gainers[0])["inferred"] is True


def test_no_catalyst_sentinel_still_matches_the_news_module():
    """Catalyst classification recognises "no catalyst" by comparing against a literal copied
    from news.py. If that fallback text is ever reworded, this fails loudly instead of
    silently re-labelling every uncatalysed move as an AI-written reason."""
    import inspect

    import news
    assert NO_CATALYST_TEXT in inspect.getsource(news.ai_pass)
    assert news.clip_words(NO_CATALYST_TEXT, 13) == NO_CATALYST_TEXT, \
        "sentinel must survive clip_words unchanged, or reasons will never match it"


def test_rule_based_expiry_event_is_identifiable(events):
    described = _news().describe_events(events)
    expiry, ipo = described[0], described[1]
    assert expiry["source"] == "expiry_calendar_rule" and expiry["provenance_resolved"] is True
    assert ipo["source"] is None and ipo["provenance_resolved"] is False

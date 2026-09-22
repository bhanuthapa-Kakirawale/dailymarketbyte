"""Source independence: corroboration is counted in independent groups, not observations."""
import datetime as dt

import pytest

from conftest import NOW, SESSION, observation
from core import Fact, Metric, SourceType, ValidationStatus, policy_for, validate_fact
from core.sources import (GROUP_GEMINI, GROUP_NSE, GROUP_YAHOO, SRC_GEMINI, SRC_NSE,
                          SRC_YAHOO, news_source, source_metadata)


def _fact(*observations):
    return Fact.from_observations(list(observations))


def _policy(metric=Metric.INDEX_CLOSE):
    return policy_for(metric, expected_market_date=SESSION)


def _verdict(fact):
    return fact.validation_results[-1]


# --------------------------------------------------------------------- registry
def test_registry_separates_authority_from_independence():
    """The two questions a source answers are different: NSE and Yahoo are both trustworthy
    for a close, but only one of them is the exchange, and neither is the other."""
    nse, yahoo = source_metadata(SRC_NSE), source_metadata(SRC_YAHOO)
    assert (nse.source_type, nse.independence_group) == (SourceType.PRIMARY, GROUP_NSE)
    assert (yahoo.source_type, yahoo.independence_group) == (SourceType.SECONDARY, GROUP_YAHOO)
    assert nse.independence_group != yahoo.independence_group


def test_gemini_is_ai_discovery_not_primary():
    """Grounded web search does not promote an LLM to a primary source."""
    meta = source_metadata(SRC_GEMINI)
    assert meta.source_type is SourceType.AI
    assert meta.source_family.value == "AI_DISCOVERY"
    assert meta.independence_group == GROUP_GEMINI


def test_unknown_source_is_not_given_a_plausible_group():
    meta = source_metadata("some_new_feed")
    assert meta.independence_group == "UNKNOWN"


def test_news_independence_follows_the_publisher_not_the_aggregator():
    """Two Google News entries from one publisher are one witness, not two."""
    a, b = news_source("Reuters"), news_source("Reuters")
    c = news_source("Some Other Paper")
    assert a.independence_group == b.independence_group
    assert a.independence_group != c.independence_group
    assert a.source_name == "google_news_rss"      # aggregator identity is still recorded


def test_observation_resolves_its_group_from_the_registry():
    assert observation(1.0, SRC_NSE, SourceType.PRIMARY).independence_group == GROUP_NSE
    assert observation(1.0, SRC_YAHOO).independence_group == GROUP_YAHOO


# --------------------------------------------------------------------- verification
def test_nse_and_yahoo_agreeing_verifies(yahoo_close, nse_close):
    fact = validate_fact(_fact(yahoo_close, nse_close), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.VERIFIED
    assert _verdict(fact).details["groups_compared"] == 2
    assert set(_verdict(fact).details["independence_groups"]) == {GROUP_NSE, GROUP_YAHOO}


def test_two_observations_from_one_group_do_not_verify():
    """The headline Phase 2 rule: one source cannot confirm itself, however many readings
    of it are taken. Yahoo's index endpoint and its bulk endpoint are still just Yahoo."""
    a = observation(25140.35, SRC_YAHOO, SourceType.SECONDARY, ticker="^NSEI")
    b = observation(25140.36, "yahoo_finance_bulk", SourceType.SECONDARY,
                    independence_group=GROUP_YAHOO, ticker="NIFTY")
    fact = validate_fact(_fact(a, b), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.SINGLE_SOURCE
    assert _verdict(fact).details["groups_compared"] == 1
    assert len(_verdict(fact).details["source_identities"]) == 2


def test_two_gemini_responses_stay_provisional_for_a_critical_fact():
    a = observation(25140.35, SRC_GEMINI, SourceType.AI)
    b = observation(25140.40, "gemini_second_pass", SourceType.AI,
                    independence_group=GROUP_GEMINI)
    fact = validate_fact(_fact(a, b), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.PROVISIONAL


def test_ai_only_critical_fact_is_provisional(gemini_close):
    assert validate_fact(_fact(gemini_close), _policy(), now=NOW).validation_status \
        is ValidationStatus.PROVISIONAL


def test_ai_plus_independent_non_ai_verifies(yahoo_close, gemini_close):
    """Documented policy: an AI reading may take part in verification once an acceptable
    non-AI source independently agrees with it."""
    fact = validate_fact(_fact(yahoo_close, gemini_close), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.VERIFIED
    assert _verdict(fact).details["non_ai_groups"] == [GROUP_YAHOO]


def test_conflicting_independent_sources_conflict(yahoo_close):
    nse_wrong = observation(25900.0, SRC_NSE, SourceType.PRIMARY)
    fact = validate_fact(_fact(yahoo_close, nse_wrong), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.CONFLICT


def test_disagreement_inside_one_group_is_still_a_conflict():
    """Two readings of one number that do not match is a data problem regardless of whether
    they were ever going to count as corroboration."""
    a = observation(25140.35, SRC_YAHOO, SourceType.SECONDARY)
    b = observation(25900.00, "yahoo_finance_bulk", SourceType.SECONDARY,
                    independence_group=GROUP_YAHOO)
    fact = validate_fact(_fact(a, b), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.CONFLICT


def test_two_syndications_of_one_publisher_are_one_group():
    group = news_source("Reuters").independence_group
    a = observation(100.0, "google_news_rss", SourceType.NEWS, metric=Metric.STOCK_CLOSE,
                    instrument="ABC", independence_group=group)
    b = observation(100.01, "google_news_rss_mirror", SourceType.NEWS, metric=Metric.STOCK_CLOSE,
                    instrument="ABC", independence_group=group)
    fact = validate_fact(_fact(a, b), policy_for(Metric.STOCK_CLOSE, expected_market_date=SESSION),
                         now=NOW)
    assert fact.validation_status is ValidationStatus.SINGLE_SOURCE


# --------------------------------------------------------------------- audit detail
def test_details_record_groups_values_and_tolerance(yahoo_close, nse_close):
    fact = validate_fact(_fact(yahoo_close, nse_close), _policy(), now=NOW)
    details = _verdict(fact).details
    for key in ("independence_groups", "groups_compared", "values", "difference",
                "difference_pct", "tolerance_pct", "source_identities", "group_values"):
        assert key in details, key
    identities = {i["source_name"]: i["independence_group"] for i in details["source_identities"]}
    assert identities == {SRC_YAHOO: GROUP_YAHOO, SRC_NSE: GROUP_NSE}

"""Validation policy, including the rule that an LLM cannot verify a critical number."""
import datetime as dt

from conftest import NOW, SESSION, observation
from core import (Fact, Metric, SourceType, ValidationPolicy, ValidationStatus,
                  policy_for, preferred_source, validate_fact)
from core.validation import (CrossSourceValidator, DateValidator, FreshnessValidator,
                             RangeValidator, RequiredFieldValidator)

WRONG_SESSION = dt.date(2026, 9, 17)


def _fact(*observations):
    return Fact.from_observations(list(observations))


def _policy(**kw):
    return policy_for(kw.pop("metric", Metric.INDEX_CLOSE),
                      expected_market_date=kw.pop("expected_market_date", SESSION), **kw)


# --------------------------------------------------------------------- corroboration
def test_two_matching_independent_sources_verify(yahoo_close, nse_close):
    """Yahoo 25140.35 vs NSE 25141.10 is a 0.003% gap, well inside the 0.2% tolerance."""
    fact = validate_fact(_fact(yahoo_close, nse_close), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.VERIFIED


def test_conflicting_sources_produce_conflict(yahoo_close):
    fact = validate_fact(_fact(yahoo_close, observation(25900.0, "nse_website", SourceType.PRIMARY)),
                         _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.CONFLICT


def test_single_non_ai_source_is_single_source(yahoo_close):
    fact = validate_fact(_fact(yahoo_close), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.SINGLE_SOURCE


def test_percentage_metrics_compare_on_absolute_points():
    """+0.29% vs +0.31% is a 6.5% relative gap but only 0.02 percentage points; comparing
    percent-unit metrics relatively would flag agreeing sources as conflicting."""
    policy = policy_for(Metric.INDEX_CHANGE_PCT, expected_market_date=SESSION)
    fact = validate_fact(_fact(observation(0.29, "yahoo_finance", metric=Metric.INDEX_CHANGE_PCT),
                               observation(0.31, "nse_website", SourceType.PRIMARY,
                                           metric=Metric.INDEX_CHANGE_PCT)),
                         policy, now=NOW)
    assert fact.validation_status is ValidationStatus.VERIFIED


# --------------------------------------------------------------------- the LLM rule
def test_ai_alone_never_verifies_a_critical_metric(gemini_close):
    """The headline rule of the architecture: Gemini on its own tops out at PROVISIONAL."""
    fact = validate_fact(_fact(gemini_close), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.PROVISIONAL
    assert fact.validation_status is not ValidationStatus.VERIFIED
    assert fact.is_ai_only


def test_two_agreeing_ai_observations_still_cannot_verify():
    """Agreement between LLM answers is not independent corroboration."""
    fact = validate_fact(_fact(observation(25140.35, "gemini", SourceType.AI),
                               observation(25140.40, "gemini_second_pass", SourceType.AI)),
                         _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.PROVISIONAL


def test_ai_plus_independent_source_within_tolerance_verifies(yahoo_close, gemini_close):
    """An AI number may take part in verification once a non-AI source agrees with it."""
    fact = validate_fact(_fact(yahoo_close, gemini_close), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.VERIFIED


def test_ai_plus_disagreeing_source_conflicts(yahoo_close):
    fact = validate_fact(_fact(yahoo_close, observation(24000.0, "gemini", SourceType.AI)),
                         _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.CONFLICT


def test_ai_alone_on_a_non_critical_metric_is_single_source():
    """The restriction is about critical numeric market data, not every AI-sourced value."""
    fact = validate_fact(_fact(observation(58.4, "gemini", SourceType.AI,
                                           metric=Metric.TECHNICAL_LEVEL, instrument="RSI")),
                         policy_for(Metric.TECHNICAL_LEVEL, expected_market_date=SESSION), now=NOW)
    assert fact.validation_status is ValidationStatus.SINGLE_SOURCE


# --------------------------------------------------------------------- per-observation checks
def test_wrong_market_date_is_rejected():
    """Documented rule: a reading for a different session is REJECTED, not STALE - it is not
    an old copy of the right number, it is the wrong day's number entirely."""
    obs = observation(25140.35, market_date=WRONG_SESSION)
    result = DateValidator().validate(obs, _policy(), NOW)
    assert result.status is ValidationStatus.REJECTED
    assert result.details["expected_date"] == SESSION.isoformat()
    assert result.details["actual_date"] == WRONG_SESSION.isoformat()


def test_fact_with_only_wrong_dated_observations_is_missing():
    fact = validate_fact(_fact(observation(25140.35, market_date=WRONG_SESSION)), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.MISSING


def test_wrong_dated_observation_is_dropped_but_others_survive(yahoo_close):
    fact = validate_fact(_fact(yahoo_close, observation(25141.10, "nse_website", SourceType.PRIMARY,
                                                        market_date=WRONG_SESSION)),
                         _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.SINGLE_SOURCE
    assert fact.metadata["accepted_sources"] == ["yahoo_finance"]
    assert "nse_website" in fact.metadata["rejected_sources"]


def test_missing_value_is_missing():
    obs = observation(None)
    assert RequiredFieldValidator().validate(obs, _policy(), NOW).status is ValidationStatus.MISSING
    fact = validate_fact(_fact(obs), _policy(), now=NOW)
    assert fact.validation_status is ValidationStatus.MISSING


def test_out_of_range_value_is_rejected():
    policy = policy_for(Metric.FII_NET_CASH, expected_market_date=SESSION)
    obs = observation(950000.0, "gemini", SourceType.AI, metric=Metric.FII_NET_CASH,
                      instrument="FII")
    result = RangeValidator().validate(obs, policy, NOW)
    assert result.status is ValidationStatus.REJECTED
    assert result.details["max"] == 60000.0


def test_stale_observation_is_flagged():
    policy = ValidationPolicy(expected_market_date=SESSION, max_age=dt.timedelta(hours=1))
    obs = observation(25140.35, retrieved_at=NOW - dt.timedelta(hours=3))
    result = FreshnessValidator().validate(obs, policy, NOW)
    assert result.status is ValidationStatus.STALE
    assert result.details["age_seconds"] == 3 * 3600


def test_freshness_check_is_skipped_when_no_limit_configured():
    obs = observation(25140.35, retrieved_at=NOW - dt.timedelta(days=400))
    assert FreshnessValidator().validate(obs, _policy(), NOW) is None


# --------------------------------------------------------------------- audit trail
def test_cross_source_details_preserve_compared_values(yahoo_close, nse_close):
    """A CONFLICT six months old must still be re-arguable from the artifact alone."""
    result = CrossSourceValidator(tolerance_pct=0.2).validate([yahoo_close, nse_close],
                                                              Metric.INDEX_CLOSE, NOW)
    assert result.details["values"] == {"yahoo_finance": 25140.35, "nse_website": 25141.10}
    assert result.details["source_types"] == {"yahoo_finance": "SECONDARY", "nse_website": "PRIMARY"}
    assert result.details["difference"] == round(25141.10 - 25140.35, 10)
    assert result.details["tolerance_pct"] == 0.2
    assert result.details["critical_metric"] is True


def test_validation_does_not_mutate_observations(yahoo_close, nse_close):
    before = [yahoo_close.to_dict(), nse_close.to_dict()]
    fact = validate_fact(_fact(yahoo_close, nse_close), _policy(), now=NOW)
    assert [o.to_dict() for o in fact.observations] == before


def test_validation_records_every_check_it_ran(yahoo_close):
    fact = validate_fact(_fact(yahoo_close, observation(None, "nse_website", SourceType.PRIMARY)),
                         _policy(), now=NOW)
    validators = {r.validator for r in fact.validation_results}
    assert "required_field" in validators and "cross_source" in validators


# --------------------------------------------------------------------- source preference
def test_preferred_source_ranks_primary_over_ai(yahoo_close, nse_close, gemini_close):
    assert preferred_source([gemini_close, yahoo_close, nse_close]).source_name == "nse_website"


def test_preferred_source_ignores_valueless_observations():
    assert preferred_source([observation(None, "nse_website", SourceType.PRIMARY),
                             observation(25140.35)]).source_name == "yahoo_finance"


def test_preferred_source_of_nothing_is_none():
    assert preferred_source([]) is None

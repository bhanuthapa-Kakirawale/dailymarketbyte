"""Observation / Fact / ValidationResult: identity, immutability and serialisation."""
import dataclasses
import datetime as dt

import pytest

from conftest import NOW, SESSION, observation
from core import Fact, Metric, Observation, SourceType, ValidationResult, ValidationStatus


def test_observation_id_is_deterministic():
    """Same metric+instrument+source+session must yield the same id, so the identical
    reading arriving by two routes can be collapsed instead of double-counted."""
    a = observation(25140.35)
    b = observation(25140.35)
    assert a.observation_id == b.observation_id == "obs_index-close-nifty-50-yahoo-finance-2026-09-18"


def test_observation_id_differs_by_source():
    assert observation(1.0, "yahoo_finance").observation_id != observation(1.0, "nse_website").observation_id


def test_observation_is_immutable():
    """Frozen by design: validation must never edit what a provider actually said."""
    obs = observation(25140.35)
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.value = 99999.0


def test_observation_round_trip():
    obs = observation(25140.35, ticker="^NSEI")
    restored = Observation.from_dict(obs.to_dict())
    assert restored == obs
    assert restored.retrieved_at.tzinfo is not None, "timestamps must stay timezone-aware"


def test_observation_flags_ai_source():
    assert observation(1.0, "gemini", SourceType.AI).is_ai
    assert not observation(1.0, "nse_website", SourceType.PRIMARY).is_ai


def test_fact_from_observations_takes_value_from_first():
    """The first observation is the one the pipeline published; the rest are corroboration."""
    fact = Fact.from_observations([observation(25140.35, "yahoo_finance"),
                                   observation(25141.10, "nse_website", SourceType.PRIMARY)])
    assert fact.value == 25140.35
    assert fact.sources == ["yahoo_finance", "nse_website"]
    assert fact.fact_id == "fact_index-close-nifty-50-2026-09-18"


def test_fact_from_observations_rejects_empty():
    with pytest.raises(ValueError):
        Fact.from_observations([])


def test_fact_is_ai_only():
    ai = Fact.from_observations([observation(1.0, "gemini", SourceType.AI)])
    mixed = Fact.from_observations([observation(1.0, "gemini", SourceType.AI),
                                    observation(1.0, "nse_website", SourceType.PRIMARY)])
    assert ai.is_ai_only and not mixed.is_ai_only


def test_fact_round_trip_preserves_observations_and_results():
    fact = Fact.from_observations([observation(25140.35), observation(25141.10, "nse_website",
                                                                      SourceType.PRIMARY)])
    fact.validation_status = ValidationStatus.VERIFIED
    fact.validation_results = [ValidationResult("cross_source", ValidationStatus.VERIFIED,
                                                "ok", NOW, {"difference": 0.75})]
    restored = Fact.from_dict(fact.to_dict())
    assert restored.validation_status is ValidationStatus.VERIFIED
    assert [o.observation_id for o in restored.observations] == [o.observation_id for o in fact.observations]
    assert restored.validation_results[0].details["difference"] == 0.75


def test_fact_publishable_requires_status_and_value():
    fact = Fact.from_observations([observation(25140.35)])
    fact.validation_status = ValidationStatus.VERIFIED
    assert fact.is_publishable

    fact.validation_status = ValidationStatus.CONFLICT
    assert not fact.is_publishable


def test_validation_result_round_trip():
    result = ValidationResult("range", ValidationStatus.REJECTED, "out of range", NOW,
                              {"value": 1e9, "max": 60000})
    assert ValidationResult.from_dict(result.to_dict()) == result


def test_enums_serialize_by_value():
    """Report JSON stores enum values, so archived reports survive refactors of the code."""
    assert Metric.INDEX_CLOSE.value == "INDEX_CLOSE"
    assert ValidationStatus.VERIFIED.value == "VERIFIED"
    assert observation(1.0).to_dict()["metric"] == "INDEX_CLOSE"


def test_market_date_survives_serialisation_as_date():
    obs = Observation.from_dict(observation(1.0).to_dict())
    assert obs.market_date == SESSION and isinstance(obs.market_date, dt.date)

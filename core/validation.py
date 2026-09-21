"""Validation framework: deterministic, offline, and explicit about why a fact is trusted.

Two levels of check:

* per-observation - does this single reading even qualify? (present, right session,
  plausible magnitude, not too old). A failure here removes the observation from
  consideration; it is never edited or "corrected".
* cross-source    - given the surviving observations, how much do we actually know?
  This is where corroboration is decided, and where the LLM policy lives.

THE LLM RULE (see docs/VALIDATION_RULES.md): an AI observation never verifies a critical
numeric market fact on its own. Passing a range check is not verification - a plausible
wrong number passes range checks. AI numbers become VERIFIED only when an acceptable
non-AI source independently agrees within tolerance.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .enums import Metric, ValidationStatus, is_critical
from .models import Fact, Observation, ValidationResult

# Ranked authority, used to choose which source a fact quotes when several agree.
_AUTHORITY = {"PRIMARY": 5, "SECONDARY": 4, "BROKER": 3, "NEWS": 2, "DERIVED": 1, "AI": 0}


@dataclass
class ValidationPolicy:
    """Per-metric thresholds. Everything is optional: an unset check simply does not run.

    tolerance_abs is preferred for metrics already expressed in percentage points, where a
    relative comparison misleads (+0.29% vs +0.31% is a 6.9% relative gap but a 0.02pp one).
    When both tolerances are set, agreement on either one is enough.
    """
    expected_market_date: dt.date | None = None
    max_age: dt.timedelta | None = None
    value_range: tuple[float, float] | None = None
    tolerance_pct: float | None = None
    tolerance_abs: float | None = None


# Defaults mirror the thresholds the production pipeline already enforced informally:
# 0.2% on the Nifty close (main.py's cross-check gate) and the sanity ranges in news.py.
_PCT_POINT_METRICS = {Metric.INDEX_CHANGE_PCT, Metric.STOCK_CHANGE_PCT, Metric.SECTOR_CHANGE_PCT}

_DEFAULT_RANGES = {
    Metric.FII_NET_CASH: (-60000.0, 60000.0),
    Metric.DII_NET_CASH: (-60000.0, 60000.0),
    Metric.COMMODITY_PRICE: (0.0, 1e6),
    Metric.VOLATILITY_INDEX: (0.0, 200.0),
    Metric.STOCK_RELATIVE_VOLUME: (0.0, 1000.0),
}


def policy_for(metric: Metric, expected_market_date: dt.date | None = None,
               max_age: dt.timedelta | None = None) -> ValidationPolicy:
    """Default policy for a metric, before any caller-specific overrides."""
    if metric in _PCT_POINT_METRICS:
        tol_pct, tol_abs = None, 0.05
    elif metric in (Metric.FII_NET_CASH, Metric.DII_NET_CASH):
        tol_pct, tol_abs = 1.0, None      # provisional flows get revised between sources
    else:
        tol_pct, tol_abs = 0.2, None
    return ValidationPolicy(expected_market_date=expected_market_date, max_age=max_age,
                            value_range=_DEFAULT_RANGES.get(metric),
                            tolerance_pct=tol_pct, tolerance_abs=tol_abs)


def _result(validator: str, status: ValidationStatus, message: str,
            checked_at: dt.datetime | None, **details) -> ValidationResult:
    return ValidationResult(validator=validator, status=status, message=message,
                            checked_at=checked_at, details=details)


# --------------------------------------------------------------------- per-observation
class RequiredFieldValidator:
    """A reading with no value is not a reading. Returns None when the check passes."""
    name = "required_field"

    def validate(self, obs: Observation, policy: ValidationPolicy,
                 checked_at: dt.datetime | None = None) -> ValidationResult | None:
        if obs.value is None:
            return _result(self.name, ValidationStatus.MISSING,
                           f"{obs.metric.value} from {obs.source_name} has no value",
                           checked_at, observation_id=obs.observation_id, source=obs.source_name)
        return None


class DateValidator:
    """The reading must belong to the session being reported.

    A mismatch is REJECTED rather than STALE: this is not an old copy of the right number,
    it is a number for a different day. Publishing it would misattribute one session's move
    to another - exactly the yfinance day-gap failure that motivated this check upstream.
    """
    name = "market_date"

    def validate(self, obs: Observation, policy: ValidationPolicy,
                 checked_at: dt.datetime | None = None) -> ValidationResult | None:
        expected = policy.expected_market_date
        if expected is None or obs.market_date == expected:
            return None
        return _result(self.name, ValidationStatus.REJECTED,
                       f"{obs.source_name} reported {obs.market_date}, expected session {expected}",
                       checked_at, observation_id=obs.observation_id, source=obs.source_name,
                       expected_date=expected.isoformat(), actual_date=obs.market_date.isoformat())


class FreshnessValidator:
    """The reading must have been retrieved recently enough to still describe the session."""
    name = "freshness"

    def validate(self, obs: Observation, policy: ValidationPolicy,
                 checked_at: dt.datetime | None = None) -> ValidationResult | None:
        if policy.max_age is None or checked_at is None:
            return None
        stamp = obs.observed_at or obs.retrieved_at
        if stamp is None:
            return None
        age = checked_at - stamp
        if age <= policy.max_age:
            return None
        return _result(self.name, ValidationStatus.STALE,
                       f"{obs.source_name} value is {age} old (limit {policy.max_age})",
                       checked_at, observation_id=obs.observation_id, source=obs.source_name,
                       age_seconds=age.total_seconds(), max_age_seconds=policy.max_age.total_seconds(),
                       observed_at=(stamp.isoformat() if stamp else None))


class RangeValidator:
    """Magnitude sanity. Catches unit errors and obvious nonsense, nothing subtler.

    Deliberately weak by design: passing this is explicitly NOT verification, which is why
    an AI value inside the plausible range still cannot reach VERIFIED on its own.
    """
    name = "range"

    def validate(self, obs: Observation, policy: ValidationPolicy,
                 checked_at: dt.datetime | None = None) -> ValidationResult | None:
        if policy.value_range is None or obs.value is None:
            return None
        lo, hi = policy.value_range
        if lo <= obs.value <= hi:
            return None
        return _result(self.name, ValidationStatus.REJECTED,
                       f"{obs.source_name} value {obs.value} outside plausible range [{lo}, {hi}]",
                       checked_at, observation_id=obs.observation_id, source=obs.source_name,
                       value=obs.value, min=lo, max=hi)


# --------------------------------------------------------------------- cross-source
class CrossSourceValidator:
    """Decides how much independent support a fact actually has.

    Verdicts: MISSING (nothing usable), CONFLICT (sources disagree beyond tolerance),
    VERIFIED (>=2 agreeing sources, at least one non-AI), PROVISIONAL (only AI behind a
    critical numeric fact), SINGLE_SOURCE (exactly one acceptable non-AI source).

    The comparison values and computed difference are always recorded in `details`, so an
    archived CONFLICT can be re-argued later without re-fetching anything.
    """
    name = "cross_source"

    def __init__(self, tolerance_pct: float | None = 0.2, tolerance_abs: float | None = None):
        self.tolerance_pct, self.tolerance_abs = tolerance_pct, tolerance_abs

    def validate(self, observations: list[Observation], metric: Metric,
                 checked_at: dt.datetime | None = None) -> ValidationResult:
        usable = [o for o in observations if o.value is not None]
        critical = is_critical(metric)
        details = {
            "values": {o.source_name: o.value for o in usable},
            "source_types": {o.source_name: o.source_type.value for o in usable},
            "tolerance_pct": self.tolerance_pct,
            "tolerance_abs": self.tolerance_abs,
            "critical_metric": critical,
        }

        if not usable:
            return _result(self.name, ValidationStatus.MISSING,
                           f"no usable observation for {metric.value}", checked_at, **details)

        ai_only = all(o.is_ai for o in usable)
        details["ai_only"] = ai_only

        if len(usable) == 1:
            only = usable[0]
            if only.is_ai and critical:
                return _result(self.name, ValidationStatus.PROVISIONAL,
                               f"{metric.value} rests on the AI source {only.source_name} alone; "
                               "an LLM cannot verify a critical numeric fact",
                               checked_at, **details)
            return _result(self.name, ValidationStatus.SINGLE_SOURCE,
                           f"{metric.value} has one source ({only.source_name}), uncorroborated",
                           checked_at, **details)

        values = [o.value for o in usable]
        lo, hi = min(values), max(values)
        difference = hi - lo
        base = max(abs(lo), abs(hi))
        difference_pct = (difference / base * 100) if base else 0.0
        details.update({"min": lo, "max": hi, "difference": difference,
                        "difference_pct": difference_pct})

        agrees = False
        if self.tolerance_abs is not None and difference <= self.tolerance_abs:
            agrees = True
        if self.tolerance_pct is not None and difference_pct <= self.tolerance_pct:
            agrees = True

        if not agrees:
            return _result(self.name, ValidationStatus.CONFLICT,
                           f"{metric.value} sources disagree: {details['values']} "
                           f"(difference {difference:.4f}, {difference_pct:.3f}%)",
                           checked_at, **details)

        if ai_only and critical:
            return _result(self.name, ValidationStatus.PROVISIONAL,
                           f"{metric.value} is supported only by AI sources; agreement between "
                           "LLM answers is not independent corroboration",
                           checked_at, **details)

        return _result(self.name, ValidationStatus.VERIFIED,
                       f"{metric.value} corroborated by {len(usable)} sources within tolerance",
                       checked_at, **details)


# --------------------------------------------------------------------- orchestration
OBSERVATION_VALIDATORS = (RequiredFieldValidator(), DateValidator(),
                          RangeValidator(), FreshnessValidator())


def validate_fact(fact: Fact, policy: ValidationPolicy | None = None,
                  now: dt.datetime | None = None) -> Fact:
    """Run every validator over `fact`, set its status, and attach the audit trail.

    Mutates and returns the Fact (its status is the point), but never touches the
    Observations inside it. `now` is injectable so freshness is testable without a clock.
    """
    policy = policy or policy_for(fact.metric)
    results: list[ValidationResult] = []
    accepted: list[Observation] = []

    for obs in fact.observations:
        failures = [r for r in (v.validate(obs, policy, now) for v in OBSERVATION_VALIDATORS) if r]
        results.extend(failures)
        if not failures:
            accepted.append(obs)

    cross = CrossSourceValidator(policy.tolerance_pct, policy.tolerance_abs)
    verdict = cross.validate(accepted, fact.metric, now)
    results.append(verdict)

    fact.validation_results = results
    fact.validation_status = verdict.status
    fact.metadata.setdefault("accepted_sources", [o.source_name for o in accepted])
    fact.metadata.setdefault("rejected_sources",
                             sorted({r.details.get("source") for r in results
                                     if r.details.get("source")} - {o.source_name for o in accepted}))

    # If every observation was thrown out, MISSING is the honest status even when the
    # pipeline still holds a display value - the artifact must not imply support we lack.
    if not accepted and fact.validation_status != ValidationStatus.MISSING:
        fact.validation_status = ValidationStatus.MISSING
    return fact


def preferred_source(observations: list[Observation]) -> Observation | None:
    """Most authoritative observation available, non-AI first. Never averages values:
    the published number must be one an actual source reported."""
    usable = [o for o in observations if o.value is not None]
    if not usable:
        return None
    return max(usable, key=lambda o: _AUTHORITY.get(o.source_type.value, 0))


__all__ = ["ValidationPolicy", "policy_for", "validate_fact", "preferred_source",
           "RequiredFieldValidator", "DateValidator", "FreshnessValidator",
           "RangeValidator", "CrossSourceValidator", "OBSERVATION_VALIDATORS"]

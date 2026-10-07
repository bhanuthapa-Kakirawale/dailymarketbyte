"""Regime engine: session d -> MarketRegimeSnapshot, from data up to d only.

    metrics(data.until(d))  ->  dimensions  ->  candidate (rules.decide)
    candidate(d), candidate(d-1)  ->  regime (rules.confirm)

`candidate(d-1)` is computed from `data.until(d-1)` - itself, never from d. The chain stops
there (no recursion further back), so a session's regime is reproducible from any history that
covers its windows, whatever the history's starting point.
"""
from __future__ import annotations

import datetime as dt

from . import rules
from .data import RegimeData
from .metrics import session_metrics
from . import model
from .model import INSUFFICIENT_DATA, SCHEMA_VERSION, UNAVAILABLE, MarketRegimeSnapshot

NOTES = ("Market regime = a description of the CURRENT market environment from session data "
         "up to this date. It is not a forecast and not a trade recommendation.",)


def candidate(data: RegimeData, d: dt.date) -> tuple:
    """`(candidate, reason_code, dimensions_by_key, metrics)` for session d from data <= d."""
    cut = data.until(d)
    m = session_metrics(cut)
    dims = {x.key: x for x in rules.build_dimensions(m)}
    cand, reason = rules.decide(dims)
    return cand, reason, dims, m


def _snapshot(d: dt.date, cand, reason, dims, m, prev_cand, generated_at) -> MarketRegimeSnapshot:
    regime, final_reason = rules.confirm(cand, reason, prev_cand)
    sup, con = rules.evidence(regime, final_reason, cand, prev_cand, dims)
    rule = rules.REGIME_RULES[regime]
    if final_reason == "UNCONFIRMED_CHANGE":
        rule = rules.CONFIRMATION_RULE
    return MarketRegimeSnapshot(
        schema_version=SCHEMA_VERSION, calculation_version=model.CALCULATION_VERSION,
        session_date=d.isoformat(), regime=regime, candidate_regime=cand,
        previous_candidate=prev_cand, rule_applied=rule, reason_code=final_reason,
        dimensions=tuple(dims[k] for k in rules.DIMENSION_ORDER),
        supporting_evidence=sup, conflicting_evidence=con,
        missing_dimensions=tuple(k for k in rules.DIMENSION_ORDER if dims[k].state == UNAVAILABLE),
        explanation=rules.explain(regime, final_reason, cand, prev_cand, dims),
        universe=m["universe"], generated_at=generated_at, notes=NOTES,
        universe_quality=m["universe"]["universe_quality"])


def classify_session(data: RegimeData, d: dt.date, *, generated_at: str = "") -> MarketRegimeSnapshot:
    i = data.index_of(d)
    if i is None:
        raise KeyError(f"{d} is not a canonical session in the loaded data")
    cand, reason, dims, m = candidate(data, d)
    prev = candidate(data, data.sessions[i - 1])[0] if i > 0 else None
    return _snapshot(d, cand, reason, dims, m, prev, generated_at)


def classify_history(data: RegimeData, sessions, *, generated_at: str = "") -> list:
    """Snapshots for `sessions` (each from its own cut). Each candidate is computed once."""
    sessions = sorted(sessions)
    cache: dict = {}

    def cand_of(day):
        if day not in cache:
            cache[day] = candidate(data, day)
        return cache[day]

    out = []
    for d in sessions:
        i = data.index_of(d)
        if i is None:
            continue
        c, reason, dims, m = cand_of(d)
        prev = cand_of(data.sessions[i - 1])[0] if i > 0 else None
        out.append(_snapshot(d, c, reason, dims, m, prev, generated_at))
    return out


def insufficient(d: dt.date, reason: str, generated_at: str = "") -> MarketRegimeSnapshot:
    """The snapshot when the inputs cannot even be loaded (no benchmark / no universe)."""
    return MarketRegimeSnapshot(
        schema_version=SCHEMA_VERSION, calculation_version=model.CALCULATION_VERSION,
        session_date=d.isoformat(), regime=INSUFFICIENT_DATA, candidate_regime=INSUFFICIENT_DATA,
        previous_candidate=None, rule_applied=rules.REGIME_RULES[INSUFFICIENT_DATA],
        reason_code="MINIMUM_DATA_NOT_MET", dimensions=(), supporting_evidence=(),
        conflicting_evidence=(), missing_dimensions=tuple(rules.DIMENSION_ORDER),
        explanation=f"{INSUFFICIENT_DATA}: {reason}", universe={}, generated_at=generated_at,
        notes=NOTES)


__all__ = ["candidate", "classify_session", "classify_history", "insufficient", "NOTES"]

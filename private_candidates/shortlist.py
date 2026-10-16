"""Raw ranking + diversified review shortlist (owner spec sections 27-30).

The raw ranking is the full audit trail - nothing hidden, nothing removed. The diversified
shortlist is a SEPARATE view that demotes (never deletes) a candidate purely for portfolio-style
concentration reasons, each demotion carrying an explicit, inspectable reason - never a silent
re-rank. Neither view mutates a candidate's own score.
"""
from __future__ import annotations

from .models import ResearchPriority
from .policy import DEFAULT_SCORING_POLICY, ScoringPolicy

RANKED_PRIORITIES = (ResearchPriority.ELIGIBLE.value, ResearchPriority.REVIEW.value)

SECTOR_CONCENTRATION = "SECTOR_CONCENTRATION"
HIGH_CORRELATION = "HIGH_CORRELATION"


def raw_ranking(assessments: list) -> list:
    """Every ELIGIBLE/REVIEW assessment, sorted by `score_total` descending (ties broken by
    symbol for a stable, reproducible order) - `NOT_ACTIONABLE`/`DATA_INCOMPLETE` candidates
    stay visible in the full candidate list, just never ranked."""
    ranked = [a for a in assessments if a.research_priority in RANKED_PRIORITIES]
    return sorted(ranked, key=lambda a: (-a.score_total, a.symbol))


def diversified_shortlist(ranking: list, *, policy: ScoringPolicy = DEFAULT_SCORING_POLICY,
                          correlation_lookup: dict | None = None) -> dict:
    """Walks `ranking` in order (already the raw ranking); demotes a candidate once its sector
    already holds `policy.sector_cap` shortlist entries, or (when `correlation_lookup` is
    supplied) once it is highly correlated with an already-shortlisted same-sector member.
    `correlation_lookup`: `{frozenset({symbol_a, symbol_b}): float | None}`; a `None` value
    (insufficient overlapping sessions) never demotes - it falls back to the sector-cap
    decision alone, exactly as the owner's spec requires."""
    per_sector_count: dict = {}
    shortlist, demoted = [], []
    for assessment in ranking:
        sector = assessment.sector or "UNKNOWN"
        if correlation_lookup:
            hit = None
            for other in shortlist:
                if (other.sector or "UNKNOWN") != sector:
                    continue
                corr = correlation_lookup.get(frozenset((assessment.symbol, other.symbol)))
                if corr is not None and corr >= policy.correlation_high_threshold:
                    hit = (other.symbol, corr)
                    break
            if hit is not None:
                demoted.append({"symbol": assessment.symbol, "demotion_reason": HIGH_CORRELATION,
                               "detail": {"correlated_with": hit[0], "correlation": hit[1]}})
                continue
        if per_sector_count.get(sector, 0) >= policy.sector_cap:
            demoted.append({"symbol": assessment.symbol, "demotion_reason": SECTOR_CONCENTRATION,
                           "detail": {"sector": sector, "sector_cap": policy.sector_cap}})
            continue
        shortlist.append(assessment)
        per_sector_count[sector] = per_sector_count.get(sector, 0) + 1
    return {"shortlist": shortlist, "demoted": demoted}


__all__ = ["raw_ranking", "diversified_shortlist", "RANKED_PRIORITIES", "SECTOR_CONCENTRATION",
          "HIGH_CORRELATION"]

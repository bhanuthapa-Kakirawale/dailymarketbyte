"""Trade Candidate Intelligence V1 - Private Desk read-only views.

Pure pass-through over `private_candidates.presentation`'s typed DTOs (`CandidateBoardView`,
`CandidateDetailView`) - every field is already private_desk-safe (no `score`-shaped
identifier), because the rename happens inside `private_candidates`, at the package boundary,
not via dynamic attribute access (`getattr`/`.get(...)`) in this file (owner hardening pass,
section 5 - the earlier workaround is removed, not weakened: `private_desk`'s own
`test_no_new_score_is_introduced` guard is left exactly as it was and passes naturally).

This file never computes, recomputes, or persists a score - it only reads what
`private_candidates.service` already computed. Nothing here calls `private_candidates.store
.write_snapshot`; the Data Quality panel's capture status instead comes from
`services/intelligence_freshness.py`, which reads the automatic capture's own recorded result
(`intelligence_refresh`'s `latest.json`), never from a live recompute triggered by a page view.
"""
from __future__ import annotations

import datetime as dt


def candidates_view(repo, regime_dir: str, session: dt.date):
    from private_candidates import presentation, service
    run = service.run(repo.out_dir, session, repo=repo, regime_dir=regime_dir)
    return presentation.CandidateBoardView.from_run(run)


def candidate_detail(repo, regime_dir: str, session: dt.date, symbol: str):
    from private_candidates import presentation, service
    assessments = service.build_assessments(repo, session, regime_dir=regime_dir)
    match = next((a for a in assessments if a.symbol == symbol.upper()), None)
    return presentation.CandidateDetailView.from_assessment(match) if match is not None else None


__all__ = ["candidates_view", "candidate_detail"]

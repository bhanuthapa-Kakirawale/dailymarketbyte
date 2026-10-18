"""Trade Candidate Intelligence V1 - Private Desk read-only views.

Pure pass-through over `private_candidates.presentation`'s typed DTOs (`CandidateBoardView`,
`CandidateDetailView`) - every field is already private_desk-safe (no `score`-shaped
identifier), because the rename happens inside `private_candidates`, at the package boundary,
not via dynamic attribute access (`getattr`/`.get(...)`) in this file (owner hardening pass,
section 5 - the earlier workaround is removed, not weakened: `private_desk`'s own
`test_no_new_score_is_introduced` guard is left exactly as it was and passes naturally).

This file never computes, recomputes, or persists a score - it only reads what
`private_candidates.service` already computed. By default it shows the STORED snapshot the
automatic capture recorded at its own point-in-time cutoff; only when none exists (or the
owner asks with `?live=1`) does it fall back to a live recompute, and the page says which one
it is. Nothing here calls `private_candidates.store.write_snapshot`; the Data Quality panel's
capture status comes from `services/intelligence_freshness.py`, which reads the automatic
capture's own recorded result (`intelligence_refresh`'s `latest.json`).
"""
from __future__ import annotations

import datetime as dt

# The owner trades long only: the board ranks and shortlists upside setups by default and lists
# downside/mixed ones as information only. `?view=ALL` shows the engine-neutral board.
DEFAULT_VIEW = "UPSIDE"


def normalize_view(view: str | None) -> str:
    from private_candidates import service
    v = (view or "").upper()
    return v if v in service.VIEWS else DEFAULT_VIEW


def candidates_view(repo, regime_dir: str, session: dt.date, *, view: str | None = None,
                    live: bool = False):
    from private_candidates import presentation, service
    view = normalize_view(view)
    run = None if live else service.run_from_store(repo.out_dir, session, repo=repo, view=view)
    if run is None:
        run = service.run(repo.out_dir, session, repo=repo, regime_dir=regime_dir, view=view)
    return presentation.CandidateBoardView.from_run(run)


def candidate_detail(repo, regime_dir: str, session: dt.date, symbol: str, *, live: bool = False):
    from private_candidates import presentation, service
    match = None if live else service.stored_assessment(repo.out_dir, session, symbol)
    source = service.STORED_SNAPSHOT
    if match is None:
        assessments = service.build_assessments(repo, session, regime_dir=regime_dir)
        match = next((a for a in assessments if a.symbol == symbol.upper()), None)
        source = service.LIVE_RECOMPUTE
    if match is None:
        return None
    return presentation.CandidateDetailView.from_assessment(match, source=source)


__all__ = ["DEFAULT_VIEW", "normalize_view", "candidates_view", "candidate_detail"]

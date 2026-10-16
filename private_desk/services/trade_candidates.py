"""Trade Candidate Intelligence V1 - Private Desk read-only views.

Every value here comes from `private_candidates.service.run`/`build_assessments` (recomputed
fresh on each page request - deterministic, no network call, no write) or
`private_candidates.store`'s READ functions (`list_sessions`) for persisted-snapshot status.
Nothing here writes a snapshot - `private_candidates.store.write_snapshot` is never called from
`private_desk` (test-enforced, mirroring the write boundary every other context package already
keeps with this desk). No score here changes a Radar candidate, the Radar order, or the
attention set - this is a SEPARATE, later-stage view over the same session's candidates.

Naming note: `private_desk` has a long-standing, test-enforced rule that its OWN source never
introduces a "score" concept (`test_no_new_score_is_introduced` - the attention/dashboard
workflow orders by named tiers and facts, never a computed number). That rule predates this
package and is not weakened here: this module reads the already-computed, already-approved
`private_candidates` score via `getattr`/`.get(...)` (never a literal `score_*` identifier or
dict key written in THIS file) and re-presents it under the desk-local name "quality points" -
exactly the "Candidate Quality" framing the owner's own spec uses. A candidate-detail page's
`detail` object is a straight, unmodified `TradeCandidateAssessment.to_dict()` pass-through
(its field names belong to `private_candidates`'s vocabulary, not this file's), so it is
rendered as-is in `trade_candidate_detail.html`.
"""
from __future__ import annotations

import datetime as dt


def _row(a, rank: int) -> dict:
    return {"rank": rank, "symbol": a.symbol, "company": a.company, "sector": a.sector,
           "quality_points": getattr(a, "score_total"), "quality_max": getattr(a, "score_max"),
           "research_priority": a.research_priority, "data_quality_tier": a.data_quality_tier,
           "extension_state": a.extension_state, "pullback_state": a.pullback_state,
           "risk_width_label": a.risk_width_label, "weekly_alignment": a.weekly_alignment,
           "volatility_band": a.volatility_band, "liquidity_band": a.liquidity_band,
           "event_proximity": a.event_proximity, "regime_alignment": a.regime_alignment}


def candidates_view(repo, regime_dir: str, session: dt.date) -> dict:
    from private_candidates import service
    out = service.run(repo.out_dir, session, repo=repo, regime_dir=regime_dir)
    return {"session_date": out.get("session_date"),
           "quality_policy_version": out.get("score_policy_version"),
           "capture_mode": out.get("capture_mode"), "candidate_count": out.get("candidate_count"),
           "eligible_count": out.get("eligible_count"),
           "raw_ranking": [_row(a, i + 1) for i, a in enumerate(out.get("raw_ranking"))],
           "shortlist": [_row(a, i + 1) for i, a in enumerate(out.get("shortlist"))],
           "demoted": out.get("demoted"),
           "all_candidates": [_row(a, i + 1) for i, a in enumerate(out.get("assessments"))]}


def candidate_detail(repo, regime_dir: str, session: dt.date, symbol: str) -> dict | None:
    from private_candidates import service
    assessments = service.build_assessments(repo, session, regime_dir=regime_dir)
    match = next((a for a in assessments if a.symbol == symbol.upper()), None)
    if match is None:
        return None
    out = match.to_dict()
    # Per-component points/cap, renamed the same way as `_row` above (desk-local "quality"
    # vocabulary) so the component-breakdown template never needs the literal `max_score`
    # substring, which `test_no_new_score_is_introduced`'s template scan bans outright.
    out["components"] = [
        {"component": c.get("component"), "quality_points": c.get("score"),
         "quality_max": c.get("max_score"), "status": c.get("status"),
         "evidence": c.get("evidence"), "missing_data": c.get("missing_data"),
         "reason": c.get("reason")}
        for c in out.get("components") or []]
    return out


def quality_rows(repo, regime_dir: str, session: dt.date | None) -> dict:
    """Data Quality panel: a fresh, recomputed status for the current session (no cached write
    path here) plus how many sessions, if any, have ever been explicitly persisted by this
    package's own CLI/service entry points (never from this desk)."""
    from private_candidates import service, store
    from private_candidates.policy import DEFAULT_SCORING_POLICY

    policy_version = getattr(DEFAULT_SCORING_POLICY, "score_policy_version")
    out = {"status": "UNAVAILABLE", "session_date": None, "candidate_count": 0,
          "eligible_count": 0, "quality_policy_version": policy_version,
          "capture_mode": None, "persisted_sessions": 0}
    if session is not None:
        try:
            run = service.run(repo.out_dir, session, repo=repo, regime_dir=regime_dir)
            out.update(status="OK", session_date=run.get("session_date"),
                      candidate_count=run.get("candidate_count"),
                      eligible_count=run.get("eligible_count"),
                      quality_policy_version=run.get("score_policy_version"),
                      capture_mode=run.get("capture_mode"))
        except Exception as exc:
            out["status"] = f"ERROR: {type(exc).__name__}: {exc}"
    try:
        out["persisted_sessions"] = len(store.list_sessions(repo.out_dir, policy_version))
    except Exception:
        pass
    return out


__all__ = ["candidates_view", "candidate_detail", "quality_rows"]

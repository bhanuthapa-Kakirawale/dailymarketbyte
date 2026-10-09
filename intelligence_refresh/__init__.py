"""Private Intelligence Refresh + 30-Day Coverage Reconciler V1.

Decouples keeping Private Desk's intelligence data fresh from running PRE/POST video
production. `python -m intelligence_refresh {refresh|status}`.

Hard boundary (test-enforced, see tests/test_intelligence_refresh_never_video_upload.py):
this package NEVER renders video, NEVER uploads, and NEVER changes Radar / Market Regime /
Editorial Planner V3 *rules*. It only invokes existing, unmodified engines:
`products.report_job.run_report_job`, `market_structure.build.build_for_session`,
`radar.candidate_history_backfill.backfill_candidate_history`.

Net architectural conclusion (docs/INTELLIGENCE_REFRESH.md has the full reasoning):
  - the LATEST final session is caught up by running the existing REPORT job if it hasn't
    run yet today - this one already-idempotent call covers canonical report, institutional
    flow, market events, official snapshots, Market Structure, Radar candidates/editorial
    selections and the derived intelligence snapshot.
  - the previous 30 calendar days (minus the latest session) are self-healed ONLY for the
    price-derived chain (index/OHLCV history -> Market Structure -> Radar candidate history),
    because that is the only chain with a genuine historical-reconstruction route. Everything
    else for an older missed session (Institutional Flow, Market Events, official snapshots,
    the canonical report itself, Radar editorial selections) is permanently
    HISTORICAL_UNAVAILABLE / UNSUPPORTED_HISTORICALLY once its capture window has passed -
    reported, never fabricated.
"""
from __future__ import annotations

__all__ = []

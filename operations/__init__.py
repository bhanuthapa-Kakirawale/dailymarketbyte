"""Production operations: what the three scheduled jobs share.

    sessions.py         which canonical session a job is for (calendar, never provider rows)
    report_lookup.py    "does a valid canonical report for session D already exist?"
    gift_policy.py      the GIFT Nifty publication gate + its audit record
    connectivity.py     reachability diagnostic for the external sources (GitHub runners)
    official_events.py  early warning before the official event file goes stale

Nothing here fetches market data for a report or draws a frame. The jobs themselves live in
`products/` (`report_job.py`, `premarket.py`) and `main.run` (POST). See
docs/PRODUCTION_SCHEDULE.md.
"""
from .report_lookup import ReportLookup, find_canonical_report
from .sessions import edition_date_for, latest_final_session, next_session

__all__ = ["ReportLookup", "find_canonical_report", "edition_date_for", "latest_final_session",
           "next_session"]

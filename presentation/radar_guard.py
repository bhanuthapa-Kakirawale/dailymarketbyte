"""POST freeze - the publication guard in front of Market Radar stories.

The Radar selector's ranking and detectors are untouched. Before the POST Short takes the
selector's first `RADAR_PUBLISH_LIMIT` stories, each story's session move goes through the same
deterministic `core.move_guard.validate_move` the Movers ranking uses, fed from the story's own
visual evidence (the aligned OHLCV window the chart is drawn from):

  close / previous close     evidence.close_series[-1] / [-2]
  session bar                evidence open/high/low_series[-1] (when the artifact carries them)
  session alignment          evidence.window_dates[-1] must be the report's session and
                             [-2] the report's previous session (the Nifty calendar)
  stated change              story["price_change_pct"] must match the two closes
  volume confirmation        story volume_context.relative_volume (else evidence.rvol)

A story whose move is not publishable is skipped - the next story in the selector's own order
takes its place - and recorded with its raw values and reason. Without chart evidence only the
magnitude checks can run (and are recorded as such).
"""
from __future__ import annotations

import datetime as dt

from core.move_guard import validate_move


def _last(series, k=1):
    if not series or len(series) < k:
        return None
    return series[-k]


def radar_move_verdict(story: dict, evidence, session_date: dt.date | None = None,
                       prev_date: dt.date | None = None) -> dict:
    pct = story.get("price_change_pct")
    rvol = (story.get("volume_context") or {}).get("relative_volume")
    if evidence is not None and getattr(evidence, "close_series", None) and \
            len(evidence.close_series) >= 2:
        if rvol is None:
            rvol = getattr(evidence, "rvol", None)
        dates = list(getattr(evidence, "window_dates", None) or [])
        v = validate_move(close=_last(evidence.close_series), prev_close=_last(evidence.close_series, 2),
                          open_=_last(getattr(evidence, "open_series", None)),
                          high=_last(getattr(evidence, "high_series", None)),
                          low=_last(getattr(evidence, "low_series", None)),
                          change_pct=pct, relative_volume=rvol,
                          session_date=_last(dates), expected_session_date=session_date,
                          prev_date=_last(dates, 2), expected_prev_date=prev_date).to_dict()
        v["basis"] = "chart evidence"
        return v
    if pct is None:
        return {"status": "MISSING_PREVIOUS_CLOSE", "publishable": False, "change_pct": None,
                "reason": "no session move and no chart evidence", "checks": [], "details": {},
                "basis": "none"}
    # No chart evidence: only the stated move is known. Express it on a unit base so the
    # magnitude / corporate-action / volume rules still run; the alignment and bar checks
    # cannot, and the verdict says so.
    v = validate_move(close=100.0 * (1 + float(pct) / 100), prev_close=100.0,
                      relative_volume=rvol).to_dict()
    v["basis"] = "stated move only (no chart evidence - alignment and bar checks not run)"
    return v


def guard_radar_stories(stories: list, evidence_by_symbol: dict | None,
                        session_date: dt.date | None, prev_date: dt.date | None):
    """(publishable stories in the selector's order, audit rows for every story)."""
    kept, audit = [], []
    for sp, story in stories:
        sym = sp["instrument"]
        v = radar_move_verdict(story, (evidence_by_symbol or {}).get(sym), session_date, prev_date)
        audit.append({"instrument": sym, "selector_rank": len(audit) + 1,
                      "price_change_pct": story.get("price_change_pct"),
                      "relative_volume": (story.get("volume_context") or {}).get("relative_volume"),
                      **v})
        if v["publishable"]:
            kept.append((sp, story))
    return kept, audit


__all__ = ["radar_move_verdict", "guard_radar_stories"]

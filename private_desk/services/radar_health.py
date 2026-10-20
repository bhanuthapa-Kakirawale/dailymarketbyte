"""Distinguishes a genuine zero-candidate Radar session from a data-acquisition gap.

2026-10-19 forensics: `daily_radar_<session>.json` could report `pipeline_status=SUCCESS` with
`universe_usable=0` (2026-10-05/06/08/12/19, ~45% of the last 11 trading-day Radar runs) while
a genuine quiet market always showed full coverage (200/200). Root cause, fixed upstream in
`radar/ohlcv_service.py` (Radar OHLCV acquisition reliability V1): the daily 1-month tail fetch
was held to the 21-session minimum on its OWN window - 19-22 sessions depending on the run's
weekday and holidays - so it rejected every symbol at once although the store held full history.

Since that fix the Radar itself classifies acquisition health
(`coverage_diagnostics.acquisition.health`): a load with no usable symbol is a FAILED Radar
(code `OHLCV_ACQUISITION_FAILED`/`OHLCV_SOURCE_FAILURE`), and a partial load is DEGRADED
(`OHLCV_PARTIAL_COVERAGE`). This module still only lets the Desk tell a coverage gap apart from
a plain "0 candidates" day - for older artifacts that predate the classification too.
"""
from __future__ import annotations


def coverage_gap(art: dict | None) -> bool:
    """True when Radar completed (`SUCCESS`, or `DEGRADED` - a partial acquisition now runs
    DEGRADED) but acquired fewer usable symbols than it requested. False - never a false alarm -
    when the artifact is missing, the run FAILED (shown as a failure in its own right), or
    coverage was full (the ordinary, routine case, including a genuine quiet session with
    nothing to flag)."""
    if not art or art.get("pipeline_status") not in ("SUCCESS", "DEGRADED"):
        return False
    requested = art.get("universe_requested")
    if not requested:
        return False
    return (art.get("universe_usable") or 0) < requested


__all__ = ["coverage_gap"]

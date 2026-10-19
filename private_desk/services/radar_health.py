"""Distinguishes a genuine zero-candidate Radar session from a data-acquisition gap.

2026-10-19 forensics: `daily_radar_<session>.json` can report `pipeline_status=SUCCESS` with
`universe_usable=0` - Radar's own OHLCV fetch got nothing, even though the data existed and was
fetchable moments later in the SAME run (the `market_ohlcv.db` store shows good rows for that
exact session, written within the same report_job run, just not seen by Radar's own read).
Recurred on ~45% of the last 11 trading-day Radar runs (2026-10-05/06/08/12/19), each time
exactly `universe_usable=0` against a full `universe_requested=200`, the same days a genuine
quiet market always showed full coverage (200/200). This module adds NO fix for that upstream
gap (it is in `radar/`'s OHLCV acquisition, out of this package's scope) - it only lets the Desk
tell the two cases apart instead of showing both as a plain "0 candidates" day.
"""
from __future__ import annotations


def coverage_gap(art: dict | None) -> bool:
    """True only when Radar completed (`pipeline_status == "SUCCESS"`) but acquired fewer
    usable symbols than it requested. False - never a false alarm - when the artifact is
    missing, the run didn't succeed, or coverage was full (the ordinary, routine case,
    including a genuine quiet session with nothing to flag)."""
    if not art or art.get("pipeline_status") != "SUCCESS":
        return False
    requested = art.get("universe_requested")
    if not requested:
        return False
    return (art.get("universe_usable") or 0) < requested


__all__ = ["coverage_gap"]

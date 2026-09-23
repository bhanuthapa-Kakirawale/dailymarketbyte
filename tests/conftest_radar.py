"""Shared builders for unusual-volume-radar tests. Extends `conftest_intelligence.py` rather
than editing it - the radar package is a different consumer of the same canonical history.
"""
import datetime as dt

from conftest_intelligence import (BASE_SESSION, duplicate_relative_volume_observations, market,
                                   movers, seed, session_report, trading_sessions,
                                   _rewrite_relative_volume_version)

__all__ = ["BASE_SESSION", "market", "movers", "seed", "session_report", "trading_sessions",
          "universe", "historical_rvol_series", "duplicate_relative_volume_observations",
          "_rewrite_relative_volume_version"]


def universe(*symbols) -> list:
    """A caller-supplied universe list, exactly as `scan_universe` expects it."""
    return list(symbols)


def historical_rvol_series(symbol: str, values: list, today: dt.date = BASE_SESSION):
    """`len(values)` sessions strictly BEFORE `today`, oldest first, each with `symbol` at a
    chosen RVOL. `today` itself is left for the caller to build separately (it must never be
    part of its own comparison window) - this mirrors the `sessions[:-1]`/`sessions[-1]`
    idiom `tests/test_intelligence.py` uses for the same reason.

    Returns a list of canonical reports ready for `seed()`, so a test can assert an exact
    rank/percentile against a known series rather than an incidental one.
    """
    sessions = trading_sessions(len(values) + 1, last=today)[:-1]
    return [session_report(session, gainers=movers([symbol], volx=value))
           for session, value in zip(sessions, values)]

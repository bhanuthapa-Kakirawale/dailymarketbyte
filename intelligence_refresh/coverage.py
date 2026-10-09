"""Read-only, point-in-time coverage inspection for one historical session. Never fetches,
never writes. Used by both `refresh` (to decide what to attempt) and `status`/`--dry-run`
(to report what is or isn't there).

Only the three price-chain components (`registry.PRICE_CHAIN_KEYS`) are ever genuinely
checked against stored artifacts - every other component's status for a non-latest session
is simply its registry classification (`LATEST_SESSION_ONLY` sessions are HISTORICAL_
UNAVAILABLE once they aren't the current session; `UNSUPPORTED_HISTORICALLY`/`NOT_APPLICABLE`
never change). This mirrors `readiness/evidence.py`'s bounded-read style.
"""
from __future__ import annotations

import datetime as dt

from . import registry


def _cutoff_iso(now: dt.datetime) -> str:
    """`now`, as the IST ISO string `market_structure.store.load_revision_as_of`'s
    point-in-time `cutoff_iso` expects - the stores' own `retrieved_at` values are IST-aware
    ISO strings compared LEXICALLY (same convention as `readiness/evidence.py::cutoff_iso`), so
    the cutoff must be converted to IST too, never UTC: two correctly-ordered instants in
    different offsets do not sort correctly as plain strings.

    NOT the session's own end-of-day: a revision's `universe_source.retrieved_at` records when
    the NSE constituent list was DOWNLOADED (real wall-clock time of that build), never
    anything tied to the session date itself - a revision built today for a session from a
    month ago still carries today's timestamp. This check only asks "does a valid revision
    exist as of right now", not "was it known as of the session's own day" (that point-in-time
    replay question belongs to `readiness/evidence.py`, not here)."""
    import config
    return now.astimezone(config.IST).isoformat()


def _ohlcv_benchmark_present(out_dir: str, session: dt.date) -> bool:
    """Lightweight, honest proxy: does the market benchmark (`^NSEI`) have a stored bar for
    `session`? A missing benchmark bar means nothing has been fetched for that date yet; a
    present one means the Radar's own OHLCV store already has at least the benchmark anchor
    both `market_structure.build.build_for_session` and `radar.candidate_history_backfill.
    backfill_candidate_history` need. This is deliberately not a full per-symbol universe
    completeness check (that is each engine's own internal concern when it actually runs) -
    it only answers "would attempting this session likely be starting from nothing"."""
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    try:
        store = OHLCVStore(default_db_path(out_dir))
    except Exception:
        return False
    try:
        rows = store.get_range(["^NSEI"], session, session)
        return any(r.session_date == session for r in rows)
    except Exception:
        return False
    finally:
        store.close()


def _market_structure_present(out_dir: str, session: dt.date, now: dt.datetime) -> bool:
    from market_structure.store import load_revision_as_of
    try:
        return load_revision_as_of(out_dir, session, _cutoff_iso(now)) is not None
    except Exception:
        return False


def _radar_candidates_present(out_dir: str, session: dt.date, calculation_version: str) -> bool:
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.candidate_history_repository import default_db_path as candidate_db_path
    try:
        store = CandidateHistoryStore(candidate_db_path(out_dir))
    except Exception:
        return False
    try:
        return store.is_session_complete(session, calculation_version)
    except Exception:
        return False
    finally:
        store.close()


def scan_session(out_dir: str, session: dt.date, *, now: dt.datetime | None = None,
                 calculation_version: str | None = None) -> dict:
    """{component_key: status} for one historical (non-latest) session. Statuses used here:
    `registry.PRESENT` / `registry.MISSING` for the three price-chain components, and the
    component's own registry `backfill_mode` string for every other key."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if calculation_version is None:
        from radar.models import CALCULATION_VERSION
        calculation_version = CALCULATION_VERSION

    out = {}
    out["ohlcv_benchmark"] = (registry.PRESENT if _ohlcv_benchmark_present(out_dir, session)
                              else registry.MISSING)
    out["market_structure"] = (
        registry.PRESENT if _market_structure_present(out_dir, session, now) else registry.MISSING)
    out["radar_candidate_history"] = (
        registry.PRESENT if _radar_candidates_present(out_dir, session, calculation_version)
        else registry.MISSING)
    for key in registry.LATEST_ONLY_KEYS + registry.STATIC_KEYS:
        out[key] = registry.COMPONENTS[key].backfill_mode
    return out


__all__ = ["scan_session"]

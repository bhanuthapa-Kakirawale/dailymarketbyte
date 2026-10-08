"""Read-only, point-in-time evidence for the readiness gate.

Nothing here fetches, builds or writes. Every reader is bounded by the evaluation CUTOFF: a
record, snapshot or revision that came into existence after the cutoff is treated as not
existing yet - historical readiness must never be judged healthy because LATER state exists.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def parse_ts(value) -> dt.datetime | None:
    """ISO string / datetime -> an aware datetime (naive = IST, the pipeline's own clock)."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        ts = value
    else:
        try:
            ts = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return ts if ts.tzinfo else ts.replace(tzinfo=IST)


def at_or_before(value, cutoff: dt.datetime) -> bool:
    ts = parse_ts(value)
    return ts is not None and ts <= cutoff


def cutoff_iso(cutoff: dt.datetime) -> str:
    """The cutoff as the IST ISO string the stores compare against (their own timestamps are
    IST-aware ISO strings, compared lexically)."""
    return cutoff.astimezone(IST).isoformat()


# --------------------------------------------------------------------------- history
def history_db_path(out_dir: str) -> str:
    from storage import default_db_path
    return default_db_path(out_dir)


def open_history(out_dir: str):
    """A READ-ONLY MarketHistory (FileNotFoundError when there is no database at all)."""
    from storage import MarketHistory
    return MarketHistory.open_readonly(history_db_path(out_dir))


# --------------------------------------------------------------------------- REPORT job record
def report_record_at(out_dir: str, session: dt.date, cutoff: dt.datetime) -> dict | None:
    """The newest REPORT-job record for `session` that had COMPLETED by the cutoff (the
    per-session capture summary: official snapshots, institutional flows, market events)."""
    best, best_ts = None, None
    for path in glob.glob(os.path.join(out_dir, "report_jobs", session.isoformat(),
                                       "report_job_*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        ts = parse_ts(rec.get("completed_at") or rec.get("started_at"))
        if ts is None or ts > cutoff:
            continue
        if best_ts is None or ts > best_ts:
            best, best_ts = dict(rec, _path=path), ts
    return best


# --------------------------------------------------------------------------- Market Structure
def structure_artifact(out_dir: str, session: dt.date) -> tuple:
    """(artifact dict | None, path). Read-only JSON read - the full `load_snapshot` re-aggregates
    and is the renderer's business, not the gate's."""
    from market_structure.store import artifact_path
    path = artifact_path(out_dir, session)
    if not os.path.exists(path):
        return None, path
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), path
    except (OSError, ValueError):
        return {"_unreadable": True}, path


def structure_available_at(art: dict) -> dt.datetime | None:
    """When the snapshot's own data was obtained (`universe_source.retrieved_at`). The artifact
    is overwritten in place on a rebuild, so this is the only timestamp it carries."""
    snap = (art or {}).get("snapshot") or {}
    return parse_ts((snap.get("universe_source") or {}).get("retrieved_at"))


# --------------------------------------------------------------------------- official snapshots
def official_manifest(out_dir: str, session: dt.date) -> dict | None:
    from official_snapshots.store import load_manifest
    try:
        return load_manifest(out_dir, session)
    except Exception:
        return None


# --------------------------------------------------------------------------- market events
def live_event_families() -> list:
    """The families with a real adapter (P2A-P2F). IPO is a read-only projection of ipo_watch,
    never acquired by the engine, so it is never a market-events health input."""
    from market_events.models import ALL_FAMILIES, IPO
    from market_events.sources import DEFAULT_FETCHERS
    return [f for f in ALL_FAMILIES
            if f != IPO and not getattr(DEFAULT_FETCHERS.get(f), "not_supported_yet", False)]


def events_known_at(out_dir: str, family: str, cutoff: dt.datetime) -> list:
    """Every event of `family` as it was on disk at the cutoff (`list_events(as_of=)`: the
    revision genuinely current then - a later revision never leaks backward)."""
    from market_events.store import list_events
    return [ev for _k, _p, ev in list_events(out_dir, family, as_of=cutoff_iso(cutoff))]


# --------------------------------------------------------------------------- institutional
def institutional_latest(out_dir: str, source: str, on_or_before: dt.date, cutoff: dt.datetime):
    from institutional_flows.store import load_latest
    return load_latest(out_dir, source, on_or_before=on_or_before,
                       first_retrieved_before=cutoff_iso(cutoff))


# --------------------------------------------------------------------------- POST manifests
def post_manifest(out_dir: str, session: dt.date) -> tuple:
    """(path, manifest) of the newest production manifest describing `session`, else (None, None)."""
    found = []
    for path in glob.glob(os.path.join(out_dir, "post", "*", "production_manifest.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError):
            continue
        if str(m.get("session_date")) == session.isoformat() and "DEMO" not in path:
            found.append((os.path.getmtime(path), path, m))
    if not found:
        return None, None
    _t, path, m = max(found, key=lambda t: t[0])
    return path, m


__all__ = ["IST", "parse_ts", "at_or_before", "cutoff_iso", "history_db_path", "open_history",
           "report_record_at", "structure_artifact", "structure_available_at",
           "official_manifest", "live_event_families", "events_known_at",
           "institutional_latest", "post_manifest"]

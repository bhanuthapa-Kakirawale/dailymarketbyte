"""On-disk layout for Market Events - immutable, revisioned, no mutable index.

    <out_dir>/market_events/<FAMILY>/<safe_event_key>.json        revision 1
    <out_dir>/market_events/<FAMILY>/<safe_event_key>.r2.json     a later acquisition
    <out_dir>/market_events/attempts/<YYYY-MM-DD>/<family>_<HHMMSS>_<mode>.json

Keyed by (family, event_key) rather than (source, report_key): one family carries MANY live
events at once (every scheduled earnings filing, every open buyback), not one report per source,
so `load_latest` returns the latest revision of every qualifying event_key, not a single
snapshot. Otherwise this mirrors `institutional_flows/store.py` exactly: a later revision is
never refused because an earlier one validated (a board-meeting date or buyback price can
legitimately be restated), and a revision file, once written, is never edited or replaced - only
superseded by a new, separately-numbered file.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

from .models import MarketEvent


def _family_dir(out_dir: str, family: str) -> str:
    return os.path.join(out_dir, "market_events", family)


def _safe_key(event_key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in event_key) or "unknown"


def _revision_path(out_dir: str, family: str, event_key: str, n: int) -> str:
    stem = _safe_key(event_key)
    name = f"{stem}.json" if n == 1 else f"{stem}.r{n}.json"
    return os.path.join(_family_dir(out_dir, family), name)


def _existing_revisions(out_dir: str, family: str, event_key: str) -> list:
    stem = _safe_key(event_key)
    folder = _family_dir(out_dir, family)
    paths = sorted(glob.glob(os.path.join(folder, f"{stem}.json")) +
                   glob.glob(os.path.join(folder, f"{stem}.r*.json")))
    return paths


def load_revision(path: str) -> MarketEvent | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return MarketEvent.from_dict(json.load(fh))


def write_revision(out_dir: str, event: MarketEvent) -> str:
    """Append `event` as the next revision of (family, event_key). Never refuses because an
    earlier revision already validated - the caller (`service.py`) decides REVISED vs UNCHANGED
    by comparing checksums before calling this at all."""
    from operations.run_context import guard_write
    folder = _family_dir(out_dir, event.family)
    guard_write(folder, "market event revision")
    os.makedirs(folder, exist_ok=True)
    existing = _existing_revisions(out_dir, event.family, event.event_key)
    n = len(existing) + 1
    path = _revision_path(out_dir, event.family, event.event_key, n)
    while os.path.exists(path):
        n += 1
        path = _revision_path(out_dir, event.family, event.event_key, n)
    event.revision = n
    event.seal()
    with open(path, "x", encoding="utf-8") as fh:     # exclusive create - never overwrite
        json.dump(event.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    return path


def list_events(out_dir: str, family: str, *, as_of: str | None = None) -> list:
    """Every event_key on disk for `family`, as (event_key, path, event).

    With `as_of` omitted (every caller except `load_latest`'s own point-in-time replay path),
    "latest" means the highest-numbered revision on disk for that key - unchanged, one
    `load_revision` per key, the cheap common case.

    With `as_of` given (an ISO timestamp), "latest" instead means the highest-numbered revision
    whose OWN `retrieved_at` is STRICTLY before `as_of` - the state that was genuinely on disk
    at that moment - rather than today's absolute latest revision (strict, matching the
    parameter's own pre-existing "before" contract: a revision retrieved at exactly `as_of`
    does not yet count as known as of that instant). `retrieved_at` (unlike `first_retrieved_at`,
    which is carried forward unchanged across every revision) is set fresh on each capture, so
    this is the only field that can tell revisions apart in time. A key with NO revision at or
    before `as_of` (not even revision 1) is excluded entirely - the same "not yet discovered"
    exclusion the old first-seen-only gate provided, now also correct for a key that WAS known
    before `as_of` but was revised again afterward (a reschedule, a result, a status change) -
    that later revision must never leak backward into an earlier replay."""
    folder = _family_dir(out_dir, family)
    if not os.path.isdir(folder):
        return []
    by_key: dict = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        name = os.path.basename(path)[:-5]
        key = name.split(".r")[0]
        n = int(name.split(".r")[1]) if ".r" in name else 1
        by_key.setdefault(key, []).append((n, path))

    out = []
    for key, revs in by_key.items():
        revs.sort(key=lambda t: t[0])
        if as_of is None:
            _n, path = revs[-1]
            ev = load_revision(path)
            if ev is not None:
                out.append((ev.event_key, path, ev))
            continue
        chosen = None
        for _n, path in revs:
            ev = load_revision(path)
            if ev is not None and ev.retrieved_at and ev.retrieved_at < as_of:
                chosen = (ev.event_key, path, ev)
        if chosen is not None:
            out.append(chosen)
    out.sort(key=lambda t: t[0])
    return out


def load_latest(out_dir: str, family: str, *, symbol: str | None = None,
                on_or_before: dt.date | None = None, first_retrieved_before: str | None = None,
                validated_only: bool = True) -> list:
    """Every event_key's latest qualifying revision for `family`: `symbol` filters to one
    security when given; `on_or_before` bounds `data_as_of`; `first_retrieved_before` is the
    point-in-time replay gate, passed straight through as `list_events`'s `as_of` - the revision
    returned for each key is the one genuinely on disk at that timestamp (by its own
    `retrieved_at`), so a later revision (a reschedule, a result, any change) never leaks
    backward into an earlier replay, and a key not yet known at all as of this timestamp is
    excluded entirely."""
    out = []
    for event_key, _path, ev in list_events(out_dir, family, as_of=first_retrieved_before):
        if validated_only and not ev.validated:
            continue
        if symbol is not None and ev.symbol != symbol:
            continue
        if on_or_before is not None and ev.data_as_of:
            try:
                if dt.date.fromisoformat(ev.data_as_of) > on_or_before:
                    continue
            except ValueError:
                pass
        out.append(ev)
    return out


def record_attempt(out_dir: str, family: str, mode: str, now: dt.datetime, payload: dict) -> str:
    """Append-only log of every capture attempt (success, failure, or NOT_SUPPORTED_YET), for
    Data Quality / debugging - never consulted to decide what "current" is."""
    from operations.run_context import guard_write
    folder = os.path.join(out_dir, "market_events", "attempts", now.date().isoformat())
    guard_write(folder, "market events attempt log")
    os.makedirs(folder, exist_ok=True)
    name = f"{family}_{now.strftime('%H%M%S_%f')}_{mode}.json"
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


def latest_attempts(out_dir: str, *, days: int = 3) -> list:
    """The most recent attempt log entries across all families, newest first - for Data Quality.
    Never raises on a missing/partial directory."""
    folder = os.path.join(out_dir, "market_events", "attempts")
    if not os.path.isdir(folder):
        return []
    out = []
    for day_dir in sorted(glob.glob(os.path.join(folder, "*")), reverse=True)[:max(days, 1)]:
        for path in sorted(glob.glob(os.path.join(day_dir, "*.json")), reverse=True):
            try:
                with open(path, encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except (OSError, ValueError):
                continue
    return out


__all__ = ["write_revision", "load_revision", "list_events", "load_latest", "record_attempt",
           "latest_attempts"]

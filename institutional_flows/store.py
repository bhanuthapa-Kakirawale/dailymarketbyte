"""On-disk layout for institutional flow snapshots - immutable, revisioned, no mutable index.

    <out_dir>/institutional_flows/<NSE|CDSL|NSDL>/<report_key>.json       revision 1
    <out_dir>/institutional_flows/<NSE|CDSL|NSDL>/<report_key>.r2.json    a later acquisition
    <out_dir>/institutional_flows/attempts/<YYYY-MM-DD>/<source>_<HHMMSS>_<mode>.json

Unlike `official_snapshots`, there is no per-session manifest: each source's own `report_key`
already IS the stable identity NSE/CDSL/NSDL assign to one report, so "the current snapshot for
report_key X" is simply "the latest revision of X on disk" - found by listing, not by a mutable
index that could itself drift from what is actually on disk. `source`/`report_key` together are
the whole namespace.

Unlike `official_snapshots`'s exchange/surveillance lists, a NSE/CDSL/NSDL figure for a given
report_key is explicitly provisional and CAN be legitimately restated later (that is exactly
what `service.REVISED` represents) - so, unlike `official_snapshots.write_revision`, a later
revision is never refused merely because an earlier one validated. What stays immutable is each
REVISION FILE itself: once written, a revision is never edited or replaced, only superseded by
a new, separately-numbered file.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

from .models import InstitutionalFlowSnapshot


def _source_dir(out_dir: str, source: str) -> str:
    return os.path.join(out_dir, "institutional_flows", source)


def _safe_key(report_key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in report_key) or "unknown"


def _revision_path(out_dir: str, source: str, report_key: str, n: int) -> str:
    stem = _safe_key(report_key)
    name = f"{stem}.json" if n == 1 else f"{stem}.r{n}.json"
    return os.path.join(_source_dir(out_dir, source), name)


def _existing_revisions(out_dir: str, source: str, report_key: str) -> list:
    stem = _safe_key(report_key)
    folder = _source_dir(out_dir, source)
    paths = sorted(glob.glob(os.path.join(folder, f"{stem}.json")) +
                   glob.glob(os.path.join(folder, f"{stem}.r*.json")))
    return paths


def load_revision(path: str) -> InstitutionalFlowSnapshot | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return InstitutionalFlowSnapshot.from_dict(json.load(fh))


def write_revision(out_dir: str, snapshot: InstitutionalFlowSnapshot) -> str:
    """Append `snapshot` as the next revision of (source, report_key). A provisional NSE/CDSL/
    NSDL figure may legitimately be restated, so - unlike `official_snapshots.write_revision` -
    this never refuses because an earlier revision already validated; it only ever ADDS a new,
    never-reused file name. The caller (`service.py`) decides REVISED vs UNCHANGED by comparing
    checksums before calling this at all."""
    from operations.run_context import guard_write
    folder = _source_dir(out_dir, snapshot.source)
    guard_write(folder, "institutional flow snapshot")
    os.makedirs(folder, exist_ok=True)
    existing = _existing_revisions(out_dir, snapshot.source, snapshot.report_key)
    n = len(existing) + 1
    path = _revision_path(out_dir, snapshot.source, snapshot.report_key, n)
    while os.path.exists(path):
        n += 1
        path = _revision_path(out_dir, snapshot.source, snapshot.report_key, n)
    snapshot.revision = n
    snapshot.seal()
    with open(path, "x", encoding="utf-8") as fh:     # exclusive create - never overwrite
        json.dump(snapshot.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    return path


def list_snapshots(out_dir: str, source: str, *, as_of: str | None = None) -> list:
    """Every report_key on disk for `source`, newest first, as (report_key, path, snapshot).

    `as_of` omitted (every caller except `load_latest`'s own point-in-time path): "latest"
    means the highest-numbered revision on disk per key - unchanged, the cheap common case
    every current/live caller relies on.

    `as_of` given (an ISO timestamp): "latest" instead means the highest-numbered revision
    whose OWN `retrieved_at` is STRICTLY before `as_of` - the revision genuinely on disk at
    that moment, never today's absolute latest. `retrieved_at` (unlike `first_retrieved_at`,
    which `service.py` pins to the FIRST revision's capture time and carries forward unchanged
    on every REVISED write) is set fresh on each capture, so it is the only field that can tell
    revisions apart in time. A report_key with no revision yet at `as_of` is excluded entirely -
    a later restatement must never leak backward into an earlier replay. Mirrors
    `market_events.store.list_events`."""
    folder = _source_dir(out_dir, source)
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
            snap = load_revision(path)
            if snap is not None:
                out.append((snap.report_key, path, snap))
            continue
        chosen = None
        for _n, path in revs:
            snap = load_revision(path)
            if snap is not None and snap.retrieved_at and snap.retrieved_at < as_of:
                chosen = (snap.report_key, path, snap)
        if chosen is not None:
            out.append(chosen)
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def load_latest(out_dir: str, source: str, *, on_or_before: dt.date | None = None,
                first_retrieved_before: str | None = None,
                validated_only: bool = True) -> InstitutionalFlowSnapshot | None:
    """The newest snapshot for `source` whose report_key (parsed as a date where possible) is
    `<= on_or_before`, selected as of `first_retrieved_before` (passed straight through to
    `list_snapshots` as `as_of`) - the revision genuinely on disk at that timestamp by its own
    `retrieved_at`, so a later restatement never leaks backward into an earlier replay, and a
    report_key not yet known at all as of this timestamp is excluded entirely."""
    candidates = list_snapshots(out_dir, source, as_of=first_retrieved_before)
    for report_key, _path, snap in candidates:
        if validated_only and not snap.validated:
            continue
        if on_or_before is not None:
            try:
                if dt.date.fromisoformat(report_key) > on_or_before:
                    continue
            except ValueError:
                pass
        return snap
    return None


def record_attempt(out_dir: str, source: str, mode: str, now: dt.datetime, payload: dict) -> str:
    """Append-only log of every capture attempt (success or failure), for Data Quality /
    debugging - never consulted to decide what "the current snapshot" is."""
    from operations.run_context import guard_write
    folder = os.path.join(out_dir, "institutional_flows", "attempts", now.date().isoformat())
    guard_write(folder, "institutional flow attempt log")
    os.makedirs(folder, exist_ok=True)
    name = f"{source}_{now.strftime('%H%M%S_%f')}_{mode}.json"
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


def latest_attempts(out_dir: str, *, days: int = 3) -> list:
    """The most recent attempt log entries across all sources, newest first - for Data Quality.
    Never raises on a missing/partial directory."""
    folder = os.path.join(out_dir, "institutional_flows", "attempts")
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


__all__ = ["write_revision", "load_revision", "list_snapshots", "load_latest",
           "record_attempt", "latest_attempts"]

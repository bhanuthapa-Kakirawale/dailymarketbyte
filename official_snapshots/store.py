"""On-disk layout of official snapshots - immutable revisions + one manifest per session.

    <out_dir>/official_snapshots/<session>/
        official_snapshot_manifest.json      index: per kind, every revision + which is current
        fno_ban_snapshot.json                revision 1 (never rewritten)
        fno_ban_snapshot.r2.json             a later acquisition (e.g. the 21:30 retry after a
                                             19:30 SOURCE_UNAVAILABLE) - appended, never replacing

Rules: a revision file is created exclusively and never modified. Once a kind has a VALIDATED
revision (SUCCESS / NO_DATA) it is the current one for good - no later acquisition replaces it;
only a failed revision can be followed by a new attempt. A revision whose records no longer
match its checksum is treated as VALIDATION_FAILED and never used.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from .models import (FILE_STEM, MANIFEST_NAME, MANIFEST_SCHEMA_VERSION, VALIDATION_FAILED,
                     OfficialSnapshot, records_checksum)


def session_dir(out_dir: str, session: dt.date) -> str:
    return os.path.join(out_dir, "official_snapshots", session.isoformat())


def manifest_path(out_dir: str, session: dt.date) -> str:
    return os.path.join(session_dir(out_dir, session), MANIFEST_NAME)


def load_manifest(out_dir: str, session: dt.date) -> dict | None:
    p = manifest_path(out_dir, session)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _write_manifest(out_dir: str, session: dt.date, manifest: dict) -> str:
    p = manifest_path(out_dir, session)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, p)
    return p


def _revision_name(kind: str, n: int) -> str:
    stem = FILE_STEM[kind]
    return f"{stem}.json" if n == 1 else f"{stem}.r{n}.json"


def write_revision(out_dir: str, snapshot: OfficialSnapshot) -> tuple:
    """Append `snapshot` as the next revision of its kind; returns (file name, manifest path).
    Refuses (ValueError) when the kind already has a validated current revision."""
    session = dt.date.fromisoformat(snapshot.session_date)
    folder = session_dir(out_dir, session)
    os.makedirs(folder, exist_ok=True)
    manifest = load_manifest(out_dir, session) or {
        "schema_version": MANIFEST_SCHEMA_VERSION, "session_date": snapshot.session_date,
        "expected_list_date": snapshot.expected_list_date, "snapshots": {}}
    entry = manifest["snapshots"].setdefault(snapshot.kind, {"current": None, "revisions": []})
    if entry.get("validated"):
        raise ValueError(f"{snapshot.kind} {snapshot.session_date} already has a validated "
                         f"snapshot ({entry['current']}) - immutable")
    n = len(entry["revisions"]) + 1
    while os.path.exists(os.path.join(folder, _revision_name(snapshot.kind, n))):
        n += 1                               # never reuse a name, even an orphaned one
    snapshot.revision = n
    snapshot.seal()
    name = _revision_name(snapshot.kind, n)
    with open(os.path.join(folder, name), "x", encoding="utf-8") as fh:    # exclusive create
        json.dump(snapshot.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    entry["revisions"].append(snapshot.summary(name))
    # current = the validated revision (first, and final); else the latest attempt
    entry["current"] = name
    entry["validated"] = snapshot.validated
    entry.update({k: v for k, v in snapshot.summary(name).items() if k != "file"})
    manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return name, _write_manifest(out_dir, session, manifest)


def load_current(out_dir: str, session: dt.date, kind: str) -> tuple:
    """(OfficialSnapshot | None, file name | None). The manifest's current revision, checked
    against its checksum."""
    manifest = load_manifest(out_dir, session)
    entry = ((manifest or {}).get("snapshots") or {}).get(kind)
    if not entry or not entry.get("current"):
        return None, None
    path = os.path.join(session_dir(out_dir, session), entry["current"])
    if not os.path.exists(path):
        return None, entry["current"]
    with open(path, encoding="utf-8") as fh:
        snap = OfficialSnapshot.from_dict(json.load(fh))
    if records_checksum(snap.records) != snap.checksum or snap.checksum != entry.get("checksum"):
        snap.status = VALIDATION_FAILED
        snap.reason = "checksum mismatch - the stored records do not match their manifest"
        snap.records = []
    return snap, entry["current"]


__all__ = ["session_dir", "manifest_path", "load_manifest", "write_revision", "load_current"]

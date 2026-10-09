"""The Market Structure artifact (`output/market_structure/market_structure_<session>.json`) and
the publishable facts a chosen insight contributes.

The artifact is INTERNAL: it keeps the per-constituent observations (named) so every public
count can be traced back to the observation ids behind it. Loading re-aggregates from those
observations and refuses a file whose stored counts do not match - a count that cannot be
reproduced from its observations is not published.
"""
from __future__ import annotations

import copy
import datetime as dt
import glob
import hashlib
import json
import os

from core import sources as S
from presentation.provenance_label import session_label
from publication.classification import (ContentClass, Orientation, Origin, PublishableFact,
                                        RightsStatus, Scope)
from publication.classify import strictest_rights

from .aggregator import ReconciliationError, aggregate
from .observations import StructureObservation
from .universe import UniverseDefinition

SOURCE_NAMES = (S.SRC_YAHOO, S.SRC_NSE_CONSTITUENTS, S.SRC_MARKET_STRUCTURE)
SOURCE_LABEL = "Yahoo Finance EOD data · NSE Indices sectors"
# two different roles - never "NSE supplied the prices"
SOURCE_ROLES = (("PRICES", "Yahoo Finance EOD"), ("UNIVERSE & SECTORS", "NSE"))


def artifact_path(out_dir: str, session: dt.date) -> str:
    return os.path.join(out_dir, "market_structure", f"market_structure_{session.isoformat()}.json")


def _revision_path(out_dir: str, session: dt.date, n: int) -> str:
    return os.path.join(out_dir, "market_structure",
                        f"market_structure_{session.isoformat()}.rev{n}.json")


def _existing_revisions(out_dir: str, session: dt.date) -> list:
    """Immutable revision files for `session`, ascending by revision number. Deliberately
    `.revN.json` even for revision 1 - unlike `institutional_flows`/`market_events`'s "rev1 is
    bare" convention, the bare `market_structure_<session>.json` name is permanently reserved
    for the MUTABLE "current" file every existing live reader already depends on; revision 1
    of the immutable chain can never reuse that name."""
    folder = os.path.join(out_dir, "market_structure")
    paths = glob.glob(os.path.join(folder, f"market_structure_{session.isoformat()}.rev*.json"))
    return sorted(paths, key=lambda p: int(p.rsplit(".rev", 1)[1][:-5]))


def _content_checksum(payload: dict) -> str:
    """sha256 over canonical JSON of `payload`, with every volatile universe-download
    `retrieved_at` stripped first. `market.get_universe` stamps a fresh `retrieved_at` on every
    download, and that same value is independently embedded in FOUR places here -
    `universe.retrieved_at`, each `subset_universes[i].retrieved_at`, and two separate copies
    inside `snapshot` itself (`universe_source.retrieved_at` and `sector_mapping.retrieved_at`,
    both built from the same `UniverseDefinition` by `aggregate()`) - all four must be excluded,
    or a same-content rebuild would still look "changed" via whichever one is left in."""
    scrubbed = copy.deepcopy(payload)
    snap = scrubbed.get("snapshot") or {}
    snap.get("universe_source", {}).pop("retrieved_at", None)
    snap.get("sector_mapping", {}).pop("retrieved_at", None)
    (scrubbed.get("universe") or {}).pop("retrieved_at", None)
    for sub in scrubbed.get("subset_universes") or []:
        sub.pop("retrieved_at", None)
    blob = json.dumps(scrubbed, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def save_snapshot(snapshot, observations, universe_def, out_dir: str, subset_defs=()) -> str:
    """Writes/refreshes the mutable "current" artifact (unchanged path, unchanged semantics for
    every current/live reader) AND preserves point-in-time history as an immutable `.revN.json`
    chain alongside it: a true no-op when the rebuilt content is unchanged (by `_content_
    checksum`, never a new revision just because it reran); a new, exclusively-created revision
    file when it genuinely changed. A session with no revision chain yet but an existing current
    file (pre-fix state, or the first rebuild since this fix shipped) has that EXISTING file's
    exact bytes seeded as revision 1 first - never silently discarding pre-fix history."""
    session = dt.date.fromisoformat(snapshot.session_date)
    path = artifact_path(out_dir, session)
    from operations.run_context import guard_write
    guard_write(path, "Market Structure snapshot")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"snapshot": snapshot.to_dict(), "universe": universe_def.to_dict(),
               "subset_universes": [s.to_dict() for s in subset_defs or ()],
               "observations": [o.to_dict() for o in observations],
               "private": True, "note": "internal artifact - names securities; public output "
                                        "shows counts only"}
    new_checksum = _content_checksum(payload)

    revisions = _existing_revisions(out_dir, session)
    if not revisions and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            legacy_payload = json.load(fh)
        with open(_revision_path(out_dir, session, 1), "x", encoding="utf-8") as fh:
            json.dump(legacy_payload, fh, indent=2, ensure_ascii=False, default=str)
        revisions = _existing_revisions(out_dir, session)

    if revisions:
        with open(revisions[-1], encoding="utf-8") as fh:
            latest_payload = json.load(fh)
        if _content_checksum(latest_payload) == new_checksum:
            return path    # true no-op: no new revision, current file not even touched
        next_n = int(revisions[-1].rsplit(".rev", 1)[1][:-5]) + 1
    else:
        next_n = 1

    with open(_revision_path(out_dir, session, next_n), "x", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)     # exclusive create
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


def load_revision_as_of(out_dir: str, session: dt.date, cutoff_iso: str) -> dict | None:
    """The artifact payload genuinely on disk for `session` as of `cutoff_iso` (the revision
    chain's own highest-numbered revision whose `universe_source.retrieved_at` is AT OR BEFORE
    `cutoff_iso`, inclusive - a revision captured exactly at the cutoff is already known as of
    that instant), never today's possibly-since-rebuilt current file blindly re-read.
    Falls back to the single bare "current" file, treated as one legacy revision, only when NO
    `.revN.json` chain exists at all for this session (a session never rebuilt since this fix
    shipped) - never infers from filesystem mtime, never backdates a timestamp."""
    revisions = _existing_revisions(out_dir, session)
    if not revisions:
        legacy = artifact_path(out_dir, session)
        if not os.path.exists(legacy):
            return None
        revisions = [legacy]
    chosen = None
    for rpath in revisions:
        try:
            with open(rpath, encoding="utf-8") as fh:
                art = json.load(fh)
        except (OSError, ValueError):
            continue
        retrieved = ((art.get("snapshot") or {}).get("universe_source") or {}).get("retrieved_at")
        if retrieved and retrieved <= cutoff_iso:
            chosen = art
    return chosen


def load_snapshot(path: str):
    """(snapshot, universe_def, observations), re-aggregated and checked against the file."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    uni = UniverseDefinition.from_dict(d["universe"])
    subs = [UniverseDefinition.from_dict(s) for s in d.get("subset_universes") or []]
    obs = [StructureObservation.from_dict(o) for o in d["observations"]]
    session = dt.date.fromisoformat(d["snapshot"]["session_date"])
    snap = aggregate(obs, uni, session, subs)
    stored_metrics = d["snapshot"]["metrics"]
    for k, m in snap.metrics.items():
        if k not in stored_metrics:
            # a metric added after this file was written (e.g. Market Structure V2's
            # NEW_52W_HIGH/NEW_52W_LOW) - the file never claimed a count for it, so there is
            # nothing to reconcile; its observations correctly carry no fact for it either
            # (StructureObservation.from_dict leaves an unknown field None/falsy, i.e. "not
            # covered") - never raise for a key the file predates.
            continue
        stored = stored_metrics[k]
        if (stored.get("numerator"), stored.get("denominator")) != (m.numerator, m.denominator):
            raise ReconciliationError(f"{path}: stored {k} does not reproduce from observations")
    return snap, uni, obs


def structure_facts(insight, snapshot, session: dt.date) -> list:
    """The PublishableFacts one chosen insight puts on screen - MARKET scope, our own aggregate,
    tagged MARKET_STRUCTURE and carrying its universe (the gate refuses one without)."""
    rights = strictest_rights(SOURCE_NAMES)
    prov = session_label(SOURCE_LABEL, session, SOURCE_ROLES)
    out = []
    for i, text in enumerate(insight.public_strings()):
        out.append(PublishableFact(
            fact_id=f"structure.{insight.kind.lower()}.{i}", text=text, scope=Scope.MARKET,
            origin=Origin.INTERNAL_ANALYTICS, content_class=ContentClass.MARKET_AGGREGATE,
            orientation=Orientation.HISTORICAL, source_name=S.SRC_MARKET_STRUCTURE,
            source_label=prov.source, source_reference=snapshot.universe_source.get(
                "source_reference", ""), data_as_of=session, universe=snapshot.universe_label,
            publication_rights_status=rights if rights is not RightsStatus.UNKNOWN
            else RightsStatus.UNKNOWN, section="STRUCTURE",
            tags=frozenset({"MARKET_STRUCTURE"})))
    return out


__all__ = ["save_snapshot", "load_snapshot", "load_revision_as_of", "structure_facts",
           "artifact_path", "SOURCE_LABEL", "SOURCE_ROLES", "SOURCE_NAMES"]

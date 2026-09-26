"""Hydrate / persist: move the approved state artifacts between the runner's output directory
and the StateStore.

    runner starts clean -> hydrate(out_dir) -> job -> persist(out_dir) -> runner disappears

What is synced (and nothing else - no MP4, no image, no .env, no token):

    namespace            output/ dir           files     rule
    reports              reports/              *.json    IMMUTABLE (canonical reports; not unfit/)
    market_structure     market_structure/     *.json    IMMUTABLE (session snapshots)
    official_snapshots   official_snapshots/   **.json   revisions IMMUTABLE, manifests mutable
    databases            data/                 *.db      mutable (run history, OHLCV, editorial)
    radar                radar/                **.json   mutable
    intelligence         intelligence/         **.json   mutable (derived)
    run_history          report_jobs/          **.json   mutable
    publication_audits   publication/          **.json   mutable

IMMUTABLE objects are create-only: an existing object with different content is a CONFLICT -
recorded, never overwritten. Mutable objects are replaced only if the stored object is still the
version this runner hydrated (optimistic concurrency; the workflows also share one concurrency
group). Safety: a job that failed to hydrate from a configured store never persists - an empty
runner must not overwrite the durable history with an empty database.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import os
from dataclasses import dataclass

from .store import StateConflict, StateStore, from_env, md5_b64


@dataclass(frozen=True)
class Namespace:
    key: str
    local: str
    pattern: str
    recursive: bool
    immutable: str          # "all" / "none" / "revisions"

    def is_immutable(self, rel: str) -> bool:
        if self.immutable == "all":
            return True
        if self.immutable == "revisions":
            return not rel.endswith("official_snapshot_manifest.json")
        return False


NAMESPACES = (
    Namespace("reports", "reports", "*.json", False, "all"),
    Namespace("market_structure", "market_structure", "*.json", False, "all"),
    Namespace("official_snapshots", "official_snapshots", "*.json", True, "revisions"),
    Namespace("databases", "data", "*.db", False, "none"),
    Namespace("radar", "radar", "*.json", True, "none"),
    Namespace("intelligence", "intelligence", "*.json", True, "none"),
    Namespace("run_history", "report_jobs", "*.json", True, "none"),
    Namespace("publication_audits", "publication", "*.json", True, "none"),
)

# one process = one job: what was hydrated (versions) and whether persisting is allowed
_SESSION: dict = {}


def _local_files(out_dir: str, ns: Namespace):
    base = os.path.join(out_dir, ns.local)
    if not os.path.isdir(base):
        return
    walker = os.walk(base) if ns.recursive else [(base, [], os.listdir(base))]
    for dirpath, _, files in walker:
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            if os.path.isfile(full) and fnmatch.fnmatch(f, ns.pattern):
                yield os.path.relpath(full, base).replace(os.sep, "/"), full


def _accept(ns: Namespace, rel: str) -> bool:
    return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], ns.pattern) and (ns.recursive or "/" not in rel)


def resolve(out_dir: str, store: StateStore | None = None, env=None) -> StateStore | None:
    return store if store is not None else from_env(out_dir, env)


def hydrate(out_dir: str, store: StateStore | None = None, job: str = "", env=None) -> dict:
    """Download every approved object into `out_dir` (the store wins over a stale local copy).
    Raises on a store failure - the caller must not run the job on missing state."""
    store = resolve(out_dir, store, env)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    if store is None:
        _SESSION.clear()
        _SESSION.update(job=job, store=None, hydrated=False, index={})
        return {"hydrated": False, "state_store_backend": "local",
                "reason": "local backend - the output directory is the state", "job": job}
    index, counts = {}, {}
    for ns in NAMESPACES:
        remote = store.list(ns.key)
        n = 0
        for key, info in sorted(remote.items()):
            rel = key[len(ns.key) + 1:]
            if not _accept(ns, rel):
                continue
            local = os.path.join(out_dir, ns.local, *rel.split("/"))
            index[key] = info
            if os.path.exists(local) and md5_b64(local) == info.get("md5"):
                continue
            store.get(key, local)
            n += 1
        counts[ns.key] = {"objects": sum(1 for k in remote if k.startswith(ns.key + "/")),
                          "downloaded": n}
    _SESSION.clear()
    _SESSION.update(job=job, store=store, hydrated=True, index=index)
    return {"hydrated": True, "job": job, "started_at": started, "namespaces": counts,
            **{"state_store_" + k: v for k, v in store.describe().items()}}


def persist(out_dir: str, store: StateStore | None = None, job: str = "",
            require_hydrated: bool = True, env=None) -> dict:
    """Upload new / changed approved artifacts. Immutable conflicts are reported, never
    overwritten. Returns the result record (safe to write into run history: no secrets)."""
    active = _SESSION.get("store")
    store = store if store is not None else (active if active is not None else
                                             resolve(out_dir, None, env))
    if store is None:
        return {"persisted_to_state_store": False, "state_store_backend": "local",
                "reason": "local backend - the output directory is the state"}
    hydrated = _SESSION.get("hydrated") and _SESSION.get("store") is store
    if require_hydrated and not hydrated:
        return {"persisted_to_state_store": False, "state_store_backend": store.backend,
                "reason": "not hydrated in this run - refusing to persist (would overwrite "
                          "durable state from an incomplete runner)"}
    index = dict(_SESSION.get("index") or {}) if hydrated else {}
    uploaded, unchanged, conflicts, errors = [], 0, [], []
    for ns in NAMESPACES:
        remote = index if hydrated else store.list(ns.key)
        for rel, full in _local_files(out_dir, ns):
            key = f"{ns.key}/{rel}"
            info = remote.get(key)
            local_md5 = md5_b64(full)
            if info and info.get("md5") == local_md5:
                unchanged += 1
                continue
            immutable = ns.is_immutable(rel)
            if info and immutable:
                conflicts.append({"key": key, "reason": "immutable object exists with different "
                                                        "content - not overwritten"})
                continue
            try:
                version = store.put(key, full, expected=info.get("version") if info else None)
                index[key] = {"md5": local_md5, "version": version}
                uploaded.append(key)
            except StateConflict as exc:
                conflicts.append({"key": key, "reason": str(exc)})
            except Exception as exc:
                errors.append({"key": key, "error": type(exc).__name__})
    if hydrated:
        _SESSION["index"] = index
    return {"persisted_to_state_store": not errors and not conflicts, "job": job,
            "uploaded": len(uploaded), "unchanged": unchanged, "conflicts": conflicts,
            "errors": errors, "uploaded_keys": uploaded[:200],
            **{"state_store_" + k: v for k, v in store.describe().items()}}


def active_backend() -> str:
    store = _SESSION.get("store")
    return store.backend if store is not None else "local"


def reset() -> None:
    _SESSION.clear()


__all__ = ["NAMESPACES", "Namespace", "hydrate", "persist", "resolve", "active_backend", "reset"]

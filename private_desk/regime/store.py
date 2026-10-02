"""Derived, rebuildable regime snapshots under `<OUT_DIR>/private_desk/regime/`.

Not a canonical database: one JSON file per CALCULATION_VERSION, keyed by a fingerprint of the
inputs (OHLCV store, Market Structure artifacts, market-history store). When the version or any
input changes, the stored snapshots are discarded and recomputed - so a threshold change can
never be served from an old cache, and deleting the folder only costs a recompute.

Writes go through `DeskCache` (refuses any path outside the desk folder); a failed write is
swallowed and the freshly computed snapshots are still returned.
"""
from __future__ import annotations

import datetime as dt
import glob
import os

from ..cache import DeskCache, fingerprint
from ..db import SourceUnavailable, db_path
from ..repository import DeskRepository
from .data import load_regime_data
from .engine import classify_history, insufficient
from . import model
from .model import MarketRegimeSnapshot

RECENT_SESSIONS = 30            # sessions kept for the dashboard / detail-page history


def history_file(version: str | None = None) -> str:
    version = version or model.CALCULATION_VERSION       # read at call time, never import time
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in version)
    return f"regime_snapshots_{safe}.json"


def input_paths(repo: DeskRepository) -> list:
    paths = []
    for name in ("ohlcv", "market_history"):
        p = db_path(repo.out_dir, name)
        paths += [p, p + "-wal"]
    paths += sorted(glob.glob(repo.path("market_structure", "market_structure_*.json")))
    return paths


def input_key(repo: DeskRepository, version: str | None = None) -> str:
    return f"{version or model.CALCULATION_VERSION}:{fingerprint(input_paths(repo))}"


class RegimeStore:
    def __init__(self, repo: DeskRepository, regime_dir: str):
        self.repo = repo
        self.cache = DeskCache(regime_dir, repo.out_dir)
        self.regime_dir = regime_dir

    def _load(self, key: str) -> dict:
        return self.cache.get(history_file(), key) or {}

    def snapshots(self, end: dt.date, n: int = RECENT_SESSIONS, *, generated_at: str = "") -> list:
        """The `n` canonical sessions up to `end`, oldest first - from the store when the
        version + input fingerprint still match, else recomputed (each from data <= itself)."""
        key = input_key(self.repo)
        payload = self._load(key)
        spine, stored = payload.get("spine") or [], payload.get("snapshots") or {}
        wanted = [d for d in spine if d <= end.isoformat()][-n:]
        if end.isoformat() in spine and all(d in stored for d in wanted):
            return [MarketRegimeSnapshot.from_dict(stored[d]) for d in wanted]   # cache hit
        try:
            data = load_regime_data(self.repo, end)
        except SourceUnavailable as exc:
            return [insufficient(end, str(exc), generated_at)]
        sessions = [d for d in data.sessions if d <= end][-n:]
        missing = [d for d in sessions if d.isoformat() not in stored]
        for s in classify_history(data, missing, generated_at=generated_at):
            stored[s.session_date] = s.to_dict()
        spine = sorted(set(spine) | {d.isoformat() for d in data.sessions})
        self.cache.put(history_file(), key, {"spine": spine, "snapshots": stored})
        return [MarketRegimeSnapshot.from_dict(stored[d.isoformat()]) for d in sessions]

    def snapshot(self, session: dt.date, *, generated_at: str = "") -> MarketRegimeSnapshot:
        snaps = self.snapshots(session, n=1, generated_at=generated_at)
        if not snaps or snaps[-1].session_date != session.isoformat():
            return insufficient(session, f"{session} is not a canonical session in the stored "
                                         "benchmark history", generated_at)
        return snaps[-1]

    def stored_versions(self) -> list:
        return sorted(os.path.basename(p) for p in
                      glob.glob(os.path.join(self.regime_dir, "regime_snapshots_*.json")))


__all__ = ["RegimeStore", "history_file", "input_key", "input_paths", "RECENT_SESSIONS"]

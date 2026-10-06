"""Capture orchestration: one fetch per family, change detection per event_key against what is
already stored, and an immutable revision write-through. Never raises - a family's failure (or a
family this pass never attempts) is recorded as an attempt and the caller moves on, exactly like
`institutional_flows.service` / `official_snapshots.service`."""
from __future__ import annotations

import datetime as dt

from .models import (ALL_FAMILIES, CANCELLED_CHANGE, NEW_EVENT, NOT_SUPPORTED_YET, REVISED,
                     TERMINAL_STATUSES, UNCHANGED, VALIDATED)
from .store import list_events, record_attempt, write_revision


class MarketEventsService:
    def __init__(self, out_dir: str, fetchers: dict | None = None):
        self.out_dir = out_dir
        self._fetchers = dict(fetchers or {})

    def _default_fetchers(self):
        from .sources import DEFAULT_FETCHERS
        for family, fn in DEFAULT_FETCHERS.items():
            self._fetchers.setdefault(family, fn)

    def _store_one(self, family: str, ev, now: dt.datetime, mode: str) -> dict:
        now_iso = now.isoformat()
        existing = {k: (p, s) for k, p, s in list_events(self.out_dir, family)}
        bootstrap = not existing
        prior = existing.get(ev.event_key)
        if ev.status_capture not in VALIDATED:
            record_attempt(self.out_dir, family, mode, now,
                           {"family": family, "event_key": ev.event_key,
                            "status": ev.status_capture, "reason": ev.reason,
                            "retrieved_at": now_iso})
            return {"family": family, "event_key": ev.event_key,
                   "change_state": ev.status_capture, "status": ev.status_capture,
                   "written": False}

        ev.seal()
        if prior is not None:
            prior_path, prior_ev = prior
            if prior_ev.records_checksum_value == ev.records_checksum_value:
                record_attempt(self.out_dir, family, mode, now,
                               {"family": family, "event_key": ev.event_key,
                                "status": "UNCHANGED", "retrieved_at": now_iso})
                return {"family": family, "event_key": ev.event_key, "change_state": UNCHANGED,
                       "status": ev.status_capture, "written": False}
            ev.bootstrap = False
            ev.first_retrieved_at = prior_ev.first_retrieved_at
            change_state = CANCELLED_CHANGE if ev.status in TERMINAL_STATUSES else REVISED
            path = write_revision(self.out_dir, ev)
            record_attempt(self.out_dir, family, mode, now,
                           {"family": family, "event_key": ev.event_key,
                            "status": change_state, "path": path, "retrieved_at": now_iso})
            return {"family": family, "event_key": ev.event_key, "change_state": change_state,
                   "status": ev.status_capture, "written": True, "path": path}

        ev.bootstrap = bootstrap
        ev.first_retrieved_at = now_iso
        path = write_revision(self.out_dir, ev)
        record_attempt(self.out_dir, family, mode, now,
                       {"family": family, "event_key": ev.event_key, "status": "NEW_EVENT",
                        "bootstrap": bootstrap, "path": path, "retrieved_at": now_iso})
        return {"family": family, "event_key": ev.event_key, "change_state": NEW_EVENT,
               "status": ev.status_capture, "written": True, "path": path, "bootstrap": bootstrap}

    def capture(self, now: dt.datetime, mode: str, families: tuple = ALL_FAMILIES) -> dict:
        self._default_fetchers()
        now_iso = now.isoformat()
        results = {}
        for family in families:
            fn = self._fetchers.get(family)
            if fn is None:
                record_attempt(self.out_dir, family, mode, now,
                               {"family": family, "status": NOT_SUPPORTED_YET,
                                "reason": "no fetcher registered for this family"})
                results[family] = {"family": family, "status": NOT_SUPPORTED_YET, "events": []}
                continue
            try:
                res = fn(now_iso)
            except Exception as exc:
                record_attempt(self.out_dir, family, mode, now,
                               {"family": family, "status": "EXCEPTION",
                                "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
                results[family] = {"family": family, "status": "SOURCE_UNAVAILABLE", "events": []}
                continue
            if res.status == NOT_SUPPORTED_YET:
                record_attempt(self.out_dir, family, mode, now,
                               {"family": family, "status": NOT_SUPPORTED_YET,
                                "reason": res.reason})
                results[family] = {"family": family, "status": NOT_SUPPORTED_YET, "events": []}
                continue
            if res.status not in VALIDATED:
                record_attempt(self.out_dir, family, mode, now,
                               {"family": family, "status": res.status, "reason": res.reason,
                                "connectivity": res.connectivity})
                results[family] = {"family": family, "status": res.status, "events": []}
                continue
            stored = [self._store_one(family, ev, now, mode) for ev in res.events]
            results[family] = {"family": family, "status": res.status, "events": stored}
        return {"captured_at": now_iso, "mode": mode, "results": results}


__all__ = ["MarketEventsService"]

"""Capture orchestration: one fetch per source, change detection against what is already
stored, and an immutable revision write-through. Never raises - a source failure is recorded
as an attempt and the caller moves on, exactly like `official_snapshots.service`."""
from __future__ import annotations

import datetime as dt

from .models import CDSL, FAILED, NSDL, NSE, NEW_REPORT, REVISED, UNCHANGED
from .store import list_snapshots, load_revision, record_attempt, write_revision


class InstitutionalFlowService:
    def __init__(self, out_dir: str, nse_fn=None, cdsl_fn=None, nsdl_fn=None):
        self.out_dir = out_dir
        self._nse_fn = nse_fn
        self._cdsl_fn = cdsl_fn
        self._nsdl_fn = nsdl_fn

    def _default_fetchers(self):
        if self._nse_fn is None:
            from .sources.nse import fetch_nse_fii_dii
            self._nse_fn = fetch_nse_fii_dii
        if self._cdsl_fn is None:
            from .sources.cdsl import fetch_cdsl_daily
            self._cdsl_fn = fetch_cdsl_daily
        if self._nsdl_fn is None:
            from .sources.nsdl import fetch_nsdl_fortnightly
            self._nsdl_fn = fetch_nsdl_fortnightly

    def _store_one(self, source: str, snap, now: dt.datetime, mode: str) -> dict:
        now_iso = now.isoformat()
        existing = {k: (p, s) for k, p, s in list_snapshots(self.out_dir, source)}
        bootstrap = not existing
        prior = existing.get(snap.report_key)
        if snap.status not in ("SUCCESS",):
            record_attempt(self.out_dir, source, mode, now,
                           {"source": source, "report_key": snap.report_key,
                            "status": snap.status, "reason": snap.reason,
                            "retrieved_at": now_iso})
            return {"source": source, "report_key": snap.report_key, "change_state": snap.status,
                   "status": snap.status, "reason": snap.reason, "written": False}

        snap.seal()
        if prior is not None:
            prior_path, prior_snap = prior
            if prior_snap.records_checksum_value == snap.records_checksum_value:
                record_attempt(self.out_dir, source, mode, now,
                               {"source": source, "report_key": snap.report_key,
                                "status": "UNCHANGED", "retrieved_at": now_iso})
                return {"source": source, "report_key": snap.report_key,
                       "change_state": UNCHANGED, "status": snap.status, "written": False}
            snap.bootstrap = False
            snap.first_retrieved_at = prior_snap.first_retrieved_at
            path = write_revision(self.out_dir, snap)
            record_attempt(self.out_dir, source, mode, now,
                           {"source": source, "report_key": snap.report_key,
                            "status": "REVISED", "path": path, "retrieved_at": now_iso})
            return {"source": source, "report_key": snap.report_key, "change_state": REVISED,
                   "status": snap.status, "written": True, "path": path}

        snap.bootstrap = bootstrap
        snap.first_retrieved_at = now_iso
        path = write_revision(self.out_dir, snap)
        record_attempt(self.out_dir, source, mode, now,
                       {"source": source, "report_key": snap.report_key, "status": "NEW_REPORT",
                        "bootstrap": bootstrap, "path": path, "retrieved_at": now_iso})
        return {"source": source, "report_key": snap.report_key, "change_state": NEW_REPORT,
               "status": snap.status, "written": True, "path": path, "bootstrap": bootstrap}

    def capture(self, now: dt.datetime, mode: str, sources: tuple = (NSE, CDSL, NSDL)) -> dict:
        self._default_fetchers()
        now_iso = now.isoformat()
        results = {}
        if NSE in sources:
            try:
                snap = self._nse_fn(now_iso)
                results[NSE] = self._store_one(NSE, snap, now, mode)
            except Exception as exc:
                record_attempt(self.out_dir, NSE, mode, now,
                               {"source": NSE, "status": "EXCEPTION",
                                "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
                results[NSE] = {"source": NSE, "change_state": "SOURCE_UNAVAILABLE",
                                "status": "SOURCE_UNAVAILABLE", "written": False}
        if CDSL in sources:
            try:
                snap = self._cdsl_fn(now_iso)
                results[CDSL] = self._store_one(CDSL, snap, now, mode)
            except Exception as exc:
                record_attempt(self.out_dir, CDSL, mode, now,
                               {"source": CDSL, "status": "EXCEPTION",
                                "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
                results[CDSL] = {"source": CDSL, "change_state": "SOURCE_UNAVAILABLE",
                                 "status": "SOURCE_UNAVAILABLE", "written": False}
        if NSDL in sources:
            try:
                res = self._nsdl_fn(now_iso)
                out = {}
                for which in ("latest", "previous"):
                    snap = res.get(which)
                    if snap is not None:
                        out[which] = self._store_one(NSDL, snap, now, mode)
                record_attempt(self.out_dir, NSDL, mode, now,
                               {"source": NSDL, "discovery": res.get("discovery"),
                                "latest": out.get("latest"), "previous": out.get("previous")})
                results[NSDL] = out
            except Exception as exc:
                record_attempt(self.out_dir, NSDL, mode, now,
                               {"source": NSDL, "status": "EXCEPTION",
                                "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
                results[NSDL] = {"latest": {"source": NSDL, "change_state": "SOURCE_UNAVAILABLE",
                                            "status": "SOURCE_UNAVAILABLE", "written": False}}
        return {"captured_at": now_iso, "mode": mode, "results": results}


__all__ = ["InstitutionalFlowService"]

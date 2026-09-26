"""OfficialDailySnapshotService - the one place official daily state is acquired.

    REPORT job (evening of D)          ensure(D, now, REPORT_JOB)
    POST / PRE live run (morning)      ensure(D, now, POST_FALLBACK / PRE_FALLBACK) - only for a
                                       kind with no validated snapshot, only inside D's window
    replay / review render             load(D) only - never acquires

Capture window: a session's official state may be acquired only while it IS the current state -
D must be the latest final session and D's next session must not have opened (09:15 IST). A
past session is never captured from today's pages; its snapshot either was preserved or it is
HISTORICAL_SNAPSHOT_UNAVAILABLE.

Fetch adapters are the existing ones (exchange_watch.sources, ipo_watch.sources), resolved at
call time so a test (or a production job) can inject them. Nothing here uses Gemini.
"""
from __future__ import annotations

import datetime as dt

from . import store
from .models import (ASM, ESM, FNO_BAN, GSM, IPO, NO_DATA, NOT_SUPPORTED, PARSE_ERROR,
                     REPORT_JOB, SOURCE_UNAVAILABLE, SUCCESS, VALIDATED, VALIDATION_FAILED,
                     OfficialSnapshot)

OPEN_TIME = dt.time(9, 15)
SURVEILLANCE_WINDOW_DAYS = 4          # an ASM / GSM list is dated by its own publication day
ESM_REASON = ("no ESM adapter in V1 - never queried (NSE's ESM list is not part of the "
              "implemented Exchange Watch families)")
_CAPTURED = (FNO_BAN, ASM, GSM, IPO)


def _ist_naive(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        return now
    from config import IST
    return now.astimezone(IST).replace(tzinfo=None)


class OfficialDailySnapshotService:
    def __init__(self, out_dir: str, fo_ban_fn=None, surveillance_fn=None, ipo_fn=None,
                 calendar=None):
        self.out_dir = out_dir
        self._fo_ban_fn, self._surv_fn, self._ipo_fn = fo_ban_fn, surveillance_fn, ipo_fn
        from core.trading_calendar import SessionCalendar
        self.calendar = calendar or SessionCalendar()

    # ------------------------------------------------------------------ window
    def expected_list_date(self, session: dt.date) -> dt.date | None:
        from operations.sessions import next_session
        return next_session(session, self.calendar)

    def capture_allowed(self, session: dt.date, now: dt.datetime) -> tuple:
        """(allowed, code, reason). code: CURRENT / HISTORICAL_SESSION / WINDOW_CLOSED /
        NOT_FINAL / CALENDAR_UNKNOWN."""
        from operations.sessions import latest_final_session
        latest = latest_final_session(now, self.calendar)
        nxt = self.expected_list_date(session)
        if latest is None or nxt is None:
            return False, "CALENDAR_UNKNOWN", "the trading calendar cannot place this session"
        if session < latest:
            return False, "HISTORICAL_SESSION", (f"{session} is a past session (latest final: "
                                                 f"{latest}) - today's lists are not its state")
        if session > latest:
            return False, "NOT_FINAL", f"{session} is not final yet"
        if _ist_naive(now) >= dt.datetime.combine(nxt, OPEN_TIME):
            return False, "WINDOW_CLOSED", (f"{nxt} has opened - {session}'s official state can "
                                            "no longer be read as it was")
        return True, "CURRENT", f"{session} is the current session (next: {nxt})"

    # ------------------------------------------------------------------ acquisition
    def ensure(self, session: dt.date, now: dt.datetime, capture_mode: str = REPORT_JOB) -> dict:
        """Capture every kind that has no validated snapshot yet (when the window allows);
        returns the capture summary. Never raises for a source failure."""
        manifest = store.load_manifest(self.out_dir, session) or {}
        have = {k for k, e in (manifest.get("snapshots") or {}).items() if e.get("validated")}
        need = [k for k in _CAPTURED if k not in have]
        allowed, code, why = self.capture_allowed(session, now)
        summary = {"session_date": session.isoformat(), "capture_mode": capture_mode,
                   "window": code, "window_reason": why, "captured": [],
                   "already_validated": sorted(have)}
        if not need:
            summary["capture"] = "NOT_NEEDED"
        elif not allowed:
            summary["capture"] = "REFUSED"
        else:
            summary["capture"] = "ATTEMPTED"
            now_iso = now.astimezone(dt.timezone.utc).isoformat() if now.tzinfo else now.isoformat()
            expected = self.expected_list_date(session)
            for snap in self._acquire(session, now, now_iso, expected, need, capture_mode):
                store.write_revision(self.out_dir, snap)
                summary["captured"].append({"kind": snap.kind, "status": snap.status,
                                            "revision": snap.revision})
        if ESM not in (manifest.get("snapshots") or {}) and (allowed or have):
            self._record_unsupported(session, ESM, ESM_REASON)
        summary["manifest_path"] = store.manifest_path(self.out_dir, session)
        summary["kinds"] = self.status(session)
        return summary

    def _acquire(self, session, now, now_iso, expected, need, mode) -> list:
        from exchange_watch import sources as exs
        from ipo_watch import sources as ips
        out = []
        base = {"session_date": session.isoformat(), "capture_mode": mode,
                "expected_list_date": expected.isoformat() if expected else None}
        if FNO_BAN in need:
            res = (self._fo_ban_fn or exs.fetch_fo_ban)(now_iso)
            out.append(self._exchange_snapshot(FNO_BAN, res, expected, base, exact=True))
        if ASM in need or GSM in need:
            results = list((self._surv_fn or exs.fetch_surveillance)(now_iso))
            by_name = {r.list_name: r for r in results if getattr(r, "list_name", "")}
            if not by_name and len(results) == 1:          # one verdict for the whole client
                by_name = {ASM: results[0], GSM: results[0]}
            elif not by_name and len(results) == 2:
                by_name = {ASM: results[0], GSM: results[1]}
            for kind in (ASM, GSM):
                if kind in need:
                    res = by_name.get(kind)
                    if res is None:
                        out.append(OfficialSnapshot(
                            kind=kind, source_date=None, source_name="nse_surveillance",
                            source_reference="", retrieved_at=now_iso, status=SOURCE_UNAVAILABLE,
                            connectivity_status="NOT_ATTEMPTED",
                            reason="the surveillance fetch returned no result for this list",
                            **base))
                    else:
                        out.append(self._exchange_snapshot(kind, res, expected, base,
                                                           exact=False))
        if IPO in need:
            from config import now_ist
            read_day = _ist_naive(now).date() if now else now_ist().date()
            res = (self._ipo_fn or ips.fetch_nse_issue_lists)(read_day, now_iso)
            out.append(self._ipo_snapshot(res, read_day, now_iso, base))
        return out

    @staticmethod
    def _exchange_snapshot(kind, res, expected, base, exact) -> OfficialSnapshot:
        from exchange_watch.sources import ASM_URL, FO_BAN_URL, GSM_URL
        ref = {FNO_BAN: FO_BAN_URL, ASM: ASM_URL, GSM: GSM_URL}[kind]
        snap = OfficialSnapshot(kind=kind, source_date=None, source_name=res.source_name,
                                source_reference=ref, retrieved_at=res.retrieved_at,
                                status=SUCCESS, connectivity_status=res.connectivity,
                                reason=res.reason, **base)
        if res.status == "UNAVAILABLE":
            snap.status = SOURCE_UNAVAILABLE
            return snap
        if res.status == "INVALID":
            snap.status, snap.connectivity_status = PARSE_ERROR, "REACHABLE"
            return snap
        if res.status == "EMPTY":
            snap.status = NO_DATA            # read fine, nothing listed; no date is invented
            return snap
        snap.source_date = res.list_date.isoformat() if res.list_date else None
        rows = [_exchange_record(kind, e) for e in res.events]
        ok_date = res.list_date is not None and expected is not None and (
            res.list_date == expected if exact
            else 0 <= (expected - res.list_date).days <= SURVEILLANCE_WINDOW_DAYS)
        if not ok_date:
            snap.status = VALIDATION_FAILED
            snap.reason = (f"list dated {res.list_date}, expected "
                           + (f"{expected}" if exact else
                              f"{SURVEILLANCE_WINDOW_DAYS} days up to {expected}")
                           + " - a stale list is never relabelled")
            snap.rejected = rows
            return snap
        snap.records = sorted(rows, key=lambda r: (r["list"], r["symbol"]))
        snap.status = SUCCESS if snap.records else NO_DATA
        return snap

    @staticmethod
    def _ipo_snapshot(res, read_day, now_iso, base) -> OfficialSnapshot:
        from core import sources as S
        from ipo_watch.sources import NSE_IPO_PAGE
        if isinstance(res, tuple):                     # the older (events, notes) shape
            events, notes = res
            unavailable = any("unavailable" in n.lower() for n in notes)
            lists = [{"name": "ALL", "status": "UNAVAILABLE" if unavailable and not events
                      else "OK", "connectivity": "UNKNOWN" if unavailable else "REACHABLE",
                      "reason": "; ".join(notes)[:300]}]
        else:
            events, notes, lists = res["events"], res["notes"], res["lists"]
        snap = OfficialSnapshot(kind=IPO, source_date=read_day.isoformat(),
                                source_name=S.SRC_NSE_IPO, source_reference=NSE_IPO_PAGE,
                                retrieved_at=now_iso, status=SUCCESS,
                                connectivity_status="REACHABLE", reason="; ".join(notes)[:500],
                                **base)
        unreachable = [lst for lst in lists if lst["status"] == "UNAVAILABLE"]
        unparsed = [lst for lst in lists if lst["status"] == "INVALID"]
        if unreachable or unparsed:
            # an incomplete read is never the day's state: stored for diagnosis, never used
            snap.status = SOURCE_UNAVAILABLE if unreachable else PARSE_ERROR
            snap.connectivity_status = (unreachable[0].get("connectivity") or "UNKNOWN"
                                        ) if unreachable else "REACHABLE"
            snap.reason = "; ".join(f"{lst['name']}: {lst['status']} {lst.get('reason', '')}"
                                    for lst in unreachable + unparsed)[:500]
            snap.rejected = [e.to_snapshot_record() for e in events]
            snap.source_date = None
            return snap
        snap.records = sorted((e.to_snapshot_record() for e in events),
                              key=lambda r: ((r.get("symbol") or ""), r["company_name"]))
        snap.status = SUCCESS if snap.records else NO_DATA
        return snap

    def _record_unsupported(self, session: dt.date, kind: str, reason: str) -> None:
        manifest = store.load_manifest(self.out_dir, session)
        if manifest is None:
            return
        manifest["snapshots"][kind] = {"current": None, "validated": False,
                                       "status": NOT_SUPPORTED, "reason": reason,
                                       "connectivity_status": "NOT_ATTEMPTED", "revisions": []}
        store._write_manifest(self.out_dir, session, manifest)

    # ------------------------------------------------------------------ reading
    def load(self, session: dt.date | None) -> dict:
        """{kind: (OfficialSnapshot, file)} for every kind with a stored current revision."""
        if session is None:
            return {}
        out = {}
        for kind in _CAPTURED:
            snap, name = store.load_current(self.out_dir, session, kind)
            if snap is not None:
                out[kind] = (snap, name)
        return out

    def status(self, session: dt.date) -> dict:
        manifest = store.load_manifest(self.out_dir, session) or {}
        return {k: {"status": e.get("status"), "validated": bool(e.get("validated")),
                    "connectivity_status": e.get("connectivity_status"),
                    "record_count": e.get("record_count"), "file": e.get("current"),
                    "checksum": e.get("checksum"), "revisions": len(e.get("revisions") or [])}
                for k, e in (manifest.get("snapshots") or {}).items()}


def _exchange_record(kind: str, e) -> dict:
    lst = e.event_id.split(":")[1] if kind == ASM else kind
    return {"symbol": e.symbol, "company": e.company, "list": lst, "status": e.status,
            "detail": e.detail, "row_date": e.data_as_of.isoformat()}


def rollup(kinds: dict) -> dict:
    """Run-record statuses: official / ipo / exchange + per source (fno/asm/gsm/esm)."""
    def st(k):
        return (kinds.get(k) or {}).get("status") or "NOT_CAPTURED"

    exch = [st(k) for k in (FNO_BAN, ASM, GSM)]
    captured = [s for s in exch + [st(IPO)] if s != "NOT_CAPTURED"]

    def overall(values):
        if not values or all(v == "NOT_CAPTURED" for v in values):
            return "NOT_CAPTURED"
        if all(v in VALIDATED for v in values):
            return SUCCESS
        if any(v in VALIDATED for v in values):
            return "DEGRADED"
        return "FAILED"

    return {"official_snapshot_status": overall(captured),
            "ipo_snapshot_status": st(IPO), "exchange_snapshot_status": overall(exch),
            "fno_status": st(FNO_BAN), "asm_status": st(ASM), "gsm_status": st(GSM),
            "esm_status": st(ESM)}


__all__ = ["OfficialDailySnapshotService", "rollup", "OPEN_TIME", "SURVEILLANCE_WINDOW_DAYS"]

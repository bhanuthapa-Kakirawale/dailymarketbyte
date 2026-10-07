"""PRE's read of institutional flow snapshots: newest stored CDSL/NSDL reports as of a cutoff,
with the replay/live distinction official_snapshots already draws.

A live PRE run may CAPTURE a missing CDSL/NSDL report (persisted for next time); a replay of a
past morning never fetches today's page and pretends it existed then - it only ever reads what
was already on disk, and only what was first captured before `cutoff`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .models import (CAPTURED_THIS_RUN, CDSL, HISTORICAL_SNAPSHOT_UNAVAILABLE, NOT_CAPTURED,
                     NSDL, PERSISTED_SNAPSHOT, InstitutionalFlowSnapshot)


@dataclass
class InstitutionalContext:
    cdsl: InstitutionalFlowSnapshot | None = None
    cdsl_status: str = NOT_CAPTURED
    nsdl_latest: InstitutionalFlowSnapshot | None = None
    nsdl_previous: InstitutionalFlowSnapshot | None = None
    nsdl_status: str = NOT_CAPTURED

    def inputs(self) -> dict:
        def ref(snap):
            if snap is None:
                return None
            return {"report_key": snap.report_key, "status": snap.status,
                   "records_checksum": snap.records_checksum_value,
                   "first_retrieved_at": snap.first_retrieved_at}
        return {"cdsl": {"status": self.cdsl_status, "snapshot": ref(self.cdsl)},
               "nsdl": {"status": self.nsdl_status, "latest": ref(self.nsdl_latest),
                        "previous": ref(self.nsdl_previous)}}


SAME_SESSION_AVAILABLE = "SAME_SESSION_AVAILABLE"
AWAITING_PUBLICATION = "AWAITING_PUBLICATION"      # NSE's latest known figure is an older date
NO_SNAPSHOT = "NO_SNAPSHOT"                        # nothing captured for this source yet


def nse_same_session_status(out_dir: str, session_date: dt.date) -> dict:
    """Is NSE's own provisional FII/FPI + DII figure available FOR `session_date` yet, per the
    newest stored `institutional_flows` NSE snapshot - a read-only, local cross-check used only
    to WORD a POST/PRE omission precisely (never to decide whether to show a number: that
    decision is, and stays, the canonical report's own `institutional_flows` field).

    Distinguishes "NSE has not published today's figure yet" (a normal, expected timing gap
    this pipeline must never paper over with yesterday's number) from "no NSE snapshot has ever
    been captured" - two different operational situations with different explanations."""
    from .models import NSE
    from .store import load_latest
    latest = load_latest(out_dir, NSE)
    if latest is None:
        return {"status": NO_SNAPSHOT, "latest_report_key": None}
    try:
        latest_date = dt.date.fromisoformat(latest.report_key)
    except (TypeError, ValueError):
        return {"status": NO_SNAPSHOT, "latest_report_key": latest.report_key}
    if latest_date >= session_date:
        return {"status": SAME_SESSION_AVAILABLE, "latest_report_key": latest.report_key}
    return {"status": AWAITING_PUBLICATION, "latest_report_key": latest.report_key,
           "sessions_behind": max(0, (session_date - latest_date).days)}


def load_institutional(out_dir: str, *, cutoff: dt.datetime, live: bool,
                       on_or_before: dt.date | None = None,
                       capture_fn=None) -> InstitutionalContext:
    """`cutoff` is an ISO timestamp (brief.as_of): a snapshot counts only if it was FIRST
    retrieved strictly before this point - a report fetched later in the day, even if dated
    earlier, must never appear to have been available at the cutoff. `on_or_before` additionally
    bounds the report's own date/fortnight-end (never a report "from the future" relative to the
    session being built)."""
    from .store import load_latest
    cutoff_iso = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    ctx = InstitutionalContext()

    cdsl = load_latest(out_dir, CDSL, on_or_before=on_or_before,
                       first_retrieved_before=cutoff_iso)
    if cdsl is not None:
        ctx.cdsl, ctx.cdsl_status = cdsl, PERSISTED_SNAPSHOT
    elif live and capture_fn is not None:
        fresh = capture_fn(CDSL)
        if fresh is not None:
            ctx.cdsl, ctx.cdsl_status = fresh, CAPTURED_THIS_RUN
        else:
            ctx.cdsl_status = NOT_CAPTURED
    else:
        ctx.cdsl_status = HISTORICAL_SNAPSHOT_UNAVAILABLE if not live else NOT_CAPTURED

    nsdl_latest = load_latest(out_dir, NSDL, on_or_before=on_or_before,
                              first_retrieved_before=cutoff_iso)
    if nsdl_latest is not None:
        ctx.nsdl_latest, ctx.nsdl_status = nsdl_latest, PERSISTED_SNAPSHOT
        prev = load_latest(out_dir, NSDL, on_or_before=on_or_before,
                           first_retrieved_before=cutoff_iso)
        # second-newest with a report_key strictly before the latest one
        from .store import list_snapshots
        older = [s for k, _p, s in list_snapshots(out_dir, NSDL)
                if s.validated and k < nsdl_latest.report_key]
        ctx.nsdl_previous = older[0] if older else None
    elif live and capture_fn is not None:
        fresh = capture_fn(NSDL)
        if fresh is not None:
            ctx.nsdl_latest, ctx.nsdl_status = fresh, CAPTURED_THIS_RUN
    else:
        ctx.nsdl_status = HISTORICAL_SNAPSHOT_UNAVAILABLE if not live else NOT_CAPTURED

    return ctx


__all__ = ["InstitutionalContext", "load_institutional", "nse_same_session_status",
           "SAME_SESSION_AVAILABLE", "AWAITING_PUBLICATION", "NO_SNAPSHOT"]

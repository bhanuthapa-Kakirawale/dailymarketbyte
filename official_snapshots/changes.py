"""Exchange Watch change detection: the current official snapshot vs the PREVIOUS trading
session's validated snapshot of the same list.

    F&O ban      ENTERED_BAN / REMAINS_IN_BAN / EXITED_BAN
    ASM / GSM    ENTERED / STAGE_CHANGED / UNCHANGED / REMOVED
    no baseline  CHANGE_UNKNOWN - current membership only, never "entered", no exits

The baseline is exactly the calendar's previous session: an older snapshot is never used to
stand in for a missing one (that could call a two-session-old change "today's").
"""
from __future__ import annotations

import datetime as dt

from exchange_watch.models import Change, EventFamily, ExchangeEvent

from .models import ASM, FNO_BAN, GSM

FAMILY = {FNO_BAN: EventFamily.FNO_BAN, ASM: EventFamily.SURVEILLANCE_ASM,
          GSM: EventFamily.SURVEILLANCE_GSM}
_EXIT_STATUS = {FNO_BAN: "NOT IN BAN", ASM: "REMOVED FROM ASM", GSM: "REMOVED FROM GSM"}
_EXIT_DETAIL = {FNO_BAN: "Not on the exchange's F&O ban list for this trade date "
                         "(it was on the previous one)",
                ASM: "Not on NSE's ASM list for this date (it was on the previous one)",
                GSM: "Not on NSE's GSM list for this date (it was on the previous one)"}


def _members(snapshot) -> dict:
    """symbol -> {"status": "a / b", "rows": [...]} (a symbol can sit in two ASM buckets)."""
    out = {}
    for r in snapshot.records:
        m = out.setdefault(r["symbol"], {"rows": [], "statuses": set()})
        m["rows"].append(r)
        m["statuses"].add(r["status"])
    for m in out.values():
        m["status"] = " / ".join(sorted(m["statuses"]))
    return out


def detect(kind: str, current, previous) -> list:
    """[(symbol, change, record)] over the current list plus exits. `previous` is None (or not
    validated) -> CHANGE_UNKNOWN for every current member and no exits."""
    cur = _members(current)
    baseline = previous is not None and previous.validated
    prev = _members(previous) if baseline else {}
    out = []
    for sym, m in sorted(cur.items()):
        rec = dict(m["rows"][0], status=m["status"])
        if not baseline:
            change = Change.CHANGE_UNKNOWN
        elif kind == FNO_BAN:
            change = Change.REMAINS_IN_BAN if sym in prev else Change.ENTERED_BAN
        elif sym not in prev:
            change = Change.ENTERED
        else:
            change = Change.UNCHANGED if prev[sym]["status"] == m["status"] else Change.STAGE_CHANGED
        out.append((sym, change, rec))
    if baseline:
        for sym, m in sorted(prev.items()):
            if sym not in cur:
                out.append((sym, Change.EXITED_BAN if kind == FNO_BAN else Change.REMOVED,
                            dict(m["rows"][0], status=_EXIT_STATUS[kind],
                                 detail=_EXIT_DETAIL[kind])))
    return out


def exchange_events(kind: str, current, previous) -> list:
    """Validated ExchangeEvents for the public planner, dated by the CURRENT list."""
    if kind not in FAMILY or current is None or not current.validated:
        return []
    day = dt.date.fromisoformat(current.source_date or current.session_date)
    out = []
    for sym, change, rec in detect(kind, current, previous):
        exit_ = change in (Change.EXITED_BAN, Change.REMOVED)
        out.append(ExchangeEvent(
            event_id=f"{kind}:{rec['list']}:{day}:{sym}" + (":EXIT" if exit_ else ""),
            family=FAMILY[kind], symbol=sym, company=rec.get("company") or "",
            status=rec["status"], detail=rec.get("detail") or "", data_as_of=day,
            source_name=current.source_name, source_reference=current.source_reference,
            retrieved_at=current.retrieved_at, change=change, validation_status="VALIDATED"))
    return out


def summarize(events) -> dict:
    counts = {}
    for e in events:
        counts[e.change.value] = counts.get(e.change.value, 0) + 1
    return counts


__all__ = ["detect", "exchange_events", "summarize", "FAMILY"]

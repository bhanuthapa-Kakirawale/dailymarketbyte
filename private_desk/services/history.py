"""Radar history explorer: recorded candidates over a session range, filterable.

Counts only how often something APPEARED - no forward returns, no hit rates (Phase 2).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..repository import DeskRepository
from . import candidates as cs


@dataclass
class HistoryFilter:
    start: dt.date | None = None
    end: dt.date | None = None
    symbol: str = ""
    sector: str = ""
    attention: str = ""
    family: str = ""
    reason: str = ""
    novelty: str = ""
    appearance: str = ""


def explore(repo: DeskRepository, f: HistoryFilter, spine: list) -> dict:
    sessions = repo.radar_sessions()
    if not sessions:
        return {"rows": [], "sessions": [], "summary": [], "filter": f}
    end = f.end or sessions[-1]
    start = f.start or (sessions[-20] if len(sessions) >= 20 else sessions[0])
    all_rows = repo.candidates_between(dt.date(1900, 1, 1), end)
    novelty = cs.classify_recorded(spine, all_rows)
    dates_by_symbol: dict = {}
    for r in all_rows:
        dates_by_symbol.setdefault(r.instrument, []).append(r.session_date)
    universe, _ = repo.universe(end)
    selections = repo.selections_between(start, end)

    rows = []
    for r in all_rows:
        if not (start <= r.session_date <= end):
            continue
        sector = (universe.get(r.instrument) or {}).get("sector")
        n = novelty.get((r.session_date, r.instrument))
        app = cs.appearance(spine, dates_by_symbol[r.instrument], r.session_date)
        row = {"session": r.session_date, "symbol": r.instrument, "sector": sector,
               "attention_level": r.attention_level, "families": list(r.active_families),
               "reason_codes": list(r.reason_codes), "direction": r.direction_compatibility,
               "persistence": r.persistence_state, "price_change_pct": r.price_change_pct,
               "novelty_type": n.novelty_type.value if n else None,
               "appearance": app["label"], "sessions_since_prior": app["sessions_since"],
               "selected": (r.session_date, r.instrument) in selections}
        if f.symbol and f.symbol.upper() not in r.instrument:
            continue
        if f.sector and sector != f.sector:
            continue
        if f.attention and r.attention_level != f.attention:
            continue
        if f.family and f.family not in r.active_families:
            continue
        if f.reason and f.reason not in r.reason_codes:
            continue
        if f.novelty and row["novelty_type"] != f.novelty:
            continue
        if f.appearance and row["appearance"] != f.appearance:
            continue
        rows.append(row)
    rows.sort(key=lambda x: (x["session"], x["symbol"]), reverse=True)

    summary: dict = {}
    for row in rows:
        s = summary.setdefault(row["symbol"], {"symbol": row["symbol"], "sector": row["sector"],
                                               "count": 0, "first": row["session"],
                                               "last": row["session"], "selected": 0})
        s["count"] += 1
        s["first"] = min(s["first"], row["session"])
        s["last"] = max(s["last"], row["session"])
        s["selected"] += 1 if row["selected"] else 0
    window_sessions = [d for d in sessions if start <= d <= end]
    return {"rows": rows, "sessions": sessions, "window_sessions": window_sessions,
            "start": start, "end": end, "filter": f,
            "summary": sorted(summary.values(), key=lambda s: (-s["count"], s["symbol"])),
            "sectors": sorted({(c or {}).get("sector") for c in universe.values()} - {None})}


__all__ = ["HistoryFilter", "explore"]

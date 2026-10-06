"""Deterministic, pure per-sector FPI flow direction and public bar selection, from one NSDL
`InstitutionalFlowSnapshot`'s own current/previous fortnight columns - no network, no model."""
from __future__ import annotations

from dataclasses import dataclass

from .sector_map import NOT_A_SECTOR, UNMAPPED, sector_for_nsdl

INFLOW_CONTINUES = "INFLOW_CONTINUES"
OUTFLOW_CONTINUES = "OUTFLOW_CONTINUES"
REVERSAL_TO_INFLOW = "REVERSAL_TO_INFLOW"
REVERSAL_TO_OUTFLOW = "REVERSAL_TO_OUTFLOW"
NO_MEANINGFUL_CHANGE = "NO_MEANINGFUL_CHANGE"

_NOISE_FLOOR_CR = 100.0
SELECTION_FLOOR_CR = 500.0
CHANGE_FLOOR_CR = 1000.0
MAX_BARS = 4


@dataclass(frozen=True)
class SectorFlow:
    nsdl_sector: str
    display_sector: str              # "" if UNMAPPED/NOT_A_SECTOR
    mapping: str                     # display sector, UNMAPPED, or NOT_A_SECTOR
    current_cr: float
    previous_cr: float
    change_cr: float
    direction: str


def sector_flows(snapshot) -> list:
    """One `SectorFlow` per NSDL sector row with both a current and a previous "equity, net
    investment" fact - Sovereign/Others/Grand Total are never included (NOT_A_SECTOR)."""
    by_sector: dict = {}
    for f in snapshot.facts:
        if f["metric"] != "net_investment" or f["category"] != "equity":
            continue
        by_sector.setdefault(f["sector"], {})[f["period"]] = f["value"]
    out = []
    for sector, periods in by_sector.items():
        mapping = sector_for_nsdl(sector)
        if mapping == NOT_A_SECTOR:
            continue
        if "current" not in periods or "previous" not in periods:
            continue
        cur, prev = periods["current"], periods["previous"]
        change = cur - prev
        if abs(cur) < _NOISE_FLOOR_CR and abs(prev) < _NOISE_FLOOR_CR:
            direction = NO_MEANINGFUL_CHANGE
        elif cur >= 0 and prev >= 0:
            direction = INFLOW_CONTINUES
        elif cur < 0 and prev < 0:
            direction = OUTFLOW_CONTINUES
        elif cur >= 0 and prev < 0:
            direction = REVERSAL_TO_INFLOW
        else:
            direction = REVERSAL_TO_OUTFLOW
        out.append(SectorFlow(nsdl_sector=sector,
                              display_sector=mapping if mapping not in (UNMAPPED,) else "",
                              mapping=mapping, current_cr=cur, previous_cr=prev,
                              change_cr=change, direction=direction))
    return out


def select_public_bars(flows: list) -> list:
    """Up to `MAX_BARS` sectors for a public "FPI sector flow" scene: the largest net inflow and
    the largest net outflow (each needing |current| >= SELECTION_FLOOR_CR), then up to two more
    by largest |change| >= CHANGE_FLOOR_CR. Ties broken alphabetically by sector name. Returns
    [] if nothing qualifies - never pads with an immaterial sector."""
    candidates = [f for f in flows if f.mapping not in (UNMAPPED, NOT_A_SECTOR)]
    picked, seen = [], set()

    inflow = [f for f in candidates if f.current_cr >= SELECTION_FLOOR_CR]
    if inflow:
        top_in = sorted(inflow, key=lambda f: (-f.current_cr, f.display_sector))[0]
        picked.append(top_in)
        seen.add(top_in.display_sector)

    outflow = [f for f in candidates if f.current_cr <= -SELECTION_FLOOR_CR
              and f.display_sector not in seen]
    if outflow:
        top_out = sorted(outflow, key=lambda f: (f.current_cr, f.display_sector))[0]
        picked.append(top_out)
        seen.add(top_out.display_sector)

    rest = sorted((f for f in candidates if f.display_sector not in seen
                  and abs(f.change_cr) >= CHANGE_FLOOR_CR),
                 key=lambda f: (-abs(f.change_cr), f.display_sector))
    for f in rest:
        if len(picked) >= MAX_BARS:
            break
        picked.append(f)
        seen.add(f.display_sector)
    return picked[:MAX_BARS]


__all__ = ["SectorFlow", "sector_flows", "select_public_bars", "INFLOW_CONTINUES",
           "OUTFLOW_CONTINUES", "REVERSAL_TO_INFLOW", "REVERSAL_TO_OUTFLOW",
           "NO_MEANINGFUL_CHANGE", "SELECTION_FLOOR_CR", "CHANGE_FLOOR_CR", "MAX_BARS"]

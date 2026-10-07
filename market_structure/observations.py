"""Per-constituent observations underneath every Market Structure statistic.

One `StructureObservation` per constituent of the universe, built from the SAME detector
outputs the Radar uses (volume / technical snapshots and the session-aligned OHLC series) -
never a second, simplified detector. These are PRIVATE: they name securities and stay in the
internal artifact. Public output only ever sees their counts.

Coverage is per metric: a constituent the volume detector could not read is "not covered" for
unusual volume, and is never silently counted as "no unusual volume".
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

UNUSUAL_VOLUME_RVOL = 2.0      # the volume detector's own lowest anomaly threshold (ELEVATED)
RANGE_UP_EVENTS = ("BREAK_ABOVE_20D_RANGE", "BREAK_ABOVE_50D_RANGE")
RANGE_DOWN_EVENTS = ("BREAK_BELOW_20D_RANGE", "BREAK_BELOW_50D_RANGE")


@dataclass(frozen=True)
class StructureObservation:
    observation_id: str
    symbol: str
    session_date: str
    universe: str
    sector: str
    volume_covered: bool
    unusual_volume: bool
    relative_volume: float | None
    technical_covered: bool
    range_up: bool
    range_down: bool
    breadth_covered: bool
    price_change_pct: float | None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StructureObservation":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


def _event_names(structure) -> set:
    return {getattr(e.event_type, "value", e.event_type) for e in (structure.events or [])}


def _dated_change(rows, session_date: dt.date, prev_date: dt.date | None):
    """Day-over-day % change only when the last two rows ARE the session and the canonical
    previous session (CLAUDE.md: never `iloc[-1]/iloc[-2]` without checking the dates)."""
    if not rows or len(rows) < 2:
        return None
    last, prev = rows[-1], rows[-2]
    d_last = last.get("date") if isinstance(last, dict) else None
    d_prev = prev.get("date") if isinstance(prev, dict) else None
    if hasattr(d_last, "date") and not isinstance(d_last, dt.date):
        d_last = d_last.date()
    if isinstance(d_last, str):
        d_last = dt.date.fromisoformat(d_last[:10])
    if isinstance(d_prev, str):
        d_prev = dt.date.fromisoformat(d_prev[:10])
    if d_last != session_date or (prev_date is not None and d_prev != prev_date):
        return None
    pc, cc = prev.get("close"), last.get("close")
    if not pc or cc is None:
        return None
    return (float(cc) / float(pc) - 1) * 100


def build_observations(universe_def, session_date: dt.date, prev_date: dt.date | None,
                       volume_snapshot, technical_snapshot, series_by_symbol: dict) -> list:
    """One observation per constituent of `universe_def` - and only for those. A symbol the
    detectors read that is not a constituent is ignored here (never mixed in)."""
    vol_scanned = set(getattr(volume_snapshot, "scanned", []) or [])
    rvol = {a.instrument: a.relative_volume for a in getattr(volume_snapshot, "anomalies", []) or []}
    tech_scanned = set(getattr(technical_snapshot, "scanned", []) or [])
    events = {s.instrument: _event_names(s) for s in getattr(technical_snapshot, "flagged", []) or []}
    out = []
    for sym in sorted(universe_def.constituents):
        c = universe_def.constituents[sym]
        ev = events.get(sym, set())
        rv = rvol.get(sym)
        chg = _dated_change(series_by_symbol.get(sym), session_date, prev_date)
        out.append(StructureObservation(
            observation_id=f"{session_date.isoformat()}:{universe_def.index}:{sym}", symbol=sym,
            session_date=session_date.isoformat(), universe=universe_def.index, sector=c.sector,
            volume_covered=sym in vol_scanned,
            unusual_volume=sym in vol_scanned and rv is not None and rv >= UNUSUAL_VOLUME_RVOL,
            relative_volume=round(float(rv), 4) if rv is not None else None,
            technical_covered=sym in tech_scanned,
            range_up=sym in tech_scanned and bool(ev & set(RANGE_UP_EVENTS)),
            range_down=sym in tech_scanned and bool(ev & set(RANGE_DOWN_EVENTS)),
            breadth_covered=chg is not None,
            price_change_pct=round(chg, 4) if chg is not None else None))
    return out


__all__ = ["StructureObservation", "build_observations", "UNUSUAL_VOLUME_RVOL",
           "RANGE_UP_EVENTS", "RANGE_DOWN_EVENTS"]

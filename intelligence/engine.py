"""Builds the IntelligenceSnapshot from a canonical report plus canonical history.

Two hard boundaries:

* **No acquisition.** Nothing here calls a provider, a website or a model. The only inputs
  are today's MarketReport and what is already in MarketHistory, which is what makes the
  output reproducible rather than merely plausible.
* **No mutation.** The canonical report and its rows are read and never written. The
  snapshot is derived data, written as its own artifact, regenerable at any time from the
  canonical records it cites.

Failure degrades rather than fabricates: if history cannot be read, the snapshot comes back
empty with a warning and the pipeline continues, because historical context is an
enhancement and the day's report stands on its own without it.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from . import flows, market_context, movers, sectors, volatility
from .history import DEFAULT_WINDOW_SESSIONS, HistoricalWindow, HistoryUnavailable
from .models import IntelligenceSnapshot

ARTIFACT_DIR = "intelligence"

# Order is presentational only - selection is by unusualness, not by position here.
ANALYSERS = (
    ("index_move", market_context.analyse),
    ("institutional_flow", flows.analyse),
    ("volatility", volatility.analyse),
    ("sector_persistence", sectors.analyse),
    ("mover_recurrence", movers.analyse_recurrence),
    ("relative_volume", movers.analyse_relative_volume),
)


def build_snapshot(report, history, now: dt.datetime | None = None,
                   window_sessions: int = DEFAULT_WINDOW_SESSIONS,
                   include_demo: bool = False) -> IntelligenceSnapshot:
    """Derive historical context for `report`. Always returns a valid snapshot.

    `now` is injectable so a snapshot can be byte-identical across runs in tests; everything
    else is a pure function of the report and the database.
    """
    session_date = report.session_date or report.report_date
    snapshot = IntelligenceSnapshot(
        report_id=report.report_id, session_date=session_date,
        historical_cutoff=session_date,
        generated_at=now or dt.datetime.now(dt.timezone.utc))

    if history is None:
        snapshot.warnings.append("no historical database available; "
                                 "intelligence limited to the current report")
        return snapshot

    window = HistoricalWindow(history, session_date, report_id=report.report_id,
                              window_sessions=window_sessions, include_demo=include_demo)
    try:
        snapshot.available_history = window.depth()
    except HistoryUnavailable as exc:
        snapshot.warnings.append(f"historical database unreadable: {exc}")
        return snapshot

    if snapshot.available_history == 0:
        snapshot.warnings.append("no prior canonical sessions are available yet; "
                                 "historical context will appear as history accumulates")

    for name, analyse in ANALYSERS:
        try:
            snapshot.insights.extend(analyse(report, window))
        except HistoryUnavailable as exc:
            # A storage failure mid-analysis: record it and keep the insights already
            # derived. Never substitute a guess for a reading that could not be taken.
            snapshot.warnings.append(f"{name}: historical database unreadable: {exc}")
        except Exception as exc:
            snapshot.warnings.append(f"{name}: skipped after {type(exc).__name__}: {exc}")

    snapshot.warnings.extend(w for w in window.warnings if w not in snapshot.warnings)
    snapshot.metrics = {
        "available_sessions": snapshot.available_history,
        "insights_computed": len(snapshot.insights),
        "insights_displayable": len(snapshot.displayable()),
        "window_sessions": window_sessions,
    }
    return snapshot


def save_snapshot(snapshot: IntelligenceSnapshot, out_dir: str, demo: bool = False) -> str:
    """Write the derived artifact. It cites the canonical report; it never replaces it."""
    directory = os.path.join(out_dir, ARTIFACT_DIR)
    os.makedirs(directory, exist_ok=True)
    suffix = "_DEMO" if demo else ""
    stamp = (snapshot.session_date or snapshot.historical_cutoff or dt.date.today())
    path = os.path.join(directory, f"intelligence_{stamp:%Y-%m-%d}{suffix}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(snapshot.to_json())
    return path


def describe(snapshot: IntelligenceSnapshot) -> str:
    return (f"{len(snapshot.insights)} insights "
            f"({len(snapshot.displayable())} usable) over "
            f"{snapshot.available_history} prior sessions")


__all__ = ["build_snapshot", "save_snapshot", "describe", "ANALYSERS", "ARTIFACT_DIR"]

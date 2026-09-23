"""Artifact writer for radar snapshots. Mirrors `intelligence/engine.py::save_snapshot`.

Not wired into `main.py` - this packet adds no orchestration call. A caller (a test, a manual
invocation, or a future packet) builds a `MarketReport` and `MarketHistory`, calls
`radar.volume.scan_universe` / `radar.technical.scan_technical_universe`, and optionally
persists the result here.
"""
from __future__ import annotations

import datetime as dt
import os

from .models import TechnicalRadarSnapshot, VolumeRadarSnapshot

ARTIFACT_DIR = "radar"


def _write(snapshot, out_dir: str, prefix: str, demo: bool) -> str:
    directory = os.path.join(out_dir, ARTIFACT_DIR)
    os.makedirs(directory, exist_ok=True)
    suffix = "_DEMO" if demo else ""
    stamp = snapshot.session_date or dt.date.today()
    path = os.path.join(directory, f"{prefix}_{stamp:%Y-%m-%d}{suffix}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(snapshot.to_json())
    return path


def save_snapshot(snapshot: VolumeRadarSnapshot, out_dir: str, demo: bool = False) -> str:
    """Write the derived artifact to output/radar/volume_YYYY-MM-DD[_DEMO].json."""
    return _write(snapshot, out_dir, "volume", demo)


def save_technical_snapshot(snapshot: TechnicalRadarSnapshot, out_dir: str, demo: bool = False) -> str:
    """Write the derived artifact to output/radar/technical_YYYY-MM-DD[_DEMO].json."""
    return _write(snapshot, out_dir, "technical", demo)


__all__ = ["save_snapshot", "save_technical_snapshot", "ARTIFACT_DIR"]

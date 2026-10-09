"""Read-only PK-D status: the latest PRE/POST production run. No acquisition, no readiness
evaluation, no rendering - this command (and the Private Desk panel that reuses it) only reads
`output/production_runs/{PRE,POST}/latest.json`.
"""
from __future__ import annotations

from . import manifest as MF
from .models import EDITIONS


def status(out_dir: str) -> dict:
    """{"PRE": {...} | None, "POST": {...} | None} - each value is the latest.json pointer, or
    None if that edition has never produced a run."""
    return {edition: MF.read_latest(out_dir, edition) for edition in EDITIONS}


__all__ = ["status"]

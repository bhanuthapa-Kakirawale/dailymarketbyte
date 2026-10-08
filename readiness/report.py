"""The readiness report on disk: `<OUT_DIR>/readiness/readiness_<PRE|POST>_<session>.json`.

One file per edition + session, overwritten by the next LIVE evaluation (`evaluated_at` inside)
- the latest answer to "why did PRE not run / why was POST degraded", without file sprawl.
REPLAY (`--as-of`) evaluations are diagnostics and are not written.
"""
from __future__ import annotations

import json
import os

READINESS_DIR = "readiness"


def report_path(out_dir: str, result) -> str:
    stage = "" if result.stage == "PREFLIGHT" else "_post_render"
    session = result.session_date.isoformat() if result.session_date else "unknown"
    return os.path.join(out_dir, READINESS_DIR,
                        f"readiness_{result.edition}_{session}{stage}.json")


def write_report(result, out_dir: str) -> str:
    from operations.run_context import guard_write
    path = report_path(out_dir, result)
    guard_write(path, "readiness report")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return path


__all__ = ["READINESS_DIR", "report_path", "write_report"]

"""Read-only `status` command: reports the last `refresh` run's recorded outcome. Never calls
any repair/fetch function, never acquires the lock, never writes - mirrors `private_desk.
services.production_runs`'s read-only posture and `operations.daily_check`'s aligned-rows +
one VERDICT line.
"""
from __future__ import annotations

from . import manifest, models


def _line(label: str, value) -> str:
    return f"  {label:<30} {value}"


def run_status(*, out_dir: str | None = None, json_output: bool = False) -> tuple:
    """-> (exit_code, text_or_dict). Caller decides how to print/return it."""
    import config
    out_dir = out_dir or config.OUT_DIR
    latest = manifest.read_latest(out_dir)
    if latest is None:
        if json_output:
            return 30, {"status": "NO_RUN"}
        return 30, "No intelligence_refresh run recorded yet.\nVERDICT: NO_RUN"

    status = latest.get("orchestrator_status") or "UNKNOWN"
    if status in models.OK_STATUSES:
        verdict, exit_code = "OK", 0
    elif status == models.BLOCKED:
        verdict, exit_code = "BLOCKED", 20
    else:
        verdict, exit_code = "FAILED", 30

    if json_output:
        return exit_code, latest

    lines = [f"Last run: {latest.get('run_id')}  status={status}",
             f"Started:  {latest.get('started_at')}",
             f"Completed: {latest.get('completed_at')}"]
    lsr = latest.get("latest_session_result") or {}
    lines.append(_line("Latest session", f"{lsr.get('session')}  action={lsr.get('action')}"))
    summary = latest.get("summary") or {}
    for key in ("sessions_audited", "sessions_fully_recoverable",
               "sessions_partially_recoverable"):
        if key in summary:
            lines.append(_line(key, summary[key]))
    counts = summary.get("component_counts") or {}
    for k, v in sorted(counts.items()):
        lines.append(_line(f"  {k}", v))
    for b in latest.get("blocking_reasons") or []:
        lines.append(f"  BLOCKING {b}")
    for w in latest.get("warnings") or []:
        lines.append(f"  WARN {w}")
    lines.append(f"VERDICT: {verdict}")
    return exit_code, "\n".join(lines)


__all__ = ["run_status"]

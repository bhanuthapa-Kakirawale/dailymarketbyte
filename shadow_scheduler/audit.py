"""shadow_scheduler.audit: READ-ONLY replay of existing run records under the CURRENT
occurrence rule (PK-OPS-1, 2026-10-24).

    python -m shadow_scheduler audit [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]

For every run record (filtered by its IST invocation date) it reports what the runner recorded
at the time (status, the window it compared itself against, whether a child launched) next to
what `config.resolve_occurrence` says now (scheduled_date, IN_WINDOW/EARLY/LATE), and a verdict:

    IN_WINDOW                 the invocation was inside its own occurrence's window
    CORRECTLY_REJECTED        out of window and no child was launched
    OUT_OF_WINDOW_EXECUTION   out of window but the (pre-fix) runner LAUNCHED the child - the
                              2026-10-21 06:48 / 2026-10-24 08:29 defect; the current runner
                              rejects the equivalent invocation (EARLY or EXPIRED)
    OUT_OF_WINDOW_ATTEMPT     out of window, the pre-fix runner got as far as a RUNNING /
                              WAITING_DEPENDENCY checkpoint (the child never provably ran)

plus the orphan classification of every non-terminal record. No write-mode open, no
os.replace, no process signal - it never changes a record, a lock or a task.
"""
from __future__ import annotations

import datetime as dt

from . import config, orphans, records
from .models import NON_TERMINAL_STATUSES

IN_WINDOW_V = "IN_WINDOW"
CORRECTLY_REJECTED = "CORRECTLY_REJECTED"
OUT_OF_WINDOW_EXECUTION = "OUT_OF_WINDOW_EXECUTION"
OUT_OF_WINDOW_ATTEMPT = "OUT_OF_WINDOW_ATTEMPT"


def _ist(value):
    parsed = orphans._parse_utc(value)
    return config.to_ist(parsed) if parsed else None


def audit(out_dir: str | None = None, *, date_from: dt.date | None = None,
          date_to: dt.date | None = None, now: dt.datetime | None = None,
          probe=None, business_root: str | None = None) -> dict:
    out_dir = out_dir or config.shadow_out_dir()
    if business_root is None:
        import config as root_config
        business_root = root_config.OUT_DIR
    rows = []
    for rec in records.list_records(out_dir):
        started_ist = _ist(rec.get("started_at"))
        if started_ist is None:
            continue
        d = started_ist.date()
        if (date_from and d < date_from) or (date_to and d > date_to):
            continue
        spec = config.JOB_REGISTRY.get(rec.get("job"))
        if spec is None:
            continue
        occ = config.resolve_occurrence(spec, orphans._parse_utc(rec["started_at"]))
        status = rec.get("runner_status")
        child_started = rec.get("child_started", True)
        if occ.invocation == config.IN_WINDOW:
            verdict = IN_WINDOW_V
        elif status in NON_TERMINAL_STATUSES:
            verdict = OUT_OF_WINDOW_ATTEMPT
        elif child_started is False:
            verdict = CORRECTLY_REJECTED
        else:
            verdict = OUT_OF_WINDOW_EXECUTION
        row = {
            "run_id": rec.get("run_id"), "job": rec.get("job"),
            "invoked_at_ist": started_ist.isoformat(timespec="seconds"),
            "invoked_weekday": started_ist.strftime("%a"),
            "recorded_status": status, "child_exit_code": rec.get("child_exit_code"),
            "child_started": child_started,
            "recorded_window_start": rec.get("window_start"),
            "recorded_scheduled_date": rec.get("scheduled_date"),
            "attributed_scheduled_date": occ.scheduled_date.isoformat(),
            "attributed_window": [occ.window_start.isoformat(), occ.window_end.isoformat()],
            "invocation": occ.invocation,
            "current_runner_would": {config.IN_WINDOW: "PROCEED (subject to dependency/lock)",
                                     config.EARLY: "REJECT as EARLY (no child)",
                                     config.LATE: "REJECT as EXPIRED (no child)"}[occ.invocation],
            "verdict": verdict,
        }
        if status in NON_TERMINAL_STATUSES:
            v = orphans.classify(rec, out_dir=out_dir, now=now, probe=probe,
                                 business_root=business_root)
            row["orphan"] = {"classification": v["classification"],
                             "abandoned_kind": v.get("abandoned_kind"),
                             "reasons": v["reasons"]}
        rows.append(row)
    rows.sort(key=lambda r: r["invoked_at_ist"])
    counts: dict = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {"rows": rows, "counts": counts,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None}


def _child_flag(row: dict) -> str:
    if row["recorded_status"] in NON_TERMINAL_STATUSES:
        return "?"   # a checkpoint's child_started is only its default, never evidence
    return "Y" if row["child_started"] else "N"


def render_human(result: dict) -> str:
    lines = ["DMB SHADOW SCHEDULER - WINDOW AUDIT (read-only)", "-" * 78]
    for r in result["rows"]:
        flag = "" if r["verdict"] in (IN_WINDOW_V, CORRECTLY_REJECTED) else "  <-- "
        lines.append(f"{r['invoked_at_ist']} {r['invoked_weekday']} {r['job']:<16} "
                     f"{r['recorded_status']:<19} child={_child_flag(r)} "
                     f"-> {r['attributed_scheduled_date']} {r['invocation']:<9} "
                     f"{r['verdict']}{flag}")
        if r["verdict"] not in (IN_WINDOW_V, CORRECTLY_REJECTED):
            lines.append(f"{'':<26}recorded window {r['recorded_window_start']}; "
                         f"current runner would {r['current_runner_would']}")
        if "orphan" in r:
            lines.append(f"{'':<26}orphan check: {r['orphan']['classification']} "
                         f"({r['orphan']['abandoned_kind'] or '-'})")
    lines.append("-" * 78)
    for k, v in sorted(result["counts"].items()):
        lines.append(f"{k:<26} {v}")
    return "\n".join(lines)


__all__ = ["IN_WINDOW_V", "CORRECTLY_REJECTED", "OUT_OF_WINDOW_EXECUTION",
          "OUT_OF_WINDOW_ATTEMPT", "audit", "render_human"]

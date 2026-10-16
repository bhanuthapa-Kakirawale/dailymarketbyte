"""Read-only PRE degradation audit (PK-E packet, 2026-10-16 "audit first" directive).

Walks the last N `output/readiness/readiness_PRE_<date>.json` snapshots (PK-C's own
`ReadinessResult.to_dict()` dumps - never recomputed here) and groups every non-PASS
check into a normalized cause so recurring causes can be tallied across real history,
instead of reacting to one day's warning at a time.

Zero network calls, zero writes, zero render calls: every function here only calls
`open()` in read mode and `glob.glob()`. `tests/test_production_quality_pre_degradation.py`
enforces this behaviorally (a guarded `open` that raises on write mode) the same way
`shadow_scheduler.status` is enforced.

The human classification (EXPECTED_SOURCE_DELAY / REAL_SOURCE_FAILURE / MISSING_CAPTURE /
IMPLEMENTATION_GAP / STALE_BY_INCORRECT_FRESHNESS_POLICY / UNKNOWN) is NOT produced by this
module - it groups/counts/cross-references normalized causes only; a human reads the output
and writes the classification up by hand after tracing the actual code, per the packet's
"do not classify automatically from keyword matching" instruction.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
from dataclasses import dataclass, field

READINESS_SUBDIR = "readiness"
REPORT_JOBS_SUBDIR = "report_jobs"

# Checks whose SKIP is pure collateral from an earlier REQUIRED check's own FAIL (e.g.
# BENCHMARK/EDITORIAL_PLAN/PUBLICATION all SKIP once CANONICAL_REPORT fails) - counting
# these as independent causes would just multiply one real root cause into five rows. Only
# FAIL/WARN rows are ever treated as an independent cause; a SKIP is never one by itself.
_COUNTED_STATUSES = frozenset({"FAIL", "WARN"})


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def list_pre_readiness_files(out_dir: str, limit: int) -> list[tuple[str, str]]:
    """[(session_date_iso, path)], newest session first, at most `limit` entries - or every
    one on disk if fewer than `limit` exist."""
    pattern = os.path.join(out_dir, READINESS_SUBDIR, "readiness_PRE_*.json")
    rows = []
    for path in glob.glob(pattern):
        name = os.path.basename(path)
        date_part = name[len("readiness_PRE_"):-len(".json")]
        try:
            dt.date.fromisoformat(date_part)
        except ValueError:
            continue
        rows.append((date_part, path))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows[:limit]


def _cause_for_check(check: dict) -> str | list[str] | None:
    """One normalized cause id for a non-PASS/non-SKIP check, or a list of cause ids when a
    single check fans out into several independently-meaningful sub-causes (INSTITUTIONAL_FLOW
    carries three sources in one check row). Returns None for anything not worth counting."""
    status = check.get("status")
    if status not in _COUNTED_STATUSES:
        return None
    check_id = check.get("check_id") or "UNKNOWN"
    source_health = check.get("source_health")

    if check_id == "INSTITUTIONAL_FLOW":
        observed = check.get("observed") or {}
        causes = []
        for source, detail in observed.items():
            health = (detail or {}).get("health")
            if health and health != "HEALTHY":
                causes.append(f"INSTITUTIONAL_FLOW.{source}:{health}")
        return causes or [f"INSTITUTIONAL_FLOW:{source_health or status}"]

    if check_id == "CANONICAL_REPORT":
        observed = check.get("observed")
        return f"CANONICAL_REPORT:{observed}" if observed else f"CANONICAL_REPORT:{status}"

    return f"{check_id}:{source_health or status}"


@dataclass
class PreDegradationReport:
    runs_analyzed: int
    sessions: list[str] = field(default_factory=list)              # newest first
    status_counts: dict[str, int] = field(default_factory=dict)
    cause_counts: dict[str, int] = field(default_factory=dict)
    cause_dates: dict[str, list[str]] = field(default_factory=dict)
    market_events_missing_sessions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "runs_analyzed": self.runs_analyzed,
            "sessions": self.sessions,
            "status_counts": self.status_counts,
            "cause_counts": dict(sorted(self.cause_counts.items(),
                                        key=lambda kv: kv[1], reverse=True)),
            "cause_dates": self.cause_dates,
            "market_events_missing_sessions": self.market_events_missing_sessions,
        }


def _report_job_runs_for_session(out_dir: str, session: str) -> list[dict]:
    """Every REPORT-job run record targeting `session`, oldest first - read-only, used only
    to show (for a MARKET_EVENTS MISSING cause) whether a later successful attempt existed
    that the PRE cutoff simply hadn't seen yet, vs. no successful attempt existing at all."""
    session_dir = os.path.join(out_dir, REPORT_JOBS_SUBDIR, session)
    runs = []
    for path in sorted(glob.glob(os.path.join(session_dir, "report_job_*.json"))):
        rec = _read_json(path)
        if rec is None:
            continue
        runs.append({
            "file": os.path.basename(path),
            "run_status": rec.get("run_status"),
            "started_at": rec.get("started_at"),
            "completed_at": rec.get("completed_at"),
            "has_market_events": bool((rec.get("details") or {}).get("market_events")),
        })
    runs.sort(key=lambda r: r.get("started_at") or "")
    return runs


def analyze_pre_degradation(out_dir: str, runs: int = 20) -> PreDegradationReport:
    """Read-only: no network call, no write, no render. `out_dir` defaults to the real
    `output/` root at the call site, exactly like every other read-only diagnostic in this
    repo (`validate_readiness.py`, `validate_pre_production.py --connectivity-only`)."""
    files = list_pre_readiness_files(out_dir, runs)
    report = PreDegradationReport(runs_analyzed=len(files))

    for session_date, path in files:
        data = _read_json(path)
        if data is None:
            continue
        report.sessions.append(session_date)
        overall = data.get("overall_status") or "UNKNOWN"
        report.status_counts[overall] = report.status_counts.get(overall, 0) + 1

        market_events_missing = False
        for check in data.get("checks") or []:
            cause = _cause_for_check(check)
            if cause is None:
                continue
            cause_list = cause if isinstance(cause, list) else [cause]
            for one_cause in cause_list:
                report.cause_counts[one_cause] = report.cause_counts.get(one_cause, 0) + 1
                report.cause_dates.setdefault(one_cause, []).append(session_date)
            if (check.get("check_id") or "").startswith("MARKET_EVENTS.") and \
                    check.get("source_health") == "MISSING":
                market_events_missing = True

        if market_events_missing:
            prev_session = data.get("source_session")
            runs_for_session = (_report_job_runs_for_session(out_dir, prev_session)
                               if prev_session else [])
            report.market_events_missing_sessions.append({
                "pre_session": session_date,
                "source_session": prev_session,
                "cutoff": data.get("cutoff") or data.get("as_of"),
                "report_job_runs": runs_for_session,
            })

    return report


def render_human(report: PreDegradationReport) -> str:
    lines = [f"PRE DEGRADATION AUDIT ({report.runs_analyzed} run(s) analyzed)", "-" * 62]
    if report.sessions:
        lines.append(f"Sessions: {', '.join(report.sessions)}")
    lines.append("")
    lines.append("Status counts:")
    for status, count in sorted(report.status_counts.items()):
        lines.append(f"  {status:<12} {count}")
    lines.append("")
    lines.append("Cause frequency (most frequent first):")
    for cause, count in sorted(report.cause_counts.items(), key=lambda kv: kv[1], reverse=True):
        dates = ", ".join(report.cause_dates.get(cause, []))
        lines.append(f"  {count:<3} {cause:<40} [{dates}]")
    if report.market_events_missing_sessions:
        lines.append("")
        lines.append("MARKET_EVENTS MISSING - REPORT-job cross-reference:")
        for entry in report.market_events_missing_sessions:
            lines.append(f"  PRE {entry['pre_session']} (source session {entry['source_session']}, "
                         f"cutoff {entry['cutoff']}):")
            if not entry["report_job_runs"]:
                lines.append("    no REPORT-job run record found for that session at all")
            for run in entry["report_job_runs"]:
                lines.append(f"    {run['started_at']} -> {run['completed_at']}  "
                             f"run_status={run['run_status']}  "
                             f"has_market_events={run['has_market_events']}")
    lines.append("-" * 62)
    return "\n".join(lines)


__all__ = ["PreDegradationReport", "analyze_pre_degradation", "list_pre_readiness_files",
          "render_human"]

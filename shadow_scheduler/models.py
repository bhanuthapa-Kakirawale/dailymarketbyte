"""shadow_scheduler data shapes and vocabulary. Data only - no I/O, no subprocess, no business
logic. Independently defined from `production_orchestrator/models.py` and
`intelligence_refresh/models.py` (not imported from either), mirroring this repo's convention
that each orchestration layer stays independently runnable.

Runner status vocabulary is deliberately its OWN small set, never renamed to match PK-C's
READY/DEGRADED/BLOCKED or PK-D's/intelligence_refresh's SUCCESS/DEGRADED/BLOCKED/FAILED/...:
this layer answers "did the scheduled job launch and finish", never "was the business outcome
good" - that second question is answered by the child's own exit code / manifest, preserved
verbatim in the run record, never re-labelled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA = "dmb.shadow_scheduler.run/1"
LATEST_SCHEMA = "dmb.shadow_scheduler.latest/1"

JOBS = ("intelligence_am", "pre", "intelligence_pm", "post")

# RUNNING/COMPLETED are the ordinary path: the child launched. CHILD_NONZERO still means the
# child launched and ran to completion - a nonzero exit (e.g. PK-D's BLOCKED=20) is a business
# outcome, not scheduler infrastructure failure, and is never retried. LOCKED and FAILED are
# the two infra-only outcomes where no child exit code exists to propagate.
RUNNING, COMPLETED, CHILD_NONZERO, LOCKED, FAILED = (
    "RUNNING", "COMPLETED", "CHILD_NONZERO", "LOCKED", "FAILED")
TERMINAL_STATUSES = frozenset({COMPLETED, CHILD_NONZERO, LOCKED, FAILED})

# Infra-only exit codes, used ONLY when no child exit code exists to propagate (lock timeout,
# launch failure before/while starting the subprocess). COMPLETED/CHILD_NONZERO always exit
# with the child's own exit code, verbatim - never one of these two numbers. Deliberately
# small and disjoint from production_orchestrator's {0,20,30,40} and intelligence_refresh's
# {0,20,30} so a 3 or 4 can never be misread as one of those business codes.
INFRA_EXIT_CODES = {LOCKED: 3, FAILED: 4}

# status.py's own per-job/overall health vocabulary (distinct again from runner_status and
# from business readiness).
PENDING, MISSED, NOT_EXPECTED, OK, UNKNOWN = "PENDING", "MISSED", "NOT_EXPECTED", "OK", "UNKNOWN"
HEALTHY, ATTENTION, BROKEN = "HEALTHY", "ATTENTION", "BROKEN"


@dataclass(frozen=True)
class JobSpec:
    """One row of the static, trusted job registry (shadow_scheduler.config.JOB_REGISTRY).
    `runner.py` builds the child command ONLY from this table's `bat_relpath` - never from an
    env var, CLI-supplied path, or any market/log/session text.
    """
    job: str
    bat_relpath: str                 # repo-root-relative, e.g. "scripts/run_production_pre.bat"
    scheduled_time: str              # "HH:MM", IST, a plain string - no business clock logic here
    weekdays: tuple = (0, 1, 2, 3, 4)   # Python date.weekday() numbering, Mon-Fri
    edition: str | None = None       # "PRE" | "POST" | None (the two intelligence jobs)
    business_latest_relpath: str = ""  # path, relative to config.OUT_DIR, of the OTHER
                                        # package's latest.json this job's business outcome
                                        # lives in - read-only, read by status.py only
    task_name: str = ""              # Windows Task Scheduler task name under \\DMB\\


@dataclass
class RunRecord:
    """One shadow_scheduler invocation. Immutable once `runner_status` is terminal."""
    schema_version: str = SCHEMA
    job: str = ""
    run_id: str = ""
    started_at: str = ""              # UTC ISO-8601
    completed_at: str | None = None
    duration: float | None = None     # seconds, None until terminal
    hostname: str = ""
    pid: int = 0
    repo_path: str = ""
    python_executable: str = ""
    child_command: list = field(default_factory=list)   # literal argv, e.g. [bat_abspath]
    child_exit_code: int | None = None   # None only for LOCKED/FAILED-before-launch
    runner_status: str = RUNNING
    log_path: str = ""                # relative to shadow_scheduler's own out_dir
    business_latest_ref: str | None = None   # a path STRING reference, never file content
    reclaimed_stale_lock: dict | None = None
    error: str | None = None          # launch-failure diagnostic (FAILED only)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_latest_dict(self, path: str) -> dict:
        return {"schema": LATEST_SCHEMA, "job": self.job, "run_id": self.run_id, "path": path,
                "runner_status": self.runner_status, "child_exit_code": self.child_exit_code,
                "started_at": self.started_at, "completed_at": self.completed_at,
                "updated_at": self.completed_at or self.started_at}


__all__ = ["SCHEMA", "LATEST_SCHEMA", "JOBS", "RUNNING", "COMPLETED", "CHILD_NONZERO", "LOCKED",
          "FAILED", "TERMINAL_STATUSES", "INFRA_EXIT_CODES", "PENDING", "MISSED",
          "NOT_EXPECTED", "OK", "UNKNOWN", "HEALTHY", "ATTENTION", "BROKEN", "JobSpec",
          "RunRecord"]

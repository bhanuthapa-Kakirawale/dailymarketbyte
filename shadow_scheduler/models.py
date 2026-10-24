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
# the two infra-only outcomes where no child exit code exists to propagate. EXPIRED is a THIRD
# kind of terminal outcome, distinct from both: the job was invoked (or finished waiting for
# the global lock) at/after its own 1-hour execution-window deadline
# (`config.EXECUTION_WINDOW_MINUTES`), and the runner deliberately refused to launch the child
# at all - never a business outcome (nothing ran), never an infra failure (nothing crashed),
# never retried.
RUNNING, COMPLETED, CHILD_NONZERO, LOCKED, FAILED, EXPIRED = (
    "RUNNING", "COMPLETED", "CHILD_NONZERO", "LOCKED", "FAILED", "EXPIRED")

# Dependency ordering (PK-E follow-up): WAITING_DEPENDENCY is a non-terminal checkpoint, written
# only while `pre`/`post` is genuinely blocked on its upstream intelligence job - the same
# mechanism the ordinary RUNNING checkpoint already uses, so `status` can show it live with no
# extra process-introspection machinery. DEPENDENCY_FAILED is a terminal outcome: the upstream
# job reached FAILED/LOCKED/EXPIRED today (a terminal, non-satisfying attempt) or the dependency
# state could not be re-confirmed immediately before the lock/child launch. Neither is ever
# produced for intelligence_am/intelligence_pm themselves - they have no dependency of their own
# (shadow_scheduler.config.DEPENDENCIES).
WAITING_DEPENDENCY, DEPENDENCY_FAILED = "WAITING_DEPENDENCY", "DEPENDENCY_FAILED"

# PK-OPS-1 (2026-10-24): two more terminal outcomes.
# EARLY - invoked BEFORE its occurrence's window_start (`config.resolve_occurrence` class
#   EARLY): the child was deliberately never launched, nothing was waited for, nothing is
#   rescheduled. Never a successful scheduled execution, never retried (exit 0, like EXPIRED).
#   An out-of-window invocation that belongs to a PAST occurrence (e.g. last night's job fired
#   by StartWhenAvailable at 06:48) is EXPIRED, never EARLY - see config.resolve_occurrence.
# ABANDONED - never written by the runner itself. It is the EFFECTIVE status of a RUNNING /
#   WAITING_DEPENDENCY checkpoint whose owning runner is verifiably gone on this host (see
#   shadow_scheduler/orphans.py), and it exists only through an APPEND-ONLY reconciliation
#   marker (`reconciliations/<run_id>.json`): the original run file is never rewritten, so the
#   checkpoint it left behind stays the evidence. Never a success - nothing is claimed about
#   what the child did.
EARLY, ABANDONED = "EARLY", "ABANDONED"

TERMINAL_STATUSES = frozenset({COMPLETED, CHILD_NONZERO, LOCKED, FAILED, EXPIRED,
                               DEPENDENCY_FAILED, EARLY, ABANDONED})
NON_TERMINAL_STATUSES = frozenset({RUNNING, WAITING_DEPENDENCY})

# Infra-only exit codes, used ONLY when no child exit code exists to propagate (lock timeout,
# launch failure before/while starting the subprocess, or a dependency that never became
# satisfied). COMPLETED/CHILD_NONZERO always exit with the child's own exit code, verbatim -
# never one of these numbers. Deliberately small and disjoint from production_orchestrator's
# {0,20,30,40} and intelligence_refresh's {0,20,30} so none of these can ever be misread as one
# of those business codes. DEPENDENCY_FAILED is nonzero (unlike EXPIRED) because, unlike an
# unremarkable deliberately-skipped window, it is worth a look - but it must never trigger a
# Windows Task Scheduler retry; RestartCount=0 is already configured repo-wide for all 4 tasks.
INFRA_EXIT_CODES = {LOCKED: 3, FAILED: 4, DEPENDENCY_FAILED: 5}

# EXPIRED's own exit code: deliberately 0, NOT one of INFRA_EXIT_CODES' nonzero codes. There is
# no infrastructure failure to report (the lock and every cmdlet worked fine) and nothing here
# should ever be auto-retried by Task Scheduler - EXPIRED is a deliberately-skipped scheduling
# opportunity, not a crash. The run record's `runner_status` field (EXPIRED, never COMPLETED)
# is what actually distinguishes it from an ordinary successful run.
EXPIRED_EXIT_CODE = 0
EARLY_EXIT_CODE = 0

# status.py's own per-job/overall health vocabulary (distinct again from runner_status and
# from business readiness). WAITING is new (PK-E follow-up): "scheduled_time <= now <
# deadline, no run yet today" - distinct from PENDING (before scheduled_time) and MISSED
# (deadline has passed with no run today). It must never be reported as MISSED.
PENDING, MISSED, NOT_EXPECTED, OK, UNKNOWN, WAITING = (
    "PENDING", "MISSED", "NOT_EXPECTED", "OK", "UNKNOWN", "WAITING")
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
    started_at: str = ""              # UTC ISO-8601 - the actual invocation time
    completed_at: str | None = None
    duration: float | None = None     # seconds, None until terminal
    hostname: str = ""
    pid: int = 0
    repo_path: str = ""
    python_executable: str = ""
    child_command: list = field(default_factory=list)   # literal argv, e.g. [bat_abspath]
    child_exit_code: int | None = None   # None for LOCKED/FAILED/EXPIRED (no child ran)
    runner_status: str = RUNNING
    log_path: str = ""                # relative to shadow_scheduler's own out_dir
    business_latest_ref: str | None = None   # a path STRING reference, never file content
    reclaimed_stale_lock: dict | None = None
    error: str | None = None          # launch-failure/EXPIRED diagnostic (free text)
    # 1-hour execution-window fields (PK-E follow-up, 2026-10-12), IST ISO-8601:
    scheduled_time: str = ""          # "HH:MM", copied from JobSpec for a self-contained record
    window_start: str | None = None   # == scheduled_dt
    window_end: str | None = None     # == deadline_dt; EXPIRED means started_at (IST) >= this
    child_started: bool = True        # False only for LOCKED/FAILED-before-launch/EXPIRED
    # Dependency ordering (PK-E follow-up) - all stay None for intelligence_am/intelligence_pm,
    # which have no entry in config.DEPENDENCIES:
    dependency_job: str | None = None       # e.g. "intelligence_am", or None if no dependency
    dependency_status: str | None = None    # "SATISFIED" | "FAILED" | "TIMED_OUT" | None
    dependency_run_id: str | None = None    # the upstream job's own run_id, once SATISFIED
    dependency_wait_seconds: float | None = None
    dependency_satisfied_at: str | None = None   # UTC ISO-8601, only when SATISFIED
    # Scheduled-occurrence identity (PK-OPS-1, 2026-10-24) - see config.resolve_occurrence.
    # `window_start`/`window_end` above are now the ATTRIBUTED occurrence's window (before this
    # change they were always computed on the invocation's own calendar date). Records written
    # before this change have neither field; readers infer both from `started_at` with the same
    # `resolve_occurrence` rule (orphans.attribution).
    scheduled_date: str | None = None   # IST "YYYY-MM-DD" of the occurrence this run belongs to
    invocation: str | None = None       # "IN_WINDOW" | "EARLY" | "LATE"
    # The launched child's own process identity (PK-OPS-1) - written in a second RUNNING
    # checkpoint right after the child starts, so an orphan check can tell "runner died, child
    # still running" apart from "both gone". None when no child launched, or for a record
    # written before this change.
    child_pid: int | None = None
    child_started_at: str | None = None   # UTC ISO-8601
    # "NOT_LAUNCHED" (no child can exist) -> "LAUNCHING" (checkpointed immediately before the
    # launch: a child may exist) -> "LAUNCHED" (PID known). None on a pre-PK-OPS-1 record.
    child_phase: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_latest_dict(self, path: str) -> dict:
        return {"schema": LATEST_SCHEMA, "job": self.job, "run_id": self.run_id, "path": path,
                "runner_status": self.runner_status, "child_exit_code": self.child_exit_code,
                "started_at": self.started_at, "completed_at": self.completed_at,
                "updated_at": self.completed_at or self.started_at,
                "dependency_job": self.dependency_job,
                "dependency_status": self.dependency_status,
                "dependency_wait_seconds": self.dependency_wait_seconds,
                "scheduled_date": self.scheduled_date, "invocation": self.invocation}


__all__ = ["SCHEMA", "LATEST_SCHEMA", "JOBS", "RUNNING", "COMPLETED", "CHILD_NONZERO", "LOCKED",
          "FAILED", "EXPIRED", "WAITING_DEPENDENCY", "DEPENDENCY_FAILED", "TERMINAL_STATUSES",
          "INFRA_EXIT_CODES", "EXPIRED_EXIT_CODE", "EARLY", "ABANDONED",
          "NON_TERMINAL_STATUSES", "EARLY_EXIT_CODE", "PENDING", "MISSED", "NOT_EXPECTED", "OK",
          "UNKNOWN", "WAITING", "HEALTHY", "ATTENTION", "BROKEN", "JobSpec", "RunRecord"]

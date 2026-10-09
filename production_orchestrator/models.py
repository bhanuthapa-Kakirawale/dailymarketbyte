"""The production-orchestrator run manifest (PK-D): one record per PRE or POST invocation.

    StageRecord      what happened in one stage of the sequence (SESSION_RESOLUTION, RUN_LOCK, ...)
    RunManifest       the whole run: orchestrator_status, both PK-C readiness verdicts, every
                      artifact reference, warnings/blocking reasons, runtime

This module holds data only - no I/O, no subprocess, no readiness/rendering calls. See
`production_orchestrator.manifest` for reading/writing it to disk and `production_orchestrator.
post`/`pre` for the sequencing that fills it in.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

SCHEMA = "dmb.production_orchestrator.run/1"
LATEST_SCHEMA = "dmb.production_orchestrator.latest/1"

EDITIONS = ("PRE", "POST")
SHADOW, PUBLISH = "SHADOW", "PUBLISH"

# orchestrator_status: PLANNED/RUNNING are PK-D-only transient states; SUCCESS/DEGRADED/BLOCKED/
# FAILED/SKIPPED are reused verbatim from the existing publication_runs vocabulary (never
# renamed, so every other run-history reader in the repo keeps meaning the same thing);
# ALREADY_COMPLETED and LOCKED are the two genuinely new terminal states PK-D adds.
PLANNED, RUNNING = "PLANNED", "RUNNING"
SUCCESS, DEGRADED, BLOCKED, FAILED, SKIPPED = "SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "SKIPPED"
ALREADY_COMPLETED, LOCKED = "ALREADY_COMPLETED", "LOCKED"

TERMINAL_STATUSES = frozenset({SUCCESS, DEGRADED, BLOCKED, FAILED, SKIPPED, ALREADY_COMPLETED, LOCKED})
OK_STATUSES = frozenset({SUCCESS, DEGRADED, ALREADY_COMPLETED, SKIPPED})

# CLI / scheduler exit-code contract (docs/PRODUCTION_ORCHESTRATOR.md).
EXIT_CODES = {
    SUCCESS: 0, DEGRADED: 0, ALREADY_COMPLETED: 0, SKIPPED: 0,
    BLOCKED: 20, FAILED: 30, LOCKED: 40,
}
EXIT_ERROR = 30     # same code as FAILED - an uncaught exception IS an execution failure

STAGE_ORDER = {
    "PRE": ("SESSION_RESOLUTION", "RUN_LOCK", "IDEMPOTENCY_CHECK", "PREFLIGHT_READINESS",
            "RENDERER", "POST_RENDER_CHECK", "MANIFEST"),
    "POST": ("SESSION_RESOLUTION", "RUN_LOCK", "IDEMPOTENCY_CHECK", "ACQUISITION_OR_REPORT",
             "PREFLIGHT_READINESS", "RENDERER", "POST_RENDER_CHECK", "MANIFEST"),
}

STAGE_OK, STAGE_SKIPPED, STAGE_BLOCKED, STAGE_FAILED, STAGE_NOT_RUN = (
    "OK", "SKIPPED", "BLOCKED", "FAILED", "NOT_RUN")


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class StageRecord:
    stage: str
    status: str = STAGE_NOT_RUN
    started_at: str | None = None
    completed_at: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunManifest:
    schema: str = SCHEMA
    run_id: str = ""
    edition: str = ""                      # "PRE" | "POST"
    command: str = ""                      # "pre" | "post"
    session_date: str | None = None
    intent: str = SHADOW
    started_at: str = field(default_factory=_iso_now)
    completed_at: str | None = None
    orchestrator_status: str = PLANNED
    readiness_status_preflight: str | None = None
    readiness_status_post_render: str | None = None
    stages: list = field(default_factory=list)          # [StageRecord, ...]
    resumed_from_run_id: str | None = None
    forced: bool = False
    no_write: bool = False
    canonical_report: dict | None = None                 # {report_id, path, source}
    readiness_report: dict | None = None                 # {preflight_path, post_render_path}
    video_artifact: dict | None = None                    # {path, duration, exists}
    audit_artifact: dict | None = None                    # {path, final, failed_checks}
    warnings: list = field(default_factory=list)
    blocking_reasons: list = field(default_factory=list)
    failure: dict | None = None                           # {stage, reason}
    reclaimed_stale_lock: dict | None = None
    runtime: dict = field(default_factory=dict)           # {seconds, subprocess_calls: [...]}
    git_commit: str = "unknown"
    host: dict = field(default_factory=dict)              # {hostname, pid}
    cli: dict = field(default_factory=dict)               # {argv, resume, force}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.orchestrator_status, EXIT_ERROR)

    def stage(self, name: str) -> StageRecord | None:
        for s in self.stages:
            if s.stage == name:
                return s
        return None

    def begin_stage(self, name: str) -> StageRecord:
        rec = self.stage(name)
        if rec is None:
            rec = StageRecord(stage=name)
            self.stages.append(rec)
        rec.status = STAGE_NOT_RUN
        rec.started_at = _iso_now()
        rec.completed_at = None
        return rec

    def finish_stage(self, name: str, status: str, detail: str = "") -> StageRecord:
        rec = self.stage(name)
        if rec is None:
            rec = StageRecord(stage=name, started_at=_iso_now())
            self.stages.append(rec)
        rec.status = status
        rec.detail = detail
        rec.completed_at = _iso_now()
        return rec

    def finalize(self, orchestrator_status: str) -> None:
        self.orchestrator_status = orchestrator_status
        self.completed_at = _iso_now()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() if isinstance(s, StageRecord) else s for s in self.stages]
        d["exit_code"] = self.exit_code
        return d

    def to_latest_dict(self, path: str) -> dict:
        return {"schema": LATEST_SCHEMA, "edition": self.edition, "run_id": self.run_id,
                "session_date": self.session_date, "path": path,
                "orchestrator_status": self.orchestrator_status,
                "readiness_status_preflight": self.readiness_status_preflight,
                "readiness_status_post_render": self.readiness_status_post_render,
                "started_at": self.started_at, "completed_at": self.completed_at,
                "blocking_reasons": list(self.blocking_reasons), "warnings": list(self.warnings),
                "updated_at": _iso_now()}


__all__ = ["SCHEMA", "LATEST_SCHEMA", "EDITIONS", "SHADOW", "PUBLISH", "PLANNED", "RUNNING",
           "SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "SKIPPED", "ALREADY_COMPLETED", "LOCKED",
           "TERMINAL_STATUSES", "OK_STATUSES", "EXIT_CODES", "EXIT_ERROR", "STAGE_ORDER",
           "STAGE_OK", "STAGE_SKIPPED", "STAGE_BLOCKED", "STAGE_FAILED", "STAGE_NOT_RUN",
           "StageRecord", "RunManifest"]

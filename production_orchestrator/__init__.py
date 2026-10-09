"""PK-D - Production Automation & Orchestration V1 (docs/PRODUCTION_ORCHESTRATOR.md).

ONE command per edition that sequences the EXISTING production components - it never redesigns
or recalculates business logic (readiness policy, editorial, Radar, Market Structure,
institutional flows, publication rights all stay exactly as they are):

    python -m production_orchestrator pre     SESSION_RESOLUTION -> RUN_LOCK -> IDEMPOTENCY_CHECK
    python -m production_orchestrator post    -> PREFLIGHT_READINESS (PK-C) -> RENDERER (existing
                                                  REPORT/POST/PRE commands) -> POST_RENDER_CHECK
                                                  (PK-C) -> an immutable run manifest.
    python -m production_orchestrator status  read-only: the latest PRE/POST run, no side effects

Never uploads, regardless of --intent publish: the existing PUBLIC_REVIEW_REQUIRED_POLICY and
readiness checks decide, PK-D adds no override and never imports `upload`.
"""
from .models import (ALREADY_COMPLETED, BLOCKED, DEGRADED, EXIT_CODES, EXIT_ERROR, FAILED,
                     LOCKED, PLANNED, PUBLISH, RUNNING, SHADOW, SKIPPED, STAGE_ORDER, SUCCESS,
                     RunManifest, StageRecord)

__all__ = ["RunManifest", "StageRecord", "STAGE_ORDER", "EXIT_CODES", "EXIT_ERROR", "PLANNED",
           "RUNNING", "SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "SKIPPED",
           "ALREADY_COMPLETED", "LOCKED", "SHADOW", "PUBLISH"]

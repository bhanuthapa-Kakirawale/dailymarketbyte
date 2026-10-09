"""The intelligence_refresh run manifest. Data only - no I/O. Independently defined from
`production_orchestrator/models.py` (not imported from it) so the two orchestrators stay
independently runnable, though the shape/status vocabulary intentionally mirrors it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

SCHEMA = "dmb.intelligence_refresh.run/1"
LATEST_SCHEMA = "dmb.intelligence_refresh.latest/1"

SUCCESS, DEGRADED, BLOCKED, FAILED, LOCKED = "SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "LOCKED"
TERMINAL_STATUSES = frozenset({SUCCESS, DEGRADED, BLOCKED, FAILED, LOCKED})
OK_STATUSES = frozenset({SUCCESS, DEGRADED})

# CLI exit-code contract (docs/INTELLIGENCE_REFRESH.md): 0 = current refresh succeeded or
# degraded only on optional historical gaps; 20 = the CURRENT/latest refresh itself was
# blocked; 30 = execution failure or a held lock.
EXIT_CODES = {SUCCESS: 0, DEGRADED: 0, BLOCKED: 20, FAILED: 30, LOCKED: 30}
EXIT_ERROR = 30


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class RunManifest:
    schema: str = SCHEMA
    run_id: str = ""
    command: str = "refresh"
    started_at: str = field(default_factory=_iso_now)
    completed_at: str | None = None
    orchestrator_status: str = "RUNNING"
    cli: dict = field(default_factory=dict)
    resumed_from_run_id: str | None = None
    reclaimed_stale_lock: dict | None = None
    latest_session_result: dict | None = None
    session_results: list = field(default_factory=list)   # [{session, components, session_status}]
    summary: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    blocking_reasons: list = field(default_factory=list)
    failure: dict | None = None
    host: dict = field(default_factory=dict)
    git_commit: str = "unknown"

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.orchestrator_status, EXIT_ERROR)

    def finalize(self, orchestrator_status: str) -> None:
        self.orchestrator_status = orchestrator_status
        self.completed_at = _iso_now()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["exit_code"] = self.exit_code
        return d

    def to_latest_dict(self, path: str) -> dict:
        return {"schema": LATEST_SCHEMA, "run_id": self.run_id, "path": path,
                "orchestrator_status": self.orchestrator_status,
                "started_at": self.started_at, "completed_at": self.completed_at,
                "latest_session_result": self.latest_session_result,
                "summary": self.summary, "blocking_reasons": list(self.blocking_reasons),
                "warnings": list(self.warnings), "updated_at": _iso_now()}


__all__ = ["SCHEMA", "LATEST_SCHEMA", "SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "LOCKED",
          "TERMINAL_STATUSES", "OK_STATUSES", "EXIT_CODES", "EXIT_ERROR", "RunManifest"]

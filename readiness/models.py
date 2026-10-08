"""The readiness result: one check per capability, one overall verdict (PK-C).

    CheckResult       what one check saw (status PASS / WARN / FAIL / SKIP)
    ReadinessResult   the edition's verdict READY / DEGRADED / BLOCKED + every check

The verdict is pure arithmetic over the checks - any FAIL -> BLOCKED, else any WARN ->
DEGRADED, else READY - so the same evidence always gives the same answer. A check's status is
fixed by the capability's requirement (`readiness.matrix`): an unmet REQUIRED capability FAILs,
an unmet OPTIONAL one WARNs; HEALTHY_EMPTY / UNSUPPORTED never fail.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

SCHEMA = "dmb.readiness/1"

# overall
READY, DEGRADED, BLOCKED = "READY", "DEGRADED", "BLOCKED"
# per check
PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
# requirement (readiness.matrix) -> severity
REQUIRED, OPTIONAL, NOT_APPLICABLE = "REQUIRED", "OPTIONAL", "NOT_APPLICABLE"
BLOCKING, DEGRADING, INFO = "BLOCKING", "DEGRADING", "INFO"
SEVERITY = {REQUIRED: BLOCKING, OPTIONAL: DEGRADING, NOT_APPLICABLE: INFO}
# source health vocabulary (aggregated from the existing per-source statuses)
HEALTHY = "HEALTHY"
HEALTHY_EMPTY = "HEALTHY_EMPTY"          # the source answered and validly had nothing
STALE = "STALE"
PARTIAL = "PARTIAL"
SOURCE_FAILURE = "SOURCE_FAILURE"
UNSUPPORTED = "UNSUPPORTED"              # no adapter by design (ESM, NOT_SUPPORTED_YET)
MISSING = "MISSING"                      # never captured / not available at the cutoff
GOOD_HEALTH = frozenset({HEALTHY, HEALTHY_EMPTY, UNSUPPORTED})

# stages / modes / intents
PREFLIGHT, POST_RENDER = "PREFLIGHT", "POST_RENDER"
LIVE, REPLAY = "LIVE", "REPLAY"
SHADOW, PUBLISH = "SHADOW", "PUBLISH"

# CLI exit-code contract (docs/PRODUCTION_READINESS.md). Any other non-zero - e.g. 1 from an
# interpreter crash before the CLI's own handler - is an execution error too.
EXIT_CODES = {READY: 0, DEGRADED: 10, BLOCKED: 20}
EXIT_ERROR = 30


class ReadinessExecutionError(RuntimeError):
    """The gate itself could not evaluate (not a verdict about the edition)."""


@dataclass
class CheckResult:
    check_id: str
    category: str
    capability: str
    requirement: str
    status: str
    message: str
    source_health: str | None = None
    observed: object = None
    expected: object = None
    source: str | None = None
    as_of: str | None = None
    remediation: str | None = None

    @property
    def severity(self) -> str:
        return SEVERITY[self.requirement]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity
        return d


def unmet(requirement: str) -> str:
    """The status an unmet capability gets: FAIL if required, WARN if optional, else SKIP."""
    return {REQUIRED: FAIL, OPTIONAL: WARN}.get(requirement, SKIP)


def health_status(requirement: str, health: str) -> str:
    """PASS for a healthy / validly empty / unsupported-by-design source, else unmet()."""
    if health in GOOD_HEALTH:
        return PASS
    return unmet(requirement)


def overall(checks) -> str:
    statuses = {c.status for c in checks}
    if FAIL in statuses:
        return BLOCKED
    if WARN in statuses:
        return DEGRADED
    return READY


@dataclass
class ReadinessResult:
    edition: str                       # PRE | POST
    stage: str = PREFLIGHT
    mode: str = LIVE
    intent: str = SHADOW
    profile: str = "PUBLIC_UNREGISTERED"
    as_of: dt.datetime | None = None
    cutoff: dt.datetime | None = None
    session_date: dt.date | None = None      # PRE: the session about to open; POST: the recap
    edition_date: dt.date | None = None
    source_session: dt.date | None = None    # PRE: the previous session it describes
    checks: list = field(default_factory=list)
    evaluated_at: str | None = None
    runtime: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def overall_status(self) -> str:
        return overall(self.checks)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.overall_status]

    @property
    def blocking_reasons(self) -> list:
        return [f"{c.check_id}: {c.message}" for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list:
        return [f"{c.check_id}: {c.message}" for c in self.checks if c.status == WARN]

    @property
    def decision(self) -> str:
        st = self.overall_status
        if self.stage == POST_RENDER:
            return {READY: "RENDERED EDITION PASSED QA", DEGRADED: "RENDERED EDITION PASSED QA WITH NOTES",
                    BLOCKED: "RENDERED EDITION FAILED QA - DO NOT PUBLISH"}[st]
        if st == READY:
            return "SAFE TO GENERATE"
        if st == BLOCKED:
            return "DO NOT GENERATE - " + ", ".join(sorted({c.category for c in self.checks
                                                            if c.status == FAIL}))
        cats = sorted({c.category for c in self.checks if c.status == WARN})
        return "SAFE TO GENERATE WITH DEGRADED " + ", ".join(cats)

    def check(self, check_id: str) -> CheckResult | None:
        return next((c for c in self.checks if c.check_id == check_id), None)

    def to_dict(self) -> dict:
        iso = lambda v: v.isoformat() if v is not None else None   # noqa: E731
        return {"schema": SCHEMA, "edition": self.edition, "stage": self.stage,
                "mode": self.mode, "intent": self.intent, "profile": self.profile,
                "evaluated_at": self.evaluated_at, "as_of": iso(self.as_of),
                "cutoff": iso(self.cutoff), "session_date": iso(self.session_date),
                "edition_date": iso(self.edition_date), "source_session": iso(self.source_session),
                "overall_status": self.overall_status, "decision": self.decision,
                "exit_code": self.exit_code, "blocking_reasons": self.blocking_reasons,
                "warnings": self.warnings, "checks": [c.to_dict() for c in self.checks],
                "notes": list(self.notes), "runtime": dict(self.runtime)}


__all__ = ["SCHEMA", "READY", "DEGRADED", "BLOCKED", "PASS", "WARN", "FAIL", "SKIP",
           "REQUIRED", "OPTIONAL", "NOT_APPLICABLE", "BLOCKING", "DEGRADING", "INFO",
           "HEALTHY", "HEALTHY_EMPTY", "STALE", "PARTIAL", "SOURCE_FAILURE", "UNSUPPORTED",
           "MISSING", "PREFLIGHT", "POST_RENDER", "LIVE", "REPLAY", "SHADOW", "PUBLISH",
           "EXIT_CODES", "EXIT_ERROR", "ReadinessExecutionError", "CheckResult",
           "ReadinessResult", "unmet", "health_status", "overall"]

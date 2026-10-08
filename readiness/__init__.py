"""PK-C - Production Readiness Gate V1 (docs/PRODUCTION_READINESS.md).

"Is this PRE or POST edition safe and ready to generate right now?" -> READY / DEGRADED /
BLOCKED, with every check and the reason. Read-only: it consumes the existing authoritative
checks (session calendar, canonical report lookup, freshness, capture records, persisted
snapshots, the V3 planner, the publication audit, render prerequisites) and never renders,
uploads, rebuilds or changes a selection.

    evaluate_pre(pre_date, as_of, ...)     PREFLIGHT for the "before the bell" edition
    evaluate_post(session, as_of, ...)     PREFLIGHT for the completed-session recap
    evaluate_post_render(edition, session) POST_RENDER: did the rendered edition pass QA
    python -m readiness {pre|post}         the operator / PK-D command (exit 0 / 10 / 20 / 30)
"""
from .matrix import MATRIX, as_table, requirement
from .models import (BLOCKED, DEGRADED, EXIT_CODES, EXIT_ERROR, READY, CheckResult,
                     ReadinessExecutionError, ReadinessResult)
from .post import evaluate_post
from .post_render import evaluate_post_render
from .pre import evaluate_pre
from .report import report_path, write_report

__all__ = ["evaluate_pre", "evaluate_post", "evaluate_post_render", "ReadinessResult",
           "CheckResult", "ReadinessExecutionError", "READY", "DEGRADED", "BLOCKED",
           "EXIT_CODES", "EXIT_ERROR", "MATRIX", "as_table", "requirement", "report_path",
           "write_report"]

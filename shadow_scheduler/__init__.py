"""Shadow Production Operations V1 (PK-E): a thin Windows Task Scheduler wrapper around the
four already-approved DMB entry points (`scripts\\run_intelligence_refresh.bat`,
`scripts\\run_production_pre.bat`, `scripts\\run_production_post.bat`). It only starts the
existing safe command, records the operational result, and exposes read-only health - it
decides nothing about market/business readiness. It also enforces scheduler-level dependency
ordering (`intelligence_am` before `pre`, `intelligence_pm` before `post`, `dependency.py`) by
observing its own run records - never business state, never PK-C's readiness verdict, and PK-C
remains the sole authority on whether a render is actually safe. See
docs/SHADOW_PRODUCTION_OPERATIONS.md.
"""
from __future__ import annotations

__all__ = []

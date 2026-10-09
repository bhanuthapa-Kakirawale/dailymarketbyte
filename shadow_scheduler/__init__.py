"""Shadow Production Operations V1 (PK-E): a thin Windows Task Scheduler wrapper around the
four already-approved DMB entry points (`scripts\\run_intelligence_refresh.bat`,
`scripts\\run_production_pre.bat`, `scripts\\run_production_post.bat`). It only starts the
existing safe command, records the operational result, and exposes read-only health - it
decides nothing about market/business readiness. See docs/SHADOW_PRODUCTION_OPERATIONS.md.
"""
from __future__ import annotations

__all__ = []

"""production_quality: read-only diagnostics over DMB's own already-written artifacts.

Never a business-logic package: it does not reimplement readiness/market_events/
institutional_flows classification, make a network call, write anything, or render
anything - it only reads the JSON this repo already produces (readiness snapshots,
REPORT job run records) and groups/counts what is already there. See
`pre_degradation.py` and docs/PRODUCTION_READINESS.md.
"""
from __future__ import annotations

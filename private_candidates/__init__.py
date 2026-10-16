"""Private Trade Candidate Intelligence V1 (PRIVATE_ANALYTICS only).

Radar flags a stock worth attention; this package asks a narrower, PRIVATE-only question of
each flagged stock: does it deserve trading-RESEARCH priority once setup quality, market/sector
alignment, entry location, liquidity/volatility, event risk and data completeness are all
weighed together? It is not an execution engine (no order/position/broker concept anywhere) and
not a statistically-validated model (`score_validation_status` is always "PROVISIONAL" - V1
weights are a documented, reviewable judgment call, never tuned against historical returns).

Deterministic Python only - no LLM, no ML, in the candidate universe, the scoring or the
shortlist. Never imported by any PUBLIC planner, `main.py`, `upload.py` or video renderer
(enforced by `tests/test_private_candidates_isolation.py`); never changes a Radar candidate,
its order, or the Private Desk attention set (also enforced there).

See docs/PRIVATE_TRADE_CANDIDATES.md.
"""

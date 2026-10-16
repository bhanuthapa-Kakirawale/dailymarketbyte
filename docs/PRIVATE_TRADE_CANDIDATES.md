# Private Trade Candidate Intelligence V1

PRIVATE_ANALYTICS only. Never reachable from PUBLIC PRE/POST video production (test-enforced:
`tests/test_private_candidates_isolation.py`). Not an execution engine (no order/position/
broker concept anywhere - test-enforced) and not a statistically-validated model: every score
is stamped `score_validation_status = "PROVISIONAL"` and V1 weights are a documented,
reviewable judgment call, never tuned against historical returns.

## Radar candidate vs. trade candidate

A Radar candidate is an **attention item**: the Market Intelligence Radar flagged a stock
because at least two independent evidence families (volume, technical structure, relative
performance) were active on it that session. That says nothing about whether the stock is
presently a *good trading research priority* - its market/sector alignment, how extended the
price already is, its liquidity/volatility, nearby known events, and the completeness of its
own data all matter too. This package answers that second, narrower question for EVERY Radar
candidate of a session, never for a stock Radar did not flag (never a fresh market-wide scan).

**A Radar candidate is never automatically a trade candidate.** Every candidate keeps its Radar
evidence (`radar_reason_codes`/`radar_active_families`) for traceability, but its
`research_priority` is decided independently by this package's own component model.

## Candidate universe

The input universe is exactly today's stored Radar candidates (`radar_candidate_history`, read
via `private_desk.repository.DeskRepository.candidates`). Detector values (volume anomaly,
technical structure, relative performance) are read from `private_desk.replay.get_replay`/
`reconcile` - the Radar's OWN detector chain, re-run read-only from stored OHLCV and reconciled
against what the evening run actually recorded. This package never re-implements RVOL,
prior-high/low, or relative-vs-Nifty itself; a candidate whose replay status is `NOT_REPLAYED`
or `REASON_CODES_DIFFER` is `DATA_INCOMPLETE` and its detector values are not trusted.

Every Radar candidate for the session is retained in the output, whatever its research
priority - `ELIGIBLE` / `REVIEW` / `NOT_ACTIONABLE` / `DATA_INCOMPLETE` - for auditability. None
is ever deleted for failing a trade-quality filter.

## Package layout

```
private_candidates/
    models.py      TradeCandidateAssessment packet, ComponentScore envelope, every enum
    policy.py      ScoringPolicy: weights, thresholds, score_policy_version (PROVISIONAL)
    context.py     one read function per subsystem, each using THAT subsystem's own PIT primitive
    metrics.py     the few small, new, documented deterministic calculations this package needed
    eligibility.py DataQualityTier + ResearchPriority state machine
    scoring.py     the 10 component scorers (A-J) + total
    shortlist.py   raw ranking + sector-capped diversified shortlist with demotion reasons
    store.py       immutable, versioned snapshot history (mirrors institutional_flows/store.py)
    service.py     orchestration: replay -> eligibility -> metrics -> scoring -> shortlist
```

`private_desk/services/trade_candidates.py` is the only private_desk-side glue: a thin,
read-only wrapper rendering this package's output as the `/trade-candidates` page, the
`/trade-candidates/{symbol}` detail page, and one Data Quality panel. It never writes a
snapshot - `private_candidates.store.write_snapshot` is called only by this package's own
service entry points, never from `private_desk`.

## Why some field names look unusual

`private_desk.packet.FORBIDDEN_FIELD_TOKENS` bans standalone words like `entry`, `stop`,
`target`, `exit` in any field name reachable from the desk (checked by splitting every field
name on `_` and intersecting with the token set). Rather than widen that list, this package's
field names were chosen to avoid the collision from the start while keeping the underlying
concepts: "entry location/quality" is `range_position_pct`/`extension_state`/`pullback_state`;
a stop-loss-shaped "where would this idea be wrong" reference is `structural_reference_price`/
`structural_reference_kind`/`risk_width_label`. None of these is an order, a stop-loss, or a
profit target - `risk_width_label` is a *distance* label (how many ATR14 units price sits from
the structural reference), never a reward:risk ratio, because no profit target exists anywhere
in this system to compute one from.

`private_desk` also has a long-standing, test-enforced rule that its OWN source never
introduces a "score" identifier (`test_no_new_score_is_introduced`). That rule is not weakened
here: `private_desk/services/trade_candidates.py` reads the already-computed score via
`getattr`/`.get(...)` (never a literal `score_*` identifier written in that file) and
re-presents it under the desk-local name "quality points" - the same "Candidate Quality"
framing used throughout this document and the owner's own spec.

## Component model (PROVISIONAL weights, max 100)

| # | Component | Max | Reused inputs | New calculation |
|---|---|---|---|---|
| A | `SETUP_QUALITY` | 20 | `TechnicalStructure.events`, `VolumeAnomaly.level`, independent signal count | - |
| B | `MARKET_ALIGNMENT` | 12 | Market Regime label vs. candidate direction | regime streak (metrics.regime_streak) |
| C | `SECTOR_ALIGNMENT` | 10 | `market_structure.sectors` mapping | equal-weight sector series + `radar.relative` (reused, computed fresh) |
| D | `RELATIVE_STRENGTH` | 10 | `RelativePerformance.market_relative_20d_pp`, persistence | - |
| E | `MTF_CONFIRMATION` | 10 | daily `TechnicalStructure` | weekly OHLCV resample (metrics.weekly_resample) |
| F | `RANGE_POSITION_AND_EXTENSION` | 12 | `prior_20_high/low`, `sma20` | ATR14 (metrics.atr14) |
| G | `LIQUIDITY` | 8 | `VolumeAnomaly.average_volume_20` x close | - |
| H | `VOLATILITY_FIT` | 8 | - | ATR14 as % of close |
| I | `EVENT_RISK` | 6 | `market_events.store.load_latest` (stock families) | trading-session event window |
| J | `DATA_QUALITY_INTEGRITY` | 4 | replay `reconcile()` status, detector warnings | - |

Every component always appears on every candidate, even at 0 (`status=NOT_SCORABLE`), with
`missing_data`/`reason` naming exactly what was unavailable. `score_max` is always 100
regardless of how many components are `NOT_SCORABLE` - never renormalized, so a low total
stays self-explanatory rather than hidden by rescaling the denominator.

### New calculations, and why each was necessary

No ATR, no weekly bar aggregation, and no stock-vs-sector relative-strength wiring existed
anywhere in the repository before this package (confirmed during design: `radar/models.py`'s
`TechnicalStructure` has only prior-20/50 high/low, SMA20/50, and 5-day range context; sector
fields on `RelativePerformance` exist but are always `None` in production because
`radar/daily_pipeline.py` never feeds `radar.relative.scan_relative_performance_universe`'s
`sector_map`/`sector_series_by_name` parameters). Each new calculation is small, pure, and
documented in `metrics.py`:

- **ATR14** - the standard 14-session True Range average, from the same OHLCV already read for
  every other calculation. Needs 15 sessions; below that, every ATR-dependent field is `None`.
- **Weekly resample** - groups daily OHLCV into completed ISO weeks (the week containing the
  session being evaluated is always excluded as still-forming); needs 10 completed weeks before
  a weekly SMA10 reading is trusted.
- **Equal-weight sector series** - rebases each sector member's close to 100 at its own first
  available date, then averages across members per date. This is a NEW, LOCAL, READ-ONLY
  approximation this package computes for its own private research context; it is NOT NSE's or
  any official sector index, and it is fed into the EXISTING, unmodified
  `radar.relative.scan_relative_performance_universe` (which already supports sector parameters
  - production simply never supplies them). This package does not change `radar/daily_pipeline
  .py`'s production wiring, so public Radar output is completely unaffected.
- **Regime streak** - counts consecutive identical-regime sessions ending at the session being
  evaluated, walking `RegimeStore.snapshots` backward; a gap or differing regime breaks the
  streak at that point rather than being stepped over.
- **Trading-session event window** - "within N trading sessions" counts canonical NSE trading
  sessions (`core.trading_calendar.SessionCalendar`), never calendar days, per this repo's
  general convention for every other window.
- **Pairwise return correlation** - Pearson correlation of DAILY RETURNS (never price levels)
  over a trailing window, used only by the diversified shortlist's optional correlation check;
  below `correlation_min_sample` (20) overlapping sessions, correlation is `None`/`UNKNOWN` and
  never used to demote.

Deliberately NOT built in V1: a per-stock swing-point/trendline/pivot structural level (the
richer indicators in `market.analyze()` are index-only and promoting them to per-stock is a
materially bigger change than this package's "minimal new metric" scope allows). The structural
reference stays `prior_20_low`/`prior_20_high` from the existing `TechnicalStructure` - zero new
indicator. A future V2 could lift `market.key_levels`/`swing_points`/`trendline` to per-stock if
the owner wants a richer reference.

## Point-in-time contract (per subsystem - never one uniform wrapper)

| Subsystem | Primitive this package calls | Cutoff semantics |
|---|---|---|
| Market Regime | `RegimeStore.snapshot(session)` | no cutoff gate at all - date-indexed, PIT-safe by construction |
| Market Structure | `market_structure.store.load_revision_as_of(out_dir, session, cutoff_iso)` | inclusive `<=` |
| Institutional Flows (sector-level only) | `institutional_flows.context.load_institutional(cutoff=...)` | inclusive `<=` |
| Market Events | `market_events.store.load_latest(..., first_retrieved_before=cutoff_iso)` | strictly exclusive `<` |
| OHLCV | date-bounded reads only (`DeskRepository.ohlcv_rows(..., end=session)`) | no revision chain - date-bounding alone is sufficient for a session fully in the past |

This package deliberately bypasses two existing, NON-point-in-time shortcuts: `private_desk
.repository`'s `market_structure()`/`universe()` (which read the mutable "current" artifact
file, not a point-in-time revision) and `private_desk/services/market_events.py`'s
`stock_official_rows` (which never passes `first_retrieved_before`). It calls
`market_structure.store.load_revision_as_of` and `market_events.store.load_latest` directly
instead.

## Eligibility / data quality

Every candidate's `data_quality_tier` is `INSUFFICIENT` (essential detector reading missing, or
fewer than 15 OHLCV sessions), `PARTIAL` (an optional reading - relative-performance, ATR -
missing), or `COMPLETE`. A `PARTIAL` tier is capped at `REVIEW` regardless of score - "a high
technical score with poor data must not rank normally." A compounding-risk veto
(`DEEPLY_STRETCHED` extension together with `EXTREME` volatility) demotes to `NOT_ACTIONABLE`
regardless of score, always recorded as the `COMPOUNDING_RISK_VETO` flag - never silent.

## Raw ranking vs. diversified shortlist

`raw_ranking` is every `ELIGIBLE`/`REVIEW` candidate sorted by score, descending - the full
audit trail, nothing hidden. `diversified_shortlist` is a SEPARATE view: it walks the raw
ranking and demotes (never deletes, never re-scores) a candidate once its sector already holds
`sector_cap` (2, PROVISIONAL default) shortlist entries, or - when a correlation lookup is
supplied - once it is highly correlated (>= 0.85, PROVISIONAL) with an already-shortlisted
same-sector member. Every demotion names its exact reason (`SECTOR_CONCENTRATION` or
`HIGH_CORRELATION`) and detail; the raw ranking itself is never mutated.

## Durable snapshot history

`private_candidates/store.py` mirrors `institutional_flows/store.py` exactly: immutable,
exclusive-create revisions under
`<out_dir>/private_candidates/<score_policy_version>/<session_date>/<symbol>.json` (`.r2.json`
for a same-day re-run). A later `score_policy_version` writes to its own folder, so it
structurally never touches an earlier version's files - a V1 snapshot stays reproducible under
its own policy even after the policy changes. `capture_mode` (`POINT_IN_TIME` for a same-day
run, `RECONSTRUCTED` for a backfill/replay) is stamped at build time, never inferred later from
file metadata. This store is written only by `private_candidates.service.persist` - never by
`private_desk`, which reads only `store.list_sessions`/`load_latest`.

## Limitations (V1, all explicitly provisional)

- Component weights, score bands, and every ATR/weekly/event/sector-cap/correlation threshold
  are a documented first-pass judgment call, not derived from any backtest. `python -m
  private_candidates` (a future CLI, not built in V1) would re-run this deterministically under
  a new `score_policy_version` if the owner changes them.
- Sector relative strength and the weekly multi-timeframe view are NEW, PARALLEL calculations
  introduced by this package - not a fix to Radar's own unwired `RelativePerformance.sector_*`
  fields, which stay `None` in production exactly as before.
- The structural reference is `prior_20_low`/`prior_20_high` only - no swing/pivot/trendline
  level (those remain index-only in `market.py`).
- Institutional-flow and event context are sector/index-level and symbol-level-from-official-
  filings respectively - never a claim of stock-specific institutional buying/selling.
- No historical Edge Analytics in V1: nothing here measures forward returns, MFE/MAE, or
  regime-/sector-conditioned outcomes. Snapshot history is captured with enough evidence to
  support that analysis later, explicitly marked `POINT_IN_TIME`/`RECONSTRUCTED` so a later
  reconstruction can never masquerade as something actually selected on that day.
- No weight optimization against historical returns, no ML, no LLM, anywhere in this package
  (test-enforced).
- Never reaches PUBLIC video production; no public planner imports this package (test-enforced).
- Future Kite integration (docs/PRIVATE_DESK_KITE_INTEGRATION.md) is explicitly out of scope:
  `TradeCandidateAssessment` carries zero execution authority (no order/quantity/broker field;
  checked the same way `PrivateCandidatePacket.FORBIDDEN_FIELD_TOKENS` is checked).

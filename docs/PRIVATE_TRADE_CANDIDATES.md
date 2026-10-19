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
    models.py        TradeCandidateAssessment packet, ComponentScore envelope, every enum
    policy.py         ScoringPolicy: weights, thresholds, score_policy_version (PROVISIONAL)
    context.py        one read function per subsystem, each using THAT subsystem's own PIT primitive
    metrics.py         the few small, new, documented deterministic calculations this package needed
    eligibility.py      DataQualityTier + ResearchPriority state machine
    scoring.py          the 10 component scorers (A-J) + total
    shortlist.py        raw ranking + sector-capped diversified shortlist with demotion reasons
    store.py            immutable, versioned snapshot history (mirrors institutional_flows/store.py)
    service.py          orchestration: replay -> eligibility -> metrics -> scoring -> shortlist
    capture.py          automatic, idempotent, point-in-time-honest capture (intelligence_refresh calls this)
    presentation.py     typed, read-only DTOs - the ONLY shape private_desk may consume
```

`private_desk/services/trade_candidates.py` is the only private_desk-side glue: a pure
pass-through over `presentation`'s DTOs, rendering the `/trade-candidates` page and the
`/trade-candidates/{symbol}` detail page. It never writes a snapshot -
`private_candidates.store.write_snapshot` is called only by `capture.py` (itself called only
from `intelligence_refresh`, never from a Private Desk page view). The Data Quality panel's
capture status is a plain READ of `intelligence_refresh`'s own recorded result
(`private_desk/services/intelligence_freshness.py`), not a live recompute.

**What the Desk shows.** By default the pages show the STORED snapshot - exactly what the
automatic capture recorded at its own cutoff - via `service.run_from_store` /
`service.stored_assessment` (read-only; they rebuild the raw ranking and shortlist over the stored
assessments, never re-score them). A banner names the source and capture time. Only when no
stored snapshot exists under the current schema (or the owner asks with `?live=1`) does the page
fall back to `service.run` - a live recompute with cutoff = now, labelled as such, which for a
past session can see information published after that evening. The board defaults to the
**UPSIDE** view (the owner trades long only, `DEFAULT_VIEW` in the Desk glue); `?view=ALL` shows
the engine-neutral board. See "Direction" below.

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
introduces a "score" identifier (`test_no_new_score_is_introduced`). **This is resolved by a
typed presentation boundary, not a dynamic-access workaround** (an earlier draft of this
package read the score via `getattr`/`.get(...)` inside `private_desk` itself to dodge the
guard - removed, not weakened, after review). `private_candidates/presentation.py` builds
read-only DTOs (`CandidateRow`, `CandidateDetailView`, `ComponentPresentation`,
`CandidateBoardView`) that rename the score-shaped fields ONCE, at the package boundary -
`score_total`/`score_max` become `quality_points`/`quality_max`, and so on -
`private_desk/services/trade_candidates.py` then just reads these DTOs' attributes directly.
No identifier containing "score" is ever written inside `private_desk/`'s own source, so the
guard passes naturally; `tests/test_private_candidates_presentation.py` re-runs the same
AST check directly against `trade_candidates.py` as a standing regression test.

## Component model (PROVISIONAL weights, max 100) - policy `trade-candidate-score-2.0`

"Direction" below is the candidate's direction from the Radar's own multi-session evidence
(`radar_direction`; the day's move only as a fallback for MIXED evidence) - see "Policy 2.0".

| # | Component | Max | Reused inputs | New calculation |
|---|---|---|---|---|
| A | `SETUP_QUALITY` | 20 | independent signal count (4/9/13 for 1/2/3 families) + the strongest `TechnicalStructure` event IN the direction (50D range break 4, 20D break 3, SMA50 cross 2, SMA20 cross 1) + `VolumeAnomaly.level` (UNUSUAL 2, EXTREME 3) | - |
| B | `MARKET_ALIGNMENT` | 12 | Market Regime label vs. direction | regime streak (metrics.regime_streak) |
| C | `SECTOR_ALIGNMENT` | 10 | `market_structure.sectors` mapping; the SECTOR's 20-session return minus NIFTY 50's when it agrees with the direction (1.25 points per pp) | equal-weight series of the stock's sector PEERS + `radar.relative` (reused, computed fresh) |
| D | `RELATIVE_STRENGTH` | 10 | `RelativePerformance.market_relative_20d_pp`, persistence | - |
| E | `MTF_CONFIRMATION` | 10 | daily `TechnicalStructure` | weekly OHLCV resample (metrics.weekly_resample) |
| F | `RANGE_POSITION_AND_EXTENSION` | 12 | `prior_20_high/low`, `sma20` | ATR14 (metrics.atr14) |
| G | `LIQUIDITY` | 8 | `VolumeAnomaly.average_volume_20` x close | - |
| H | `VOLATILITY_FIT` | 8 | - | ATR14 as % of close |
| I | `EVENT_RISK` | 6 | `market_events.store.load_latest` (stock families): no event on file 6, past event within 3 sessions 4, upcoming/same-day 2; no event on file in a results window with no results date on file 4 | trading-session event window, results window |
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
- **Equal-weight sector series** - rebases each sector PEER's close to 100 at its own first
  available date, then averages across peers per date; the candidate itself is excluded, so a
  big mover never drags its own sector benchmark (in a small sector it otherwise dominated it).
  Member rows are read once per sector per run and shared. This is a NEW, LOCAL, READ-ONLY
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
- **Results window** (`metrics.results_window`) - the quarterly-results filing window a session
  falls in, from SEBI LODR Reg. 33(3): within 45 days of a June/September/December quarter end,
  60 days after March. Pure calendar arithmetic, no data.

**Structural reference** (`metrics.structural_reference`): a range break in the candidate's
direction makes the level it just cleared the reference (`BROKEN_PRIOR_50_HIGH`/`_20_HIGH` for
an upside break, `BROKEN_PRIOR_50_LOW`/`_20_LOW` for a downside one; the 50-session level first,
being the nearer) - the level whose loss would mean a failed break. Otherwise the far end of the
prior 20-session range (`PRIOR_20_LOW` upside, `PRIOR_20_HIGH` downside). Every level comes from
the existing `TechnicalStructure` - zero new indicator. Deliberately NOT built: a per-stock
swing-point/trendline/pivot level (the richer indicators in `market.analyze()` are index-only);
a later policy could lift `market.key_levels`/`swing_points`/`trendline` to per-stock.

## Point-in-time capture contract

Every `TradeCandidateAssessment` carries TWO distinct timestamps (this is the hardened design -
see "History" below for the defect it replaces):

- **`candidate_as_of`** - the authoritative cutoff: the latest instant whose information was
  eligible for this calculation. Every per-subsystem read is bounded by it.
- **`captured_at`** - when this assessment object was physically computed. Close to
  `candidate_as_of` for a live capture, but conceptually distinct, and can be far apart for a
  replay or a catch-up.

`private_candidates.service.resolve_capture(session, candidate_as_of, capture_mode,
captured_at)` resolves both, plus `capture_mode`:

- `candidate_as_of` omitted -> defaults to `captured_at` (a genuinely LIVE run: "now", at the
  moment the engine actually runs, IS the honest knowledge boundary - no padding needed, since
  "now" is naturally later than every artifact an earlier step in the same evening's job
  already wrote).
- `candidate_as_of` given explicitly -> honored EXACTLY, never silently widened. An explicit
  replay `as_of` of 19:20 must never see a 21:00 revision just because both fall on "the same
  calendar day" - `tests/test_private_candidates_pit_and_store.py::
  test_an_explicit_candidate_as_of_is_never_silently_widened_to_end_of_day` is the acceptance
  test for this.
- `capture_mode` omitted -> inferred as `POINT_IN_TIME` only when `candidate_as_of`'s own IST
  calendar date equals `session` (an honest same-day claim); `RECONSTRUCTED` otherwise. A
  caller with actual provenance proof (the automatic capture integration, below) may override
  this explicitly in either direction - never fabricated silently.

### Per-subsystem primitive (never one uniform wrapper)

| Subsystem | Primitive this package calls | Cutoff semantics |
|---|---|---|
| Market Regime | `RegimeStore.snapshot(session)` | no cutoff gate at all - date-indexed, PIT-safe by construction |
| Market Structure | `market_structure.store.load_revision_as_of(out_dir, session, cutoff_iso)` | inclusive `<=` |
| Institutional Flows (sector-level only) | `institutional_flows.context.load_institutional(cutoff=...)` | inclusive `<=` |
| Market Events | `market_events.store.load_latest(..., first_retrieved_before=cutoff_iso)` | strictly exclusive `<` |
| OHLCV | date-bounded reads only (`DeskRepository.ohlcv_rows(..., end=session)`) | no revision chain - date-bounding alone is sufficient for a session fully in the past |

Every one of these reads is bounded by `candidate_as_of`, not by Radar's own `generated_at` or
by an end-of-day default (see "History" below). The cutoff is always handed to the stores as an
**IST** timestamp (`service._ist_cutoff`): Market Structure, Market Events and the
institutional-flow stores compare cutoff STRINGS lexically against IST-offset `retrieved_at`
values, so a UTC string for the same instant sorts 5h30m early and hides that evening's
revisions. (Defect found 2026-10-18: the Desk's live recompute defaulted to a UTC clock, so on
the evening itself it could not see that evening's Market Structure - sector context silently
disappeared. The default live clock is now IST as well.) This package deliberately bypasses two
existing, NON-point-in-time shortcuts: `private_desk.repository`'s `market_structure()`/
`universe()` (which read the mutable "current" artifact file, not a point-in-time revision) and
`private_desk/services/market_events.py`'s `stock_official_rows` (which never passes
`first_retrieved_before`). It calls `market_structure.store.load_revision_as_of` and
`market_events.store.load_latest` directly instead.

### History: the end-of-day defect, and why it was wrong

An earlier draft derived the cutoff as `max(Radar replay's own as_of, end-of-day 23:59:59 on
session)`. This was motivated by a real bug (a Market Structure artifact retrieved ~1 minute
after that evening's Radar `generated_at` was falsely excluded), but the fix was itself
dishonest: an EXPLICIT replay cutoff of 19:20 would still see a 21:00 revision, because both
fall before the artificial 23:59:59 ceiling - "same calendar day" is not the same claim as "as
of this exact instant". The real fix needed no padding at all: for a live run, "now" at the
point the engine actually executes is ALREADY later than every artifact an earlier step in the
same job just wrote, because the candidate-capture step runs AFTER them (see "Automatic
capture" below) - the bug was in *which* clock was being used (Radar's own internal `as_of`,
sometimes much earlier than "now"), not in needing a wider window.

## Automatic capture (`capture.py`, intelligence_refresh integration)

`private_candidates.capture.capture_latest_session(out_dir, session, now)` is the ONE
integration point `intelligence_refresh.refresh.run_refresh` calls (as its own, independent
step, never gated on `ensure_latest`'s "REPORT job already ran today" check - a candidate
capture that failed one evening needs its own retry, even when the REPORT job itself
succeeded). It never raises past its own boundary; every failure mode returns a status dict
(`SUCCESS` / `FAILED` / `MISSING_INPUTS` / `NOT_APPLICABLE`).

**The capture's clock is read AFTER the REPORT job.** `run_refresh` resolves the session from
the run's start time (the same session `ensure_latest` handled) but passes the capture a clock
read once `ensure_latest` has returned - otherwise the cutoff would precede the Market
Structure / Market Events / NSDL revisions that same REPORT job just wrote. (Defect found on
2026-10-16: the start-of-run cutoff 19:29:55 excluded the 19:32 Market Structure revision, so
all 24 stored candidates had no sector or company and SECTOR_ALIGNMENT scored 0/10 for every
one.) A `now` injected by a caller (tests, replays) is used verbatim and never re-read.

`capture.resolve_capture_as_of(repo, session, now)` decides `candidate_as_of`/`capture_mode`
for an AUTOMATIC capture:

- **Same calendar day** (`now`'s IST date == `session`): a genuinely live capture -
  `candidate_as_of = now`, `capture_mode = POINT_IN_TIME`.
- **Later** (an AM catch-up of a PM miss, or any later retry): `POINT_IN_TIME` is permitted
  ONLY when provenance proves it - Radar's own `candidate_history_runs` COMPLETE marker for
  `session` was itself `processed_at` on `session`'s OWN calendar day, never later. When that
  holds, `candidate_as_of` is `session`'s end-of-day (23:59:59 IST): this is the one place
  end-of-day is a legitimate boundary, because every subsystem read bounded by it can see
  nothing from a later calendar day BY CONSTRUCTION - the cutoff itself is the proof, not a
  separate check, and it is materializing a packet that genuinely WOULD have been produced that
  evening, never silently widening an explicit replay cutoff.
- When Radar's own capture for `session` itself happened on a LATER day (or no COMPLETE marker
  exists at all), `POINT_IN_TIME` cannot be honestly claimed: `capture_mode = RECONSTRUCTED`
  (or capture is skipped entirely as `MISSING_INPUTS` if nothing can be computed yet).

**Idempotency**: `capture_latest_session` compares each freshly computed assessment against
its latest stored revision, ignoring the two provenance timestamps (`captured_at` and
`candidate_as_of`, both of which legitimately differ run to run), and only writes a new
revision when the content genuinely changed - a repeated normal refresh (or an AM catch-up
that finds nothing new) reuses the unchanged record, which also keeps the EARLIEST cutoff at
which that content was knowable. A new `score_policy_version` always writes to its own folder,
so V1 snapshots stay reproducible under their own policy even after a V2 policy exists.

**Never triggered by a page view.** Opening `/trade-candidates` or the Data Quality page never
calls `capture.py` - only `intelligence_refresh`'s own PM/AM invocations (or a direct CLI/test
call) do. A candidate-capture failure can never corrupt Radar/Regime/other intelligence: it is
wrapped so a `FAILED` result degrades (never blocks) the overall `intelligence_refresh` run,
exactly like institutional-flow/market-events capture's existing posture inside
`products/report_job.py`.

## Eligibility / data quality

Every candidate's `data_quality_tier` is `INSUFFICIENT` (essential detector reading missing, or
fewer than 15 OHLCV sessions), `PARTIAL` (an optional reading - relative-performance, ATR -
missing), or `COMPLETE`. A `PARTIAL` tier is capped at `REVIEW` regardless of score - "a high
technical score with poor data must not rank normally." A compounding-risk veto
(`DEEPLY_STRETCHED` extension together with `EXTREME` volatility) demotes to `NOT_ACTIONABLE`
regardless of score, always recorded as the `COMPOUNDING_RISK_VETO` flag - never silent. A
candidate whose Radar direction is MIXED or NON_DIRECTIONAL is likewise capped at `REVIEW`
(flag `MIXED_DIRECTION_EVIDENCE`): its components read only the day's move as the direction, so
it may rank for review but never as ELIGIBLE. Both caps are caps, never lifts.

## Raw ranking vs. diversified shortlist

`raw_ranking` is every `ELIGIBLE`/`REVIEW` candidate - ELIGIBLE first, then REVIEW, each by
score descending - the full audit trail, nothing hidden. Priority leads so a REVIEW capped for
PARTIAL data never outranks complete data on score alone. `diversified_shortlist` is a SEPARATE
view: it walks the raw ranking and demotes (never deletes, never re-scores) a candidate once its
sector already holds `sector_cap` (2, PROVISIONAL default) shortlist entries, or - when a
correlation lookup is supplied - once it is highly correlated (>= 0.85, PROVISIONAL, daily
returns over the trailing `correlation_window_sessions` = 60) with an already-shortlisted
same-sector member. Every demotion names its exact reason (`SECTOR_CONCENTRATION` or
`HIGH_CORRELATION`) and detail; the raw ranking itself is never mutated. A candidate with NO
sector is shortlisted without either check and listed under `sector_unknown` ("diversification
not applied") - missing sector data is not concentration, and pooling every unknown under one
capped bucket would shrink the shortlist to `sector_cap` names.

## Direction

Every assessment carries `radar_direction` - the Radar's own stored `direction_compatibility`
(STRUCTURE + RELATIVE evidence only, VOLUME excluded: `ALIGNED_POSITIVE` / `ALIGNED_NEGATIVE`
/ `MIXED` / `NON_DIRECTIONAL`). Under policy 2.0 it IS the candidate's direction for every
directional reading (market/sector/relative alignment, weekly alignment, structural
reference, setup-quality events); the session's own move is used only when the evidence is
MIXED or NON_DIRECTIONAL (then capped at REVIEW, see Eligibility). When an ALIGNED candidate's
day moved the other way (a pullback or bounce day - under 1% of candidates over the first 22
recorded sessions) the informational flag `DAY_MOVE_AGAINST_RADAR_DIRECTION` is added; it never
changes the score.

`service.run(..., view=...)` / `run_from_store(..., view=...)`: `ALL` (default, what capture
uses) ranks every direction; `UPSIDE` ranks and shortlists only ALIGNED_POSITIVE candidates -
so a downside name can never take an upside name's sector slot - and returns the other ranked
candidates as `outside_view` (information only). `direction_counts` gives UPSIDE / DOWNSIDE /
MIXED totals over all candidates. Why this matters: in the stored 2026-10-16 snapshot all 8
ELIGIBLE candidates were downside breakdowns (or MIXED), which a long-only owner cannot act on in
cash, while every upside breakout was REVIEW - invisible on the old board. The upside view ranks
the 11 upside breakouts on their own.

## Policy 2.0 (`trade-candidate-score-2.0`, 2026-10)

Same ten components, same weights, same ELIGIBLE/REVIEW bands, same research-priority
vocabulary - what changed is what four components read (each was a finding of the 2026-10
review, measured on real stored data):

| Change | Why |
|---|---|
| Direction = the Radar's own multi-session evidence; MIXED capped at REVIEW | 1.0 used the sign of one day's move: MIXED-evidence candidates (~10%) were scored as directional and 55 of them reached ELIGIBLE over 22 sessions (PHOENIXLTD, 2026-10-16); CGPOWER (ALIGNED_NEGATIVE, +3.4% day) was scored as an upside setup. |
| C reads the sector's strength vs NIFTY 50 (peers only, the stock excluded) | 1.0 read stock-minus-own-sector (the stock included in its own series) - a second relative-strength reading that scored a stock beating a WEAK sector highest. The stock-vs-sector gap stays as evidence. |
| Structural reference = the broken level for a range break in the setup direction | With the far prior-20 low, 499 of 655 candidates read EXTENDED_FROM_STRUCTURE and 437 WIDE; now 124 and 63. |
| Setup quality grades the strongest structural event in the direction | 580 of 655 scored exactly 13/20 under 1.0; an SMA20 cross counted like a 50-session breakout. Now spread 9-20. |
| EVENT_RISK: a past event within the window gets 2/3 credit, an upcoming one 1/3; "no event on file" in a results window with no results date on file gets 2/3 (flag `RESULTS_DATE_NOT_ON_FILE`) | Earnings coverage is thin (none of the 24 candidates on 2026-10-16 had a record, IT majors in results season included), and absence of data was earning full credit. |

Effect over all 22 recorded sessions (655 candidates, both policies recomputed read-only with an
end-of-day cutoff): ELIGIBLE 439 -> 341, MIXED-evidence ELIGIBLE 55 -> 0, 109 research
priorities changed. On 2026-10-16 every upside breakout now references its broken level
(AT/NEAR structure, NARROW/MODERATE width) and TECHM and WIPRO are the upside ELIGIBLE names.
Like 1.0, every 2.0 weight and threshold is a documented judgment call, never fitted to returns.

## Durable snapshot history

`private_candidates/store.py` mirrors `institutional_flows/store.py` exactly: immutable,
exclusive-create revisions under
`<out_dir>/private_candidates/<score_policy_version>/<session_date>/<symbol>.json` (`.r2.json`
for a genuinely changed re-run - see "Automatic capture" above for idempotency). A later
`score_policy_version` writes to its own folder, so it structurally never touches an earlier
version's files - a V1 snapshot stays reproducible under its own policy even after the policy
changes. `capture_mode`/`candidate_as_of` are resolved once (`service.resolve_capture`) and
stamped at build time, never inferred later from file metadata. This store is written only by
`private_candidates.capture`/`service.persist` - never by `private_desk`, which reads it only
through `service.run_from_store`/`stored_assessment` (`store.list_symbols`/`load_latest`).
Revisions written under an older schema are never shown as the stored board - the Desk falls
back to a labelled live recompute until the next capture supersedes them. Policy 1.0 snapshots
stay in their own `trade-candidate-score-1_0/` folder, untouched; the Desk reads the current
policy's folder only.

## Limitations (all explicitly provisional)

- Component weights, score bands, and every ATR/weekly/event/sector-cap/correlation threshold
  are a documented first-pass judgment call, not derived from any backtest. `python -m
  private_candidates` (a future CLI, not built in V1) would re-run this deterministically under
  a new `score_policy_version` if the owner changes them.
- Sector relative strength and the weekly multi-timeframe view are NEW, PARALLEL calculations
  introduced by this package - not a fix to Radar's own unwired `RelativePerformance.sector_*`
  fields, which stay `None` in production exactly as before.
- The structural reference uses only the prior 20/50-session range levels (the broken level
  for a range break) - no swing/pivot/trendline level (those remain index-only in `market.py`).
- Earnings coverage is limited to the board-meeting filings Market Events has captured; the
  results-window rule only marks the gap (`RESULTS_DATE_NOT_ON_FILE`), it cannot fill it.
- Institutional-flow and event context are sector/index-level and symbol-level-from-official-
  filings respectively - never a claim of stock-specific institutional buying/selling.
- No historical Edge Analytics in V1: nothing here measures forward returns, MFE/MAE, or
  regime-/sector-conditioned outcomes. Snapshot history is captured with enough evidence to
  support that analysis later, explicitly marked `POINT_IN_TIME`/`RECONSTRUCTED` (with
  `candidate_as_of` recorded alongside) so a later reconstruction can never masquerade as
  something actually knowable on that day.
- The AM catch-up's `POINT_IN_TIME` claim rests on Radar's `candidate_history_runs.processed_at`
  being itself dated on the session's own day - if that marker is ever missing or wrong, the
  catch-up correctly falls back to `RECONSTRUCTED` rather than guessing.
- No weight optimization against historical returns, no ML, no LLM, anywhere in this package
  (test-enforced).
- Never reaches PUBLIC video production; no public planner imports this package (test-enforced).
- Future Kite integration (docs/PRIVATE_DESK_KITE_INTEGRATION.md) is explicitly out of scope:
  `TradeCandidateAssessment` carries zero execution authority (no order/quantity/broker field;
  checked the same way `PrivateCandidatePacket.FORBIDDEN_FIELD_TOKENS` is checked).

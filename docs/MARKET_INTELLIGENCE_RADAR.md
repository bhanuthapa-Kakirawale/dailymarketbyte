# Market Intelligence Radar — Packet 1 / 1.1: Unusual Volume, Packet 2: Technical Structure, Packet 3: Relative Performance, Packet 4: Evidence Composition

Phase 4.2 begins a new capability: detecting developments that are hard for an individual
trader to find by hand across many securities, rather than only reporting what is already easy
to read off a financial website. Packet 1 implements the first detector - unusual trading
volume - and the shape a later packet will plug additional detectors into. Packet 1.1 removes
the limitation Packet 1 shipped with: the detector could only classify a stock that happened to
already be one of the day's ~10 video movers, defeating the point of a 200-500 stock radar.
Packet 1.1 adds a second acquisition path so the Radar can see far more of the universe, without
touching video, editorial, or the publication gates.

```
                                    Editorial path (unchanged)
                          market.get_movers (top ~10) -> adapters.observe_movers
                                              |
                                              v
Caller-supplied universe -----------> today's MarketReport (PRE_MARKET) ---\
        |                                                                   \
        v                                                                    v
radar.acquisition.acquire_universe_scan_report                    radar.volume.scan_universe
market.get_universe_relative_volume (whole universe)                        |
        |                                                                    v
        v                                                          VolumeRadarSnapshot
canonical MarketHistory (RADAR_SCAN report, DB-only)  -------------------^   |
                                                                              v
                                                                     JSON artifact (optional)
```

There is no video or editorial arrow. This is detection infrastructure, not a new publication
gate — nothing in `main.py`, `video.py`, `editorial/`, or the three publication gates changes.
`radar.acquisition` is a separate, explicitly-invoked module (a test, a manual script, or a
future scheduled task) - never called from `main.py`'s orchestration.

## What it is not

- Not "top volume" and not "top gainer with high volume." It answers a narrower question: is
  today's volume unusual **relative to this stock's own recent history**?
- Not a BUY/SELL signal, a rating, or a ranking of investments. `AnomalyLevel` (`ELEVATED` /
  `UNUSUAL` / `EXTREME`) is a descriptive label for how far a reading sits from a stock's own
  history, nothing more.
- No LLM anywhere in this package.
- No fundamentals, filings or options detection yet - those are later packets.
  `StockRadarCandidate.fundamental_event` (below) reserves their place without implementing
  them. Technical-structure (Packet 2), relative-performance (Packet 3) and their composition
  (Packet 4) are implemented.
- Composition is not a score or a recommendation either: `independent_signal_count` and
  `AttentionLevel` are coarse, transparent counts - never a 0-100 number, and never BUY/SELL/
  bullish/bearish/target/conviction language. See the Packet 4 section below.
- Price direction is never an input to classification. A modest price move with extreme
  relative volume is exactly the case this detector exists to surface, and a large price move
  on ordinary volume is exactly the case it must not flag.

## Deterministic, not generated

    DATA -> RULES -> DETECTION

No model classifies a reading, computes a percentile, or writes `why_flagged`. Given the same
report, the same database and the same universe, `scan_universe` produces byte-identical output
(with `as_of` injected) - asserted directly in `tests/test_volume_radar.py`.

## Relative volume is reused, never redefined

The Radar never recomputes relative volume from raw prices or volumes. Every reading is the
`STOCK_RELATIVE_VOLUME` Fact value the pipeline already computed (`market.relative_volume`,
Phase 3's `current_volume / mean of the prior 20 sessions`, current session excluded), read as
it stands. Only readings whose observation metadata carries `definition_version == "2.0"` enter
a comparison - today's reading or a historical one:

| Situation | Effect |
| --- | --- |
| today's reading is `definition_version` 2.0 | classified normally |
| today's reading is an older definition (or absent) | not classified; a warning names it; the symbol stays in `scanned`, never in `anomalies` |
| a historical reading is 2.0 | counted in `prior_sessions_available` |
| a historical reading is an older definition | excluded from the comparable sample |

Historical readings are collapsed to canonical `(report_id, fact_id)` identity before anything
is counted - a fact may carry several observations, and counting observations instead of facts
would inflate the sample and the rank. This mirrors `intelligence/movers.py`'s
`analyse_relative_volume`; `tests/test_volume_radar.py::test_dedup_matches_movers_semantics`
cross-checks the two implementations agree on the same fixture.

## Classification thresholds

Configurable on `radar.thresholds.VolumeThresholds`, conservative by default:

| Level | RVOL alone | RVOL + percentile (needs ≥20 comparable sessions) |
| --- | --- | --- |
| `ELEVATED` | ≥ 2.0x | upgraded to `UNUSUAL` at ≥ 90th percentile |
| `UNUSUAL` | ≥ 3.0x | upgraded to `EXTREME` at ≥ 97th percentile |
| `EXTREME` | ≥ 5.0x | stays `EXTREME` |
| not flagged | < 2.0x | — |

Percentile can only **upgrade** the band RVOL alone already earned, never invent a level or
demote one. With fewer than 20 comparable sessions, percentile is omitted entirely (with a
warning) and classification falls back to RVOL alone - the fallback that always applies when
history is thin. All five numbers are fields on a frozen dataclass, fully overridable by a
caller without touching `radar/volume.py`.

## `highest_rvol_in_n_sessions` wording rule

```python
{"value": 4.5, "session_date": "2026-08-11", "fact_id": "...",
 "n_requested": 20, "n_recorded": 7, "is_complete_window": False}
```

`n_requested` is what the caller asked for (default 20); `n_recorded` is what canonical history
actually holds. When `is_complete_window` is `False`, prose built from this must say **"highest
in its N recorded sessions"**, never **"N trading days"** - the window is not what was
requested, only what is actually known. When `True`, "highest in its last N sessions" is
accurate. The detector computes this independently of classification: a thin history still
produces a truthful, warned statement rather than none at all.

## Scan Universe vs Editorial Movers

Two disjoint sets of `STOCK_RELATIVE_VOLUME` facts can exist for the same session, under two
different `report_id`s:

| | Editorial Movers | Scan Universe |
| --- | --- | --- |
| Created by | `adapters/market_adapter.py::observe_movers` | `adapters/market_adapter.py::observe_universe_relative_volume` |
| Fetched by | `market.get_movers` (ranked, `n`-truncated) | `market.get_universe_relative_volume` (unranked, every usable symbol) |
| Report | that day's real `ReportType.PRE_MARKET` report - the video's input | a distinct `ReportType.RADAR_SCAN` report, DB-only, no JSON artifact |
| Size | ~10 symbols (`TOP_N` gainers + losers) | up to the full caller-supplied universe (200-500) |
| Facts emitted | `STOCK_CLOSE`, `STOCK_CHANGE_PCT`, `STOCK_RELATIVE_VOLUME` | `STOCK_RELATIVE_VOLUME` only (see RVOL-only scope, below) |
| Read by | video, editorial, `intelligence/` | `radar.volume.scan_universe` (via its `scan_report` parameter) |

They never overlap by construction: `radar.acquisition.acquire_universe_scan_report` takes an
`already_covered` set (today's real mover symbols) and excludes them before fetching, so a
symbol never gets two independent `STOCK_RELATIVE_VOLUME` facts for the same
`(instrument, market_date)` under two different `report_id`s - that would double-count the
session in every later percentile window. Both sets are queried the same way by
`intelligence.history.HistoricalWindow` (`Metric.STOCK_RELATIVE_VOLUME`, no `instrument`
filter), which already fetches every instrument for a metric in one query regardless of which
report(s) it came from.

## Where universe-wide RVOL lives

Canonical `MarketHistory`, via the second, distinctly-identified `RADAR_SCAN` report above -
not the day's real `MarketReport`, and not left unpersisted.

Two things this is deliberately not:

- **Not inside the real `MarketReport`.** "IF A NUMBER IS DISPLAYED IN THE VIDEO, IT EXISTS IN
  THE MARKETREPORT" is a one-way invariant, not its converse - most of a 200-500 stock scan is
  never displayed, and stuffing it into `MarketReport.facts` would make that field mean
  something different for every future reader of the JSON artifact.
- **Not ephemeral/unpersisted.** Packet 1's whole percentile/rank differentiator ("higher than
  N of its previous M comparable readings") requires *prior sessions'* RVOL already sitting in
  canonical history. A non-mover symbol whose RVOL is computed fresh every run and thrown away
  can never accumulate that history, which would defeat exactly what Packet 1.1 exists to
  unlock.

`save_report` already requires nothing publication-specific - `reports.report_type` is a free
column and `facts`/`observations` are keyed generically - so `acquire_universe_scan_report`
reuses it verbatim: same atomic/idempotent write, same tables, `artifact_path=None` (no JSON
file for the video or a human to ever read). This does not touch the "canonical report is
persisted exactly once per production run" guarantee - that guarantee is about `main.run`'s
orchestration specifically (enforced by `tests/test_immutability.py`), and
`radar.acquisition` is never called from `main.py`.

## Why detection must be independent of editorial mover selection

Packet 1 already established that `radar/` never acquires data itself, only reads canonical
`MarketReport`/`MarketHistory`. Packet 1.1 keeps that boundary intact by putting the new
acquisition logic in its own module (`radar/acquisition.py`) rather than changing
`main.build_report` or `market.get_movers` to fetch a wider universe - the day's real
publication report is built exactly as before, and a completely separate, optional step can
widen what the Radar can see without the two ever needing to agree on timing, size, or ranking.
An index's top gainers and losers are an editorial choice about what a 45-60 second video has
room to show; the Radar's job is the opposite of editorial - see everything, flag what is
unusual, exercise no judgment about what is interesting.

## RVOL-only scope

`observe_universe_relative_volume` emits `STOCK_RELATIVE_VOLUME` observations only - never
`STOCK_CLOSE` or `STOCK_CHANGE_PCT`. This is a hard boundary, not a simplification of
convenience: `intelligence/movers.py::analyse_recurrence` infers "was a tracked top mover"
purely from the *existence* of a `STOCK_CHANGE_PCT` fact for a symbol on a session, with no
`bucket` check on that count. That is safe today only because every `STOCK_CHANGE_PCT` fact in
canonical history currently comes from `observe_movers`. If the scan path also emitted price
facts for hundreds of non-mover symbols a day, nearly every liquid stock would silently look
like a `FREQUENT_MOVER` on screen, corrupting editorial content without a single line of
`editorial/` or `intelligence/` being touched. `tests/test_radar_acquisition.py::
test_analyse_recurrence_is_unaffected_by_scan_report_facts` guards this directly. A consequence:
`VolumeAnomaly.price_change_pct` stays `None` for an anomaly sourced only from a scan report
(non-mover symbols) - `_build_anomaly` already handles a missing price gracefully.

## Remaining limitation: universe acquisition itself

`scan_universe` accepts an arbitrary caller-supplied `universe` list and never hardcodes an
index's constituents - a test greps for common tickers to enforce this. `market.get_universe`
already supports NIFTY 50/100/200/500 constituent lists (or a custom dict). What Packet 1.1
does not do is decide *when* or *how often* `acquire_universe_scan_report` runs, or make it part
of any scheduled job - that orchestration question (a new cron entry, a manual trigger, or a
future packet's responsibility) is deliberately left open, per the "no video/editorial
integration" boundary this phase holds to.

## Traceability

Every `VolumeAnomaly` carries `supporting_fact_ids` (today's fact plus every comparable
historical fact used) and `supporting_report_ids`, plus a deterministic `why_flagged` sentence
built from the same numbers the fields expose. A reading that cannot be traced back to the
canonical records behind it is an assertion, not a detection.

## No mutation, no acquisition

`scan_universe` reads `report.facts_for(...)` and canonical history through the read-only
`intelligence.history.HistoricalWindow` (`get_recent_metric_points`, batched once across the
whole universe, not once per instrument) and calls neither `history.save_report` nor
`report.add_fact`. A test snapshots all six canonical tables and the report's own JSON before
and after a scan and asserts equality. A separate test greps the package for provider/network/
model references and fails if one appears.

## Output artifact

`radar.engine.save_snapshot` writes `output/radar/volume_YYYY-MM-DD[_DEMO].json`, carrying
`radar_schema_version` and `calculation_version`. This is not wired into `main.py` - Packet 1
adds no orchestration call; a caller builds `MarketReport` + `MarketHistory`, calls
`scan_universe`, and persists the result itself.

This is separate from the `RADAR_SCAN` report Packet 1.1 persists: `save_snapshot`'s JSON is a
disposable, regenerable-at-any-time detection *result* (what was found, given the facts that
existed when it ran); the `RADAR_SCAN` report is canonical *input* data (DB-only, no JSON) that
future runs' percentile calculations depend on. Losing a `VolumeRadarSnapshot` JSON costs
nothing but a re-run; losing `RADAR_SCAN` rows costs real accumulated history.

## Cold start and failure

| Situation | Effect |
| --- | --- |
| no historical database (`history=None`) | current-reading classification still works via the RVOL-only fallback; a warning is recorded |
| database unreadable | a warning is recorded; symbols with a current reading still classify on RVOL alone |
| a universe member has no RVOL fact today | recorded in `scanned`/`skipped`, never guessed |
| fewer than 20 comparable historical sessions | percentile omitted with a warning; RVOL-only classification still applies |
| symbol missing from the bulk universe fetch | `skipped[symbol] = "no_recap_row"` (Packet 1.1) |
| symbol's session row exists but Close is still NaN after 3 backfill retries | `skipped[symbol] = "backfill_pending"` (Packet 1.1) |
| symbol's previous-close date doesn't match the expected prior session | `skipped[symbol] = "date_gap"` (Packet 1.1) |
| symbol has fewer than 20 valid prior sessions (IPO, long suspension) | `skipped[symbol] = "insufficient_relative_volume_history"` (Packet 1.1) |

These specific reasons come from `market.get_universe_relative_volume`'s `skip_reasons` return
value and reach `VolumeRadarSnapshot.skipped` via `scan_universe`'s `acquisition_skip_reasons`
parameter; without it, every skip still falls back to the generic "no STOCK_RELATIVE_VOLUME
fact in today's report" message.

## Packet 2: Technical Structure

```
                                    Editorial path (unchanged)
                                              |
                                              v
Caller-supplied universe -----------> today's MarketReport (PRE_MARKET) ---\
        |                                                                   \
        v                                                                    v
radar.technical_acquisition.build_universe_technical_series                (not consumed here)
market.get_universe_technical_series (whole universe, one bulk fetch)
        |
        v
radar.technical.scan_technical_universe
        |
        v
TechnicalRadarSnapshot -> JSON artifact (optional, radar.engine.save_technical_snapshot)
```

Same "detection infrastructure, not a publication gate" boundary as Packet 1/1.1: nothing in
`main.py`, `video.py`, `editorial/`, or the three publication gates changes, and nothing here
is called from `main.py`'s orchestration.

### What it answers

> Did the stock's PRICE STRUCTURE materially change?

Not a prediction, not a rating, not a BUY/SELL/bullish/bearish label. `TechnicalEventType`
member names are deliberately structural (`BREAK_ABOVE_20D_RANGE`, `CROSS_ABOVE_SMA20`, ...)
and `radar.technical` never translates them into investment language.

### Why this detector does not need canonical `MarketHistory` to accumulate first

Packet 1.1's relative-volume *percentile* genuinely needs multiple days of persisted
`STOCK_RELATIVE_VOLUME` facts to compare against - that is what `radar.acquisition` persists a
`RADAR_SCAN` report for. Packet 2's core numbers do not have that problem: a single bulk
`yfinance` fetch (`market.get_universe_technical_series`, ~1 year of trailing sessions per
symbol, the same "one download, whole universe" shape `get_universe_relative_volume` already
uses for RVOL) already carries every session the 20-session, 50-session and range-compression
windows need. So Packet 2 introduces **no new canonical Facts and persists nothing** -
`radar/technical.py` is handed the already-fetched per-symbol OHLC list directly, and
`TechnicalStructure.supporting_session_dates` names the acquired session dates that backed a
reading instead of pointing at canonical fact ids, which don't exist for this data. This is a
deliberate, documented scope decision, not an oversight: fabricating fact ids for data that was
never persisted as a Fact would be dishonest provenance.

A consequence: nothing here builds up "first close above its prior 20-session range in 47
recorded sessions"-style historical-significance statements (packet spec section 9) - that
would require the same kind of canonical accumulation Packet 1.1 built for RVOL, and was left
as an explicit follow-up rather than implemented now, to keep correct structural detection
(sections 1-8) the priority.

### Two window conventions, used side by side on purpose

* **Range windows (20/50-session high/low) are prior-N-EXCLUSIVE** - the same convention
  `market.relative_volume` uses. The current session's own high/low never participates in the
  range judging it; `prior_20_high`/`prior_20_low`/`prior_50_high`/`prior_50_low` are built
  from `series[:-1]` (everything strictly before the current session), never `series` itself.
  A close exactly equal to the prior high/low is NOT a break (`>`/`<`, never `>=`/`<=`).
* **Moving averages (SMA20/SMA50) are the conventional INCLUSIVE rolling mean** - the current
  session's own close counts toward its own SMA, matching how every trading platform computes
  an SMA. A `CROSS_*` event fires only on an actual TRANSITION versus the immediately prior
  session (`close > SMA` on session N-1 flips to `close < SMA`, or vice versa) - "close is
  above its SMA" is never re-reported as a new event every day it remains true.

### Break distance sign convention

`break_distance_pct` is `(close / prior_high - 1) * 100` for an upper break (positive: close
sits above the range) and `(close / prior_low - 1) * 100` for a lower break (negative: close
sits below the range). Both live in each `TechnicalEvent.evidence` dict alongside the exact
numbers that produced them - `TechnicalEvent.why` renders the same numbers as a deterministic
sentence, never a template the model fills in.

### Range compression

`RANGE_COMPRESSION` (`radar.thresholds.TechnicalThresholds`) compares the most recent
`compression_window`-session (default 5) high/low range, normalised by that window's own last
close, against the median of `compression_lookback_windows` (default 10) PRIOR
non-overlapping windows of the same size. It fires only when the recent range is at or below
`compression_ratio` (default 0.6) times that median AND the full prior-window sample exists -
a partial sample reports `recent_5d_range_pct` as context but leaves `median_5d_range_pct` and
the event both absent, exactly like Packet 1's percentile falls back to "omitted" rather than
computing over a shorter window and calling it the same statistic. This is an observed state,
never "breakout imminent" - no Bollinger Bands, no volatility forecast.

### Data quality

`market.get_universe_technical_series` drops an individual session with invalid OHLC
(`high < low`, or any of high/low/close `<= 0`) from that symbol's series rather than
discarding the whole symbol, and reuses `get_universe_relative_volume`'s exact
`no_recap_row`/`backfill_pending`/`date_gap` skip vocabulary plus a new
`insufficient_technical_history` (fewer than `market.MIN_TECHNICAL_SESSIONS` = 21 valid
sessions - one short of even the shortest, 20-session, detector). A symbol with 20-49 valid
prior sessions still gets a full 20-session result; 50-session fields are simply `None` with a
`data_quality` note naming why - never silently computed over a shorter window.

### No composite score, no ranking

Exactly like Packet 1: `TechnicalStructure` returns discrete evidence (a list of
`TechnicalEvent`s plus the raw numbers), never a "technical score" or "bullish score". Later
editorial logic decides which evidence combinations, if any, deserve attention.
`StockRadarCandidate.technical_anomaly` (reserved in Packet 1, wired in Packet 2) is the
envelope a future composite packet will read alongside `volume_anomaly` - `radar/technical.py`
itself never imports anything from `radar/volume.py` and knows nothing about RVOL
classification, and vice versa (each detector is independently correct or incorrect on its
own).

## Packet 3: Relative Performance

```
                                    Editorial path (unchanged)
                                              |
                                              v
Caller-supplied universe -----------> today's MarketReport (PRE_MARKET) ---\
        |                                                                   \
        v                                                                    v
radar.technical_acquisition.build_universe_technical_series                (not consumed here)
market.get_universe_technical_series (whole universe, one bulk fetch, REUSED - not re-fetched)
        |
        v
radar.relative_acquisition.build_market_benchmark_series (^NSEI, ONE call)
radar.relative_acquisition.build_sector_benchmark_series (market.SECTORS, one call per sector)
        |
        v
radar.relative.scan_relative_performance_universe
        |
        v
RelativePerformanceSnapshot -> JSON artifact (optional, a future radar.engine.save_relative_snapshot)
```

Same "detection infrastructure, not a publication gate" boundary as Packets 1/1.1/2: nothing in
`main.py`, `video.py`, `editorial/`, or the three publication gates changes, and nothing here is
called from `main.py`'s orchestration.

### What it answers

> Is a stock behaving unusually strongly or weakly relative to its market and, where a reliable
> mapping exists, its sector?

Not price prediction. Not classical chart-based "Relative Strength" (no RSI, no Mansfield RS) -
the calculation is a plain return differential:

```
relative_performance_P = stock_return_P - benchmark_return_P
return_P = (current_close / close_P_sessions_ago - 1) * 100
```

in percentage points, for P in `radar.relative.WINDOWS = (1, 5, 20)` sessions. The current
session is included (a P-session return needs P+1 closes) because this detector describes
*current* relative performance, not a historical-significance statement. Absolute return alone
is insufficient context - packet spec Case B (`RelativePerformance.sector_relative_5d_pp`
example: a stock up 3.0% in a sector up 5.2% is at **-2.2pp**, i.e. it underperformed its sector
despite a positive own return) is exactly the case this detector exists to surface, and
`test_relative_radar.py::test_stock_vs_sector_relative_performance` locks it in.

### Acquisition is reused, not duplicated (no N+1)

Packet 3 issues exactly one new network call beyond what a technical-structure scan already
makes: the market benchmark (`radar.relative_acquisition.build_market_benchmark_series`,
`market.history("^NSEI", ...)`, the same generic single-ticker path `market.get_globals`/
`market.get_sectors` already trust). Per-symbol OHLC series are NOT re-fetched - `radar.relative
.scan_relative_performance_universe` takes the exact `series_by_symbol` shape `radar.technical_
acquisition.build_universe_technical_series` already returns (only `close`/`date` are read;
`high`/`low`, if present, are ignored). Sector index series
(`build_sector_benchmark_series`) are fetched once per sector (12 total, `market.SECTORS`),
never once per stock - `tests/test_relative_acquisition.py::
test_build_sector_benchmark_series_one_fetch_per_sector` asserts exactly `len(market.SECTORS)`
calls regardless of universe size.

### Market benchmark

`NIFTY 50` (`^NSEI`) only, this packet. `radar.relative`'s `benchmark_source` parameter names
the source string carried onto every `RelativePerformance.benchmark_source` for traceability;
the architecture does not prevent a second benchmark later, but none is wired in now.

### Sector-relative: mapping is caller-supplied, never fabricated

No reliable stock -> sector mapping exists anywhere in this codebase yet (`market.py` has
`SECTORS`, the 12 sector INDEX tickers `market.get_sectors` already trusts, but nothing mapping
an individual stock symbol to one of them). Packet 3 does not invent one. `scan_relative_
performance_universe`'s `sector_map` (`{symbol: sector_label}`) and `sector_series_by_name`
(`{sector_label: series}`, from `build_sector_benchmark_series`) are both optional and default
to empty - every symbol's `sector`, `sector_return_*` and `sector_relative_*_pp` are simply
`None`, with a `data_quality` note naming why ("no sector mapping supplied for this symbol"),
until a caller supplies a mapping it trusts. Market-relative output is entirely unaffected by a
missing or failed sector mapping (`tests/test_relative_radar.py::
test_sector_absent_mapping_leaves_sector_relative_unavailable`,
`test_sector_mapped_but_benchmark_series_absent`) - packet spec section 14's "market-relative
output should survive sector-data failure" requirement.

### Session alignment (packet spec section 10)

The single most load-bearing rule in this detector: a stock's and a benchmark's returns are
compared **only when they are computed over the exact same pair of calendar dates**, never by
assuming the two calendars line up by session count. `_return_pct(series, p)` returns the
stock's own `(prior_date, current_date)` for window `p`; `_benchmark_return_between(by_date,
current_date, prior_date)` then looks up the benchmark's close on those SAME two dates (a plain
`{date: close}` dict built once per benchmark by `_series_to_close_by_date`), never on "the
benchmark's own Nth-session-ago" close. If either date is missing from the benchmark (or
sector) series, that window's relative figure is `None` with a `data_quality` note - never
silently substituted (`test_missing_benchmark_session_leaves_market_relative_unavailable`).

A window's absence is also independent of every other window's availability
(`test_partial_window_availability_5d_available_20d_unavailable`): 5D can be present while 20D
is not, and the caller must read each window on its own, never infer one from another.

A stock whose own series does not end exactly at the requested `session_date` (suspended,
stale, or a backfill still pending) is not partially computed - the whole symbol is recorded in
`skipped`, never guessed at (`test_stale_current_session_is_skipped_not_substituted`), because
every window's "current" leg would otherwise be silently wrong.

### Persistence classification

`RelativePersistenceState` (`radar.thresholds.RelativePerformanceThresholds
.persistence_threshold_pp`, default 1.5pp) reads ONLY the 5D and 20D market-relative figures -
never the 1D figure, which is far too noisy to say anything about sustained behaviour on its
own:

| Condition | State |
| --- | --- |
| 5D and 20D both `> threshold` | `PERSISTENT_POSITIVE` |
| 5D and 20D both `< -threshold` | `PERSISTENT_NEGATIVE` |
| both `|value| <= threshold` | `NEUTRAL` |
| anything else (opposite signs, or one small/one large) | `MIXED` |
| either window unavailable | `None` - persistence is a statement about both windows agreeing, and cannot be made from one alone |

Packet spec Case D (5D +3.5pp, 20D -5.0pp) is exactly the "anything else" row: `MIXED`, never
`PERSISTENT_POSITIVE` just because the most recent window looks strong
(`test_persistence_mixed_on_recent_reversal`).

### Relative acceleration: implemented as a plain difference, not a new formula

Packet spec section 7 explicitly permits deferring this if no clean, intuitively explainable
calculation is obvious - transparency over sophistication. One was obvious enough to ship:

```
relative_shift_pp = market_relative_5d_pp - market_relative_20d_pp
```

No normalization, no weighting - both sides are already percentage-point figures over their own
windows, so the plain difference already answers "has short-term relative behaviour materially
changed versus the longer window" (packet spec section 7's own conceptual example uses the same
subtraction). `None` whenever either side is `None`, matching persistence's same-two-windows
requirement. Preserved even when persistence is `MIXED` (Case D) so a later packet can still
reason about the shift directly (`test_relative_shift_preserved_on_reversal_when_persistence_not_positive`).

### Evidence, never an opaque score

Every `RelativePerformance` carries the full chain a caller needs to explain a reading without
recomputing anything: `stock_return_*` and `benchmark`/`sector` `return_*` side by side with the
`*_relative_*_pp` differential they produced, `supporting_session_dates` (every calendar date
actually used across all three windows), and `benchmark_source`/`sector_source` naming where
each series came from. No composite score - exactly like Packets 1 and 2, this is discrete,
independently-true evidence per window, never a "relative strength score".

### Universe independence

`radar/relative.py` imports nothing from `radar.volume` or `radar.technical`, and its public
API takes no `VolumeAnomaly`/`TechnicalStructure` as input - a symbol's relative-performance
reading is computed identically whether or not it was also flagged by either other detector.
Packet spec Case A (a quiet stock, not a top mover, with genuine relative strength) is preserved
by construction: `RelativePerformanceSnapshot.results` holds every SCANNED instrument, not just
ones that cleared a threshold - unlike `VolumeRadarSnapshot.anomalies`/`TechnicalRadarSnapshot
.flagged`, this detector's job is to describe context for every stock, not just outliers.
`tests/test_relative_radar.py::test_module_never_imports_volume_or_technical_detectors` and
`test_case_a_quiet_non_mover_relative_strength_preserved` assert both halves of this directly.

### Data quality

| Situation | Effect |
| --- | --- |
| fewer than `P+1` stock sessions for window `P` | that window's `stock_return_P`/`market_relative_P_pp`/`sector_relative_P_pp` are `None`; a `data_quality` note names the shortfall; other windows unaffected |
| stock's own series does not end at the requested `session_date` | the whole symbol is `skipped` ("suspended, stale, or incomplete current data"), never partially computed |
| benchmark (or sector) missing a session at one of the stock's own two dates | that window's `market_relative_P_pp` (or `sector_relative_P_pp`) is `None`; the stock's own `stock_return_P` is unaffected |
| benchmark series empty/unavailable entirely | every symbol's `market_relative_*_pp` is `None`; a snapshot-level warning is recorded; `stock_return_*` still computes |
| no sector mapping for a symbol | `sector`/`sector_return_*`/`sector_relative_*_pp` all `None`, with a `data_quality` note; market-relative unaffected |
| sector mapped but no benchmark series supplied for that sector | same as above, different note naming the missing sector |
| duplicate session dates inside one stock's series | a `data_quality` note is added; returns are still computed positionally (oldest-first), since the calculation never depends on the dates being unique, only on their order |
| a close is `None`, zero, or negative | that window's return is `None`, never computed from an invalid price |

### No composite, no ranking

`RelativePerformanceSnapshot.results` is sorted by instrument, never by the size of the
relative-performance figure - nothing here reads as a ranked list. Packet 3 does not combine
`volume_anomaly`, `technical_anomaly` and `relative_strength` into a single score or opportunity
ranking; `StockRadarCandidate.relative_strength` is wired (this packet populates it) but nothing
composes the three fields together yet.

## Packet 4: Evidence Composition

```
VolumeRadarSnapshot ------\
TechnicalRadarSnapshot -----> radar.composite.build_candidates -> RadarCompositeSnapshot
RelativePerformanceSnapshot /
```

Same "detection infrastructure, not a publication gate" boundary as Packets 1/1.1/2/3: nothing
in `main.py`, `video.py`, `editorial/`, or the three publication gates changes, and nothing here
is called from `main.py`'s orchestration. `radar/composite.py` acquires nothing and mutates
nothing - its only inputs are the three detectors' own already-built snapshots.

### What it answers

Not "which stock gained the most" but:

> Across the universe, which stocks have MULTIPLE INDEPENDENT pieces of unusual evidence -
> evidence a top-gainers page would not surface on its own?

`radar.models.StockRadarCandidate`, reserved in Packet 1, is implemented here: `volume_anomaly`,
`technical_anomaly` and `relative_strength` are the three detectors' own dataclass instances,
reused by reference (never copied, never recomputed); `fundamental_event` stays reserved for a
later packet.

### Independent evidence families

`radar.models.EvidenceFamily` has exactly three members - `VOLUME`, `STRUCTURE`,
`RELATIVE_PERFORMANCE` - one per detector. Multiple events inside one family never count as
multiple families: a range break plus an SMA cross is still `STRUCTURE = 1`; a 5D plus a 20D
relative reading is still `RELATIVE_PERFORMANCE = 1`. `independent_signal_count` is the count of
ACTIVE families, capped at 3 - indicator stacking inside one detector can never inflate it.

### Meaningful vs contextual evidence

Only evidence that clears each family's own bar activates that family and enters
`evidence`/`reason_codes`. Evidence that does not clear the bar is simply absent from the
candidate - never fabricated as a weaker "context" line, to keep every emitted sentence
unambiguous:

| Family | Activates the family | Stays contextual only (never activates) |
| --- | --- | --- |
| `VOLUME` | `AnomalyLevel.UNUSUAL`, `AnomalyLevel.EXTREME` | `AnomalyLevel.ELEVATED` |
| `STRUCTURE` | any `BREAK_ABOVE/BELOW_20D/50D_RANGE`, any `CROSS_ABOVE/BELOW_SMA20/50` | `RANGE_COMPRESSION` alone - it describes a STATE, not a directional structural change |
| `RELATIVE_PERFORMANCE` | `PERSISTENT_POSITIVE`, `PERSISTENT_NEGATIVE` | `MIXED`, `NEUTRAL`, `None` |

This is `radar.composite._MEANINGFUL_VOLUME_LEVELS` / `_MEANINGFUL_STRUCTURE_EVENTS` /
`_MEANINGFUL_RELATIVE_STATES`, each a frozen constant, not a per-call parameter - only the
family-count and attention-level cutoffs are exposed as configurable thresholds (below).

### Eligibility

`radar.thresholds.CompositeThresholds.min_independent_families` (default 2) is the only gate: a
symbol becomes a candidate only when at least that many families are active. A symbol with a
single active family is never constructed as a `StockRadarCandidate` and then filtered out - the
eligibility check happens before construction (`radar.composite._build_candidate` returns `None`
outright). That symbol's one-family evidence still exists untouched in its own detector's
snapshot (`VolumeRadarSnapshot.anomalies` / `TechnicalRadarSnapshot.flagged` /
`RelativePerformanceSnapshot.results`) - Packet 4 only decides what enters the COMPOSITE set,
never deletes or hides anything upstream.

### Direction compatibility

`radar.models.DirectionCompatibility` (`ALIGNED_POSITIVE` / `ALIGNED_NEGATIVE` / `MIXED` /
`NON_DIRECTIONAL`) is an internal evidence descriptor, never a prediction - `radar.composite`
never emits `BULLISH`/`BEARISH`. `VOLUME` never contributes a direction (relative volume says
nothing about which way price moved, by construction - see Packet 1); only `STRUCTURE` and
`RELATIVE_PERFORMANCE` evidence can. A family's own internal signals must first agree with
themselves (e.g. a break above one range together with a cross below an SMA in the same session
is an internally conflicting `STRUCTURE` reading) before contributing a clean `+1`/`-1` to the
overall comparison; an internal conflict contributes a "0" that forces the whole candidate to
`MIXED`. Two active directional families that agree produce `ALIGNED_POSITIVE`/
`ALIGNED_NEGATIVE`; disagreement (including a MIXED-persistence relative reading that was
already excluded from the family count, so it contributes nothing rather than a manufactured
disagreement) produces `MIXED`. No directional family active at all (reachable only if a caller
lowers `min_independent_families` below 2, since `VOLUME` alone can never be directional) is
`NON_DIRECTIONAL`.

### Attention level

`radar.models.AttentionLevel` has exactly two values - `NOTABLE` (2 active families) and
`HIGH_INTEREST` (3) - read from `CompositeThresholds.notable_min_families` /
`.high_interest_min_families`, both configurable. Deliberately coarse: no 0-100 score, no
weighting by which families are active, no boost for EXTREME volume or a 50D break specifically
- the spec's optional "strengthen on EXTREME/50D" refinement was left out to keep the mapping
from `independent_signal_count` to `AttentionLevel` a one-line, fully transparent lookup rather
than a weighted formula a reader would have to reverse-engineer.

### Deterministic reason generation

`evidence` (human-readable sentences) and `reason_codes` (machine-readable, e.g.
`VOLUME_EXTREME`, `STRUCTURE_BREAK_ABOVE_20D_RANGE`, `RELATIVE_PERSISTENT_NEGATIVE`) are built
entirely from the numbers already sitting on each detector's own dataclass - no LLM, no
template the model fills in. Every sentence is built once per meaningful event/reading, in a
fixed phrasing, e.g.:

```
Volume is 4.3x its prior-20-session average.
Price closed 1.8% above its prior 20-session high.
Persistent outperformance versus Nifty: +3.7 pp over 5 sessions, +7.9 pp over 20 sessions.
```

The relative-performance line always reports BOTH the 5D and 20D differential together (never
one alone) because `PERSISTENT_POSITIVE`/`PERSISTENT_NEGATIVE` is itself a statement about both
windows agreeing - reporting only one would misrepresent what was actually detected. The same
phrasing pattern is used for `PERSISTENT_NEGATIVE` (`Persistent underperformance versus
Nifty: ...`), matching the packet spec's requirement that downside detection read exactly as
symmetric evidence, never softened or reworded. No sentence anywhere in `radar/composite.py`
contains BUY/SELL/bullish/bearish/target/conviction language -
`test_downside_anomaly_is_discoverable` in `tests/test_composite_radar.py` asserts this directly
against the generated text, not just the enum values.

### Session joining semantics

`build_candidates(volume_snapshot, technical_snapshot, relative_snapshot, session_date, ...)`
takes `session_date` explicitly rather than inferring it from one of the snapshots. Each
snapshot is joined ONLY if its own `session_date` equals the requested one - a mismatched
snapshot is dropped in its entirety (with a warning), never partially trusted, and never lets a
prior session's reading silently pair with today's from a different detector. Within a matched
snapshot, only entries whose own per-instrument date (`VolumeAnomaly.market_date` /
`TechnicalStructure.market_date` / `RelativePerformance.session_date`) equals `session_date` are
indexed - defensive, since every producing detector already guarantees this, but kept explicit
rather than assumed. Any of the three snapshots may be `None` (a detector that was never run, or
whose scan failed upstream): that detector's evidence is simply absent for every symbol, never
fabricated as a neutral/zero reading, and the remaining detectors can still produce a candidate
once at least `min_independent_families` of them agree.

### Candidate universe and counts

`RadarCompositeSnapshot.universe_size` is the union of every symbol any SUPPLIED, session-matched
detector actually `scanned` (not just flagged) - the same "considered, not necessarily flagged"
semantics `scanned` already carries in each individual snapshot. `symbols_with_volume` /
`symbols_with_technical` / `symbols_with_relative` count symbols for which THAT detector produced
an actual reading (an anomaly / a flagged structure / a result) for this session, independent of
whether it turned out meaningful - so a caller can tell "detector ran but found nothing
meaningful for this symbol" apart from "detector didn't cover this symbol at all". `
non_candidates_count` is `universe_size - candidate_count`.

### No ranking

`RadarCompositeSnapshot.candidates` is sorted by instrument, exactly like every other Packet's
snapshot list - never by `independent_signal_count`, attention level, or the size of any move.
Editorial selection (which candidates, if any, a future consumer chooses to surface, and in what
order) is explicitly out of scope for Packet 4, same as it was for Packets 1-3.

### Traceability

`StockRadarCandidate.supporting_session_dates` unions every date the three underlying detector
readings themselves cite (`VolumeAnomaly.market_date`,
`TechnicalStructure.supporting_session_dates`, `RelativePerformance.supporting_session_dates`) -
no new dates are invented at the composition layer. `calculation_version` on both
`StockRadarCandidate` and `RadarCompositeSnapshot` is the shared `radar.models.CALCULATION_VERSION`
constant (bumped if composition semantics themselves change meaning), independent of each
detector's own `calculation_version`, which stays attached to the nested `volume_anomaly` /
`technical_anomaly` / `relative_strength` objects.

### What Packet 4 is not

- Not a score. `independent_signal_count` and `AttentionLevel` are coarse, transparent counts -
  never a 0-100 number or an 8/10.
- Not a recommendation. No `evidence` sentence, `reason_code`, `DirectionCompatibility` member or
  `AttentionLevel` member contains BUY/SELL/bullish/bearish/strong/best/top-pick/conviction
  language.
- Not outcome testing. Packet 4 never looks at what happened AFTER a session - no forward
  return, no win rate, no backtesting. That is an explicitly separate, not-yet-designed future
  packet.
- Not integrated with video, editorial, or any publication gate - purely a detection-layer
  artifact, exactly like Packets 1-3.

## Packet 5+ roadmap (design only, not implemented)

Candidate follow-ups, none built: persisting `RadarCompositeSnapshot` via a
`radar.engine.save_composite_snapshot` (mirroring `save_snapshot`/`save_technical_snapshot`);
fundamentals/filings as a fourth evidence family once `StockRadarCandidate.fundamental_event` is
implemented; and, as a clearly separate research track, historical-outcome analysis (what
happened after a candidate was flagged) - deliberately NOT started in Packet 4 per its own scope
boundary.

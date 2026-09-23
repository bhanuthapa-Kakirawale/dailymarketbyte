# Local OHLCV Store (Phase 4.2 Packet 5.2)

```
Raw OHLCV
    |
market_ohlcv.db
    |
future derived Radar calculations (RVOL, Technical Structure, Relative Performance)
```

`market_ohlcv.db` is a local cache of raw, per-provider Yahoo bars, built as a write-through
side effect of the universe acquisition the Market Intelligence Radar already performs
(`market.get_universe_technical_series`). It exists so a future packet (5.3) can serve Radar
detectors from stored history instead of re-fetching Yahoo every run - but Packet 5.2 does not
do that read yet. This packet is write-only: existing acquisition behaviour, retry policy, and
every value it returns are unchanged; bars are simply also persisted.

## `market_ohlcv.db` != `market_history.db`

These are two separate SQLite files with different jobs, and must stay separate:

| | `market_history.db` (`storage/repository.py`) | `market_ohlcv.db` (`storage/ohlcv_repository.py`) |
|---|---|---|
| What it holds | Canonical, validated `MarketReport`s - facts that passed cross-source validation, tied to a `report_id` | Raw provider readings - one row per (symbol, session, provider), never validated or cross-checked |
| Mutability | Immutable once persisted (see CLAUDE.md: "canonical history is immutable") | A row is corrected in place as a provider backfills it (e.g. `BACKFILL_PENDING` -> `OK`) |
| Who reads it | `intelligence/`, historical comparisons, the video's historical-context scenes | Nobody yet (Packet 5.3+) - not in the render path, not read by any Radar calculation today |
| What a bad row means | A publication defect - the pipeline's own gates exist to prevent this | An ordinary, expected provider quirk (Yahoo backfill lag, a data gap) - recorded, not hidden |

Putting raw OHLCV into `market_history.db` would let an ordinary provider hiccup (a
`BACKFILL_PENDING` row later corrected) look like a *mutation of canonical history*, which
CLAUDE.md forbids outright. Keeping them apart means a `market_ohlcv.db` problem - corruption,
a bad migration, a full disk - can never touch a single published number, and `market_ohlcv.db`
can be deleted and rebuilt from scratch at any time with zero effect on `market_history.db` or
any past video.

## Schema

One table, `daily_ohlcv`, versioned the same way as `market_history.db` (`PRAGMA user_version`,
`storage/ohlcv_migrations.py`):

```
daily_ohlcv
-----------
symbol          TEXT
session_date    TEXT (ISO date)
source          TEXT              -- e.g. "yahoo"
open, high, low, close, volume   REAL, nullable
retrieved_at    TEXT (ISO datetime)
quality_status  TEXT              -- OK | BACKFILL_PENDING | DATE_GAP | NO_DATA
PRIMARY KEY (symbol, session_date, source)
```

Indexes: `(symbol, session_date)` and `(session_date)`.

`quality_status` is never inferred by a reader from null fields - it is stamped at write time
by whoever built the `OHLCVBar`, so "this row is incomplete" is always an explicit fact, not
something a caller has to reverse-engineer from which columns happen to be null.

## Model

`storage/ohlcv_models.py` defines `OHLCVBar` - a frozen dataclass carrying exactly the row
above, plus `QualityStatus`. This is deliberately **not** a `core.Fact`, `core.Observation`, or
`MarketReport`: those model a validated, cross-source-checked publication claim; an `OHLCVBar`
is one provider's raw, unvalidated reading. Forcing raw data into the canonical models would
either fabricate validation that never happened or corrupt the canonical models' own meaning -
so `radar/` and `storage/` keep this as its own small, honest type.

## Repository: `OHLCVStore`

`storage/ohlcv_repository.py::OHLCVStore` - `upsert_bars`, `get_range`, `latest_session`,
`count_rows`, `close`. All SQL for this database lives in this one module, matching
`storage/repository.py`'s convention for `market_history.db`.

`OHLCVStore` only stores and queries. It has no opinion on which provider to call, when to
refresh, or how to reconcile providers that disagree - see "Provider priority" below.

### Upsert semantics

`upsert_bars` is an idempotent UPSERT keyed on `(symbol, session_date, source)`:

- The same bar submitted twice is a no-op on the stored row (values are simply rewritten to
  the same values).
- A later, more complete reading for the same key **replaces** that provider's row in place -
  this is how a `BACKFILL_PENDING` row (Close not yet published by Yahoo) becomes `OK` once
  the next run's fetch has it.
- A different `source` for the same `(symbol, session_date)` is a **different row** and is
  never touched by an upsert for another source - Yahoo and NSE readings of the same session
  coexist so a future packet can compare them. Packet 5.2 does not perform that comparison.

## Write-through integration point

`market.get_universe_technical_series` is the seam. It already issues the one bulk
`yf.download()` call the Radar's technical-structure detector needs (~1y of daily bars per
symbol, `market._bulk_download_universe_ohlcv`). Right before it returns, it calls the new
`market._write_through_ohlcv(raw, syms, recap_date)`, which builds `OHLCVBar`s from the
**already-downloaded** `raw` frame - a NaN-Close row becomes `BACKFILL_PENDING`, a row with a
valid Close/High/Low becomes `OK`, and a symbol entirely absent from the fetch becomes an
explicit `NO_DATA` row for `recap_date` - and writes them with one `OHLCVStore.upsert_bars`
call. No second network request is made anywhere in this path.

`series`/`skip_reasons` - the values `get_universe_technical_series` returns - are computed
before this call and are never touched by it; a store failure only ever produces a `print()`
warning.

## Failure isolation

`_write_through_ohlcv` never raises: opening the store, running the upsert, and importing the
storage modules are each wrapped so any failure (a locked file, a bad path, a migration
refusing a too-new schema) is logged and swallowed. The call site in
`get_universe_technical_series` wraps the call in a second `try/except` as defense in depth, so
even a defect introduced later inside `_write_through_ohlcv` cannot propagate into acquisition.
Market data fetch success and OHLCV cache write success are and must remain independent
outcomes.

## Provider priority is not decided here

This store persists whatever `source` the caller labels a bar with ("yahoo" today). It has no
policy for choosing between Yahoo, NSE, Zerodha, or Dhan, no fallback ordering, and no
comparison logic across sources - that is explicitly out of scope for this packet and remains a
configuration/policy decision for a later packet, not something `OHLCVStore` or the schema
should encode. The schema's `(symbol, session_date, source)` key exists precisely so that
decision can be made later without a schema change: every provider's reading is already sitting
side by side, waiting to be compared.

## What Packet 5.2 does *not* do

- Nothing reads from `OHLCVStore` to influence RVOL, technical, or relative-performance
  calculations - those still run exactly as before, entirely from the in-memory series
  acquisition already produced. That reuse is Packet 5.3, below.
- No caching/freshness policy, no provider fallback, no NSE/Zerodha/Dhan implementation.
- No change to Yahoo retry timing, backfill-pending decisions, or detector thresholds.

## Packet 5.3: the shared read-through acquisition boundary

Before this packet, RVOL (`market.get_universe_relative_volume`, `period="3mo"`) and technical
structure (`market.get_universe_technical_series`, `period="1y"`) each triggered their own
independent Yahoo bulk download of the same universe, every run - the store only ever absorbed
technical's leftovers. Packet 5.3 replaces that with one shared boundary,
`radar.ohlcv_service.load_universe`, that every detector family now sits behind:

```
Requested universe/session
         |
         v
radar.ohlcv_service.load_universe
         |
check market_ohlcv.db -> complete enough for every symbol? --yes--> shared dataset,
         |no                                                         0 Yahoo calls
         v
fetch ONLY the missing tail/symbols, via the EXISTING market.get_universe_technical_series
seam (unchanged - still validates and writes through on its own) -> re-read the merged
(old + new) history from the store
         |
         v
UniverseOHLCVDataset
         |
    +----+-----------------+
    |                       |
radar.volume            radar.technical / radar.relative
(via relative_volume_    (series_by_symbol consumed directly -
 rows_from_dataset)       no change to either module's own API)
```

```
Cold start
    |
Yahoo bulk history (period="6mo", ~125 trading sessions - comfortably above the 55-session
    |                requirement below, well short of the old unconditional "1y")
market_ohlcv.db (write-through, unchanged seam)

Warm daily run
    |
Store history through session_date already sufficient for every symbol
    +
missing tail only, if any (period="1mo", for symbols with usable history that's just stale)
    |
Shared dataset (radar.ohlcv_service.UniverseOHLCVDataset)
    |
all Radar detectors (RVOL, technical structure, relative performance)
```

### Exact lookback requirement (not guessed)

`radar.ohlcv_service.REQUIRED_LOOKBACK_SESSIONS = 55`, derived from the three detector
families' own thresholds (`radar/thresholds.py`, `radar/relative.py`), not assumed:

| Requirement | Sessions needed |
| --- | --- |
| RVOL (`market.RELATIVE_VOLUME_LOOKBACK`) | 20 prior + current = 21 |
| Technical 50-session range / SMA50 cross (`TechnicalThresholds.range_window_long`/`sma_long`) | 50 prior + current = 51 |
| Technical `RANGE_COMPRESSION` (`TechnicalThresholds.compression_window=5`, `.compression_lookback_windows=10`) | recent 5-session window + 10 prior non-overlapping 5-session windows = 5 x 11 = **55** |
| Relative performance (`radar.relative.WINDOWS = (1, 5, 20)`) | 20 prior + current = 21 |

`RANGE_COMPRESSION` is the binding constraint. A symbol with fewer than 55 sessions is not
dropped outright - every detector already degrades gracefully on a shorter series (down to
`market.MIN_TECHNICAL_SESSIONS = 21`, the absolute floor even the 20-session range detector
needs); 55 only decides when the shared cache is treated as complete enough to skip a Yahoo call
for that symbol.

### Warm cache

If every requested symbol already has `QualityStatus.OK` rows in the store reaching exactly
`session_date`, with at least `REQUIRED_LOOKBACK_SESSIONS` of them: zero Yahoo calls. The Radar
is runnable entirely from local data (`tests/test_ohlcv_service.py::test_warm_cache_zero_yahoo_calls`).

### Cold cache

A symbol with no usable rows at all (or too few to ever reach the requirement) is fetched via
`market.get_universe_technical_series(subset, session_date, prev_date, period="6mo")` - one bulk
call for every such symbol together, not per symbol. That call already validates and writes
through on its own (the Packet 5.2 seam is unchanged); the next identical request for the same
symbols becomes a warm-cache request with zero further Yahoo calls
(`test_cold_cache_fetches_then_second_call_is_warm`).

### Partial cache

A symbol whose latest stored `OK` session is before `session_date` (usable history exists, just
stale) is fetched with a short trailing window instead - `period="1mo"` (~21 trading sessions),
enough to bridge an ordinary gap without re-downloading the whole 6-month history. After that
fetch (which writes through), the symbol's final series is read back from the store, so it is
the FULL merged history (old rows + the newly written tail), not just the short window the fetch
itself covered. A universe of 470 already-complete symbols and 30 incomplete ones results in
exactly one Yahoo call, containing only those 30 symbols
(`test_partial_cache_fetches_only_incomplete_symbols`).

### Quality rules

Only `QualityStatus.OK` rows count toward "complete" - a `BACKFILL_PENDING` row for
`session_date` is treated exactly like a missing one, and triggers a fetch
(`test_backfill_pending_row_does_not_satisfy_cache`). Every symbol's final series (whether
served from the store or freshly fetched) is validated with the SAME alignment checks
`market.get_universe_technical_series` already enforces - the current session must be present,
the session immediately before it must match the caller-supplied `prev_date`, and at least
`market.MIN_TECHNICAL_SESSIONS` valid sessions must exist - so a warm-cache symbol is never less
trustworthy than a freshly-fetched one.

### Failure isolation

A store that cannot be opened, or whose read raises, never stops the Radar: `load_universe`
falls back to a single direct Yahoo bulk fetch via the existing seam (which still attempts its
own best-effort write-through), and records a warning rather than raising
(`test_store_unavailable_falls_back_to_yahoo`, `test_store_read_failure_falls_back_to_yahoo`).

### Shared acquisition, not per-detector duplication

`radar.technical.scan_technical_universe` and `radar.relative.scan_relative_performance_universe`
already took a plain `series_by_symbol`/`skip_reasons` pair as input, so
`UniverseOHLCVDataset.series_by_symbol`/`.skipped_symbols` plug in directly with **no change to
either module's own API or math**. RVOL is different: `radar.volume.scan_universe` needs a
canonical `STOCK_RELATIVE_VOLUME` Fact (Packet 1.1's percentile history depends on that
accumulating in `MarketHistory`), so `radar.acquisition.build_universe_relative_volume_facts`
gained an optional `dataset` parameter - when supplied, RVOL rows come from
`ohlcv_service.relative_volume_rows_from_dataset`, which calls the exact same
`market.relative_volume()` formula, just fed from the shared dataset's own volume series instead
of a second independent Yahoo fetch. Omitting `dataset` keeps the pre-5.3 direct-fetch behaviour
as a compatibility fallback. `radar/run.py` now loads the dataset once and passes it to all
three families - `tests/test_ohlcv_service.py::
test_detectors_share_one_acquisition_no_duplicate_yahoo_calls` asserts directly that scanning
all three families after one `load_universe` call issues no further Yahoo calls.

### Why `market.get_universe_technical_series`'s row shape grew `open`/`volume`

RVOL needs a volume series; the technical-structure detector never reads `open`/`volume` at
all. Rather than a second acquisition function, the existing per-row dict (`{"date", "high",
"low", "close"}`) gained two additive, backward-compatible fields (`"open"`, `"volume"` - `None`
when NaN) so one fetch can serve both needs. No existing caller's behaviour changes: nothing
reads a fixed key set, and every existing test that checks specific keys still passes unchanged.

### Existing write-through, not moved

Packet 5.2's write-through stays exactly where it is (`market._write_through_ohlcv`, invoked
from inside `market.get_universe_technical_series`) rather than being relocated into
`radar/ohlcv_service.py`. `load_universe` calls that existing, already-tested seam for every
fetch it needs (cold or tail) instead of re-implementing fetch/validate/persist - one component
(`market.get_universe_technical_series`) still owns "fetch, validate, persist raw bars" end to
end; `radar/ohlcv_service.py` owns only "decide whether a fetch is needed at all, and for which
symbols." This was a deliberate choice to avoid the risk of moving already-tested logic for a
packet whose actual goal is read-through, not a relocation of writing.

### Benchmark data (NIFTY) stays separate

`radar.relative_acquisition.build_market_benchmark_series` (`^NSEI`) is not routed through the
store in this packet - merging it cleanly would mean giving `^NSEI` a place in a universe-shaped
service built around per-stock symbols, which is out of proportion to what this packet needs. It
still issues exactly one call per scan, same as before Packet 5.3 - no second full-universe
fetch was reintroduced. Storing the benchmark through the same `OHLCVStore` is a reasonable
follow-up, deliberately deferred rather than folded in here.

### What Packet 5.3 does *not* do

- No NSE/Zerodha/Dhan provider, no multi-provider reconciliation - `source="yahoo"` is still the
  only value ever written or read (Packet 5.2's "provider priority is not decided here" stands).
- No change to any detector's thresholds, math, or composite semantics.
- No change to video/editorial/publication gates, and nothing here is called from `main.py`.
- The NIFTY benchmark fetch (relative-performance detector) is not yet routed through the store.

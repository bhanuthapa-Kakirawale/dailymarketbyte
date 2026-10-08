# Market Structure - UNDER THE SURFACE

"What changed inside the market that the headline index may not show?" - counted over a named
index universe, never by naming a stock.

```
Radar detectors (volume / technical / session-aligned OHLC)        PRIVATE - named stocks
        |    (radar.daily_pipeline._run_detectors - the SAME calculation, no second detector)
        |
        +--  radar.ohlcv_service.load_universe(required_lookback=253, fetch_on_gap=False)
        |    a SECOND, read-only, independent OHLCV read for the 52-week window alone (V2) -
        |    never changes the 55-session detector dataset above
        v
build_observations(universe_def, ...)   one StructureObservation per constituent,
        |                               per-metric coverage flags
        v
aggregate(...) -> MarketStructureSnapshot    counts over ONE universe, reconciled
        |          output/market_structure/market_structure_<session>.json (internal)
        v
select_insights(snapshot, nifty_pct, allowed_kinds=None) -> 0-2 UNDER THE SURFACE scenes
                                                              PUBLIC - counts only
```

## Universe (locked)

`market_structure/universe.py`. A `UniverseDefinition` is one NSE index's constituent file as
NSE Indices published it (`ind_nifty200list.csv`: symbol, company, **Industry**), with its URL
and fetch time - recorded by `market.get_universe` from its own download (`market.UNIVERSE_META`,
no second request). `require_official()` refuses the built-in fallback list and a file whose
size is not the index's nominal size.

- Every statistic names its universe in the calculation, the scene ("NIFTY 200 STOCKS WITH
  UNUSUAL VOLUME", "18 / 200"), the audit and the artifact.
- An observation from another universe raises (`never mixed`); NIFTY 100 vs NIFTY 200 is only
  ever an explicit subset contrast ("NIFTY 100 · 6 / 100 - REST OF NIFTY 200 · 12 / 100"), and
  only when the subset is exactly contained in the universe.
- Preferred public universe: **NIFTY 200** (the Radar already scans it: 200/200 acquired).
- **Known limitation (unsolved by V2, by design)**: `market.get_universe` only ever holds
  TODAY's downloaded constituent file in memory (`market.UNIVERSE_META`) - there is no stored,
  point-in-time history of past constituent lists. A backfilled or replayed session therefore
  always aggregates over the CURRENT index membership, not whatever it actually was on that
  historical date (no survivorship-bias correction). This was already true before V2; a 252-
  session 52-week lookback makes it more consequential (a full year is more likely to span an
  index rebalance) without changing the underlying gap. Reconstructing point-in-time index
  membership is explicitly out of scope for this packet.

## Coverage policy (per metric)

| Coverage | Status | Shown as |
|---|---|---|
| 100% | PUBLISHABLE | `18 / 200` |
| 95% - <100% | PARTIAL | `18 of 196 covered (200 in index)` |
| < 95% | SUPPRESSED | not shown |

180 observed stocks are never treated as 200.

## Sector mapping

`market_structure/sectors.py` - a fixed table from NSE Indices' own `Industry` value to a short
display label (Financial Services -> Financials, Fast Moving Consumer Goods -> FMCG, ...). It
never narrows NSE's category (NSE's "Healthcare" is not called "Pharma"). An unknown industry is
`Unclassified` - explicit, never guessed; Gemini never assigns a sector. Sector rows always sum
to the numerator (checked; `ReconciliationError` otherwise).

## Metrics

| Metric | Definition |
|---|---|
| UNUSUAL_VOLUME | session volume >= 2x the stock's own prior-20-session average (the Radar volume detector's own threshold, RVOL v2.0) |
| RANGE_UP / RANGE_DOWN | closed above the prior 20 (or 50) session high / below the low (technical detector events) |
| ADVANCES / DECLINES | closed above / below the canonical previous session's close - only when the series' last two rows ARE the session and the previous session |
| NEW_52W_HIGH / NEW_52W_LOW | closed above the highest CLOSE / below the lowest CLOSE of the prior 252 sessions (`market_structure/fifty_two_week.py`) |

**52-week convention (V2), deliberately different from RANGE_UP/DOWN**: RANGE_UP/DOWN compare
the session's CLOSE against the prior 20/50 sessions' INTRADAY high/low (the Radar's own
technical-detector convention, `radar/technical.py`). NEW_52W_HIGH/LOW instead compare CLOSE
against the prior 252 sessions' CLOSE-only high/low - the conventional "52-week high/low" as
financial media report it. This is a deliberate, documented divergence for the new metric, not a
silent change to the existing 20/50-day events. Both share the same PRIOR-N-EXCLUSIVE window
rule (the session being evaluated never appears in its own comparison window) and the same
strict `>`/`<` (never `>=`/`<=` - a close exactly equal to the prior extreme is not a new
high/low). A stock with fewer than 252 PRIOR sessions of close history is `fifty_two_week_
covered=False` (not covered) - never a guessed/false reading, and a duplicate-dated row inside
the window can never silently satisfy the 252-session requirement.

**Cold-start limitation**: the 52-week window needs >= 253 sessions of per-symbol OHLCV
(252 prior + the session itself). Production's own wiring
(`radar.daily_pipeline._market_structure_step`, `market_structure.build.build_for_session`)
deliberately never tops this up with an extra Yahoo fetch on every run
(`radar.ohlcv_service.load_universe(..., fetch_on_gap=False)` - see `docs/OHLCV_STORE.md`); a
symbol below 253 stored sessions is honestly `INSUFFICIENT_HISTORY`, not a guess. The local
store naturally deepens by one session per production day, so coverage improves over time on its
own. `python -m market_structure.historical_validation` does a one-time, validation-run-scoped
2-year backfill to make its own ~60-session report meaningful immediately - a convenience that
script alone uses, never something the daily pipeline does on its own.

## Editorial rules (`market_structure/editorial.py`)

At most two scenes, one idea each, in priority order:

1. **BREADTH** - Nifty moved >= 0.20% one way while most of the universe moved the other
   ("Nifty 50 rose 0.42%, but most NIFTY 200 stocks fell"), else >= 80% of the covered universe
   moved the same way ("A broad move: 179 of 200 NIFTY 200 stocks fell").
2. **FIFTY_TWO_WEEK** (V2) - at least 5 stocks closed at a new 252-session high or low (whichever
   side leads). A descriptive, never predictive, index-vs-breadth read is layered on top only
   when Nifty moved by >= 0.20% (reusing BREADTH's own threshold): `INDEX_UP_BREADTH_WEAK`
   ("Nifty 50 rose 0.50%, but 6 NIFTY 200 stocks closed at new 52-week lows"),
   `INDEX_DOWN_BREADTH_RESILIENT` ("...fell 0.50%, yet 6 ... highs"),
   `INDEX_AND_BREADTH_ALIGNED_STRONG` ("...rose 0.50%, and 6 ... highs"),
   `INDEX_AND_BREADTH_ALIGNED_WEAK` ("...fell 0.50%, and 6 ... lows"), or a plain count with no
   index framing at all when Nifty's move is too small to say anything. Ranked above
   UNUSUAL_VOLUME/RANGE because a 52-week extreme is a longer-horizon, higher-conviction outcome
   than a one-session volume spike or a 20/50-day range break. Outranked by BREADTH, which
   answers the more basic "which way did the market go" question. Never "bullish"/"bearish" -
   those words are blocked outright by `publication/language.py` regardless of context.
3. **UNUSUAL_VOLUME** - at least 5 stocks; sector rows (top 4 + Others); a concentration line
   only when one sector is STRICTLY the largest and holds >= 30% and >= 3 of them, worded with an
   exact share ("one-third") only when the fraction is exact, else "14 of the 18".
4. **RANGE** - at least 10 stocks outside their 20-day range on one side.

Nothing meaningful -> no section (the Short is shorter, never padded).

`select_insights(..., allowed_kinds=...)` lets a caller restrict which candidate kinds are even
considered, without touching this priority order or any threshold. PRE (below) is the one caller
that uses it.

## PRE availability (V2)

Before V2, PRE never carried an UNDER THE SURFACE section at all (`structure_session=None` and
`max_structure=0`, both independently). V2 gives PRE the FIFTY_TWO_WEEK kind ONLY - never
BREADTH/UNUSUAL_VOLUME/RANGE, which have never appeared in PRE and stay that way here (a
deliberate scope limit, not an oversight): `products/premarket.py` now passes the previous
completed session (`prev` - the same value Exchange/IPO Watch already use) as
`structure_session`, and `presentation/pre_public.py` restricts selection with
`allowed_kinds=frozenset({"FIFTY_TWO_WEEK"})`. The section is optional and routed through the
SAME `plan.order`/runtime-ceiling mechanism as EXCHANGE/IPO/MARKET_EVENTS
(`presentation/pre_plan.py`'s `PUBLIC_OPTIONAL` group) - never a second budget. Its provenance
bar shows the true prior session's date, so there is no "today" leakage.

## Where it is built

- Production: `radar.daily_pipeline.run_daily_radar` (REPORT job) builds and saves the snapshot
  from its own detector output (`pipeline_diagnostics.market_structure`); never fatal. The
  52-week metric's own deeper OHLCV read (`required_lookback=253`, `fetch_on_gap=False`) is a
  second, independent, read-only `radar.ohlcv_service.load_universe` call alongside the existing
  55-session detector dataset - never a change to that dataset's own depth.
- Backfill / review: `python -m market_structure.build --session 2026-09-24` (same detectors,
  no Radar state persisted; reads/writes the normal OHLCV cache).
- Historical validation (not threshold tuning): `python -m market_structure.historical_
  validation [--sessions 60]` - reports average/max 52-week high/low counts and divergence
  frequency over the local store's recent sessions, writing its own report to
  `output/market_structure_validation/` (never to `output/market_structure/`).
- Loading re-aggregates from the stored observations and refuses a file whose counts do not
  reproduce (`load_snapshot`).

Verified 2026-09-26 on the real 24 Sep 2026 session (Nifty -1.64%): 100% coverage on every
metric; unusual volume 18 / 200 (Financials 14); 30 / 200 below the 20-day range; 179 / 200
lower.

## Point-in-time revisioning (replay, `feature/point-in-time-provenance-hardening-v1`)

`market_structure.store.save_snapshot` previously overwrote `market_structure_<session>.json`
in place on every rebuild, so a historical replay reading that one file could not tell "built
before the cutoff, never touched since" apart from "rebuilt after the cutoff, content now
different" - the file's one embedded timestamp always described the latest rebuild. This is
now backed by an immutable revision chain in the same directory:

- `market_structure_<session>.json` is still the mutable "current" file every existing live
  reader (`presentation.public_intelligence`, `private_desk.services.market`) depends on,
  unchanged path, unchanged content-is-always-current semantics.
- `market_structure_<session>.rev1.json`, `.rev2.json`, ... are exclusively-created, never
  overwritten. A rebuild whose content is unchanged (`_content_checksum`, which excludes every
  volatile universe-download `retrieved_at` - it is independently embedded in FOUR places:
  `universe.retrieved_at`, each `subset_universes[i].retrieved_at`,
  `snapshot.universe_source.retrieved_at` and `snapshot.sector_mapping.retrieved_at`) is a true
  no-op: no new revision, current file not even touched. A rebuild whose content genuinely
  changed gets the next revision, and the current file is refreshed as before.
- `market_structure.store.load_revision_as_of(out_dir, session, cutoff_iso)` is the replay read:
  the highest-numbered revision whose own `universe_source.retrieved_at` is strictly before the
  cutoff, never today's possibly-since-rebuilt current file. `readiness.evidence.
  structure_artifact` uses it; `check_market_structure` keeps a second, diagnostic-only,
  cutoff-unbounded read (`structure_artifact_unbounded`) purely to word a MISSING verdict
  precisely ("never built" vs "built, just not yet as of this cutoff").
- A session with no revision chain at all (built before this change, and never rebuilt since)
  falls back to treating the single bare current file as one legacy revision, on the same
  `retrieved_at < cutoff` test - never inferred from filesystem mtime, never backdated. The
  first rebuild of such a session under the new code seeds `.rev1.json` from that file's exact
  existing bytes before writing anything new, so pre-existing history is never silently
  discarded.
- `private_desk.repository.DeskRepository._dated_files` excludes any `.rev*.json` sibling, so
  its date-keyed lookups (`market_structure_path`/`market_structure`) always resolve to the
  current file, never a revision.

## Private Desk (V2)

`private_desk/services/market.py`'s `METRICS` tuple now includes `NEW_52W_HIGH`/`NEW_52W_LOW`,
so the dashboard/quality pages (which already iterate the artifact's metrics generically) and
`sector_table()`'s per-sector breakdown pick them up with no template change. `fifty_two_week_
symbols(ms)` is a small, optional helper returning the specific symbols behind each count,
straight from the reconciled artifact's own observations - private-only, never surfaced publicly
(the public scene is counts/sectors only, exactly like every other Market Structure metric).

# Market Structure - UNDER THE SURFACE

"What changed inside the market that the headline index may not show?" - counted over a named
index universe, never by naming a stock.

```
Radar detectors (volume / technical / session-aligned OHLC)        PRIVATE - named stocks
        |    (radar.daily_pipeline._run_detectors - the SAME calculation, no second detector)
        v
build_observations(universe_def, ...)   one StructureObservation per constituent,
        |                               per-metric coverage flags
        v
aggregate(...) -> MarketStructureSnapshot    counts over ONE universe, reconciled
        |          output/market_structure/market_structure_<session>.json (internal)
        v
select_insights(snapshot, nifty_pct) -> 0-2 UNDER THE SURFACE scenes        PUBLIC - counts only
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

## Editorial rules (`market_structure/editorial.py`)

At most two scenes, one idea each, in priority order:

1. **BREADTH** - Nifty moved >= 0.20% one way while most of the universe moved the other
   ("Nifty 50 rose 0.42%, but most NIFTY 200 stocks fell"), else >= 80% of the covered universe
   moved the same way ("A broad move: 179 of 200 NIFTY 200 stocks fell").
2. **UNUSUAL_VOLUME** - at least 5 stocks; sector rows (top 4 + Others); a concentration line
   only when one sector is STRICTLY the largest and holds >= 30% and >= 3 of them, worded with an
   exact share ("one-third") only when the fraction is exact, else "14 of the 18".
3. **RANGE** - at least 10 stocks outside their 20-day range on one side.

Nothing meaningful -> no section (the Short is shorter, never padded).

## Where it is built

- Production: `radar.daily_pipeline.run_daily_radar` (REPORT job) builds and saves the snapshot
  from its own detector output (`pipeline_diagnostics.market_structure`); never fatal.
- Backfill / review: `python -m market_structure.build --session 2026-09-24` (same detectors,
  no Radar state persisted; reads/writes the normal OHLCV cache).
- Loading re-aggregates from the stored observations and refuses a file whose counts do not
  reproduce (`load_snapshot`).

Verified 2026-09-26 on the real 24 Sep 2026 session (Nifty -1.64%): 100% coverage on every
metric; unusual volume 18 / 200 (Financials 14); 30 / 200 below the 20-day range; 179 / 200
lower.

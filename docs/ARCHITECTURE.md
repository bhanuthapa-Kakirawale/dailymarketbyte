# Architecture

Daily Market Byte is a verified market-information engine. The YouTube Short is its first
output channel, not its purpose. The architecture exists so that the data foundation outlives
any particular presentation of it.

## Pipeline (Phase 2)

```
Providers            NSE, Yahoo Finance, Google News, Gemini
      |                (providers/ - the only place raw provider output exists)
      v
Observations         what one source said, once, with provenance and independence attached
      |
      v
Facts                one normalized fact, backed by one or more Observations
      |
      v
Validation           date, freshness, range, and cross-source corroboration by independence
      |
      v
MarketReport         the authoritative, dated contract - built BEFORE anything is rendered
      |
      v
Presentation Adapter presentation/ - reshapes the report into renderer structures
      |
      v
Renderer             video.py / chart.py, unchanged; acquires nothing
      |
      v
Publication QA       content safety re-scan of every finalized public string
      |
      v
Publisher            upload.py
```

## Three rules this shape encodes

**LLM is not a source of truth.** Gemini is a *discovery* source. Grounded Google Search does
not promote it to a primary one. Every Gemini-derived value is `SourceType.AI`, and validation
refuses to mark a critical numeric fact `VERIFIED` when only AI groups support it - no matter
how plausible the number looks. A plausible wrong number passes a range check; that is exactly
why passing a range check is not verification. An AI value *may* join a `VERIFIED` fact once
an acceptable non-AI source independently agrees inside tolerance.

**Source authority is not source independence.** They answer different questions. Authority
(`source_type`) is how much one source's word is worth; independence (`independence_group`) is
whether two readings are separate evidence at all. Two Yahoo readings of the same close are
one witness however they were fetched. Corroboration is counted in independence groups, never
in observations - see `docs/SOURCE_PROVENANCE.md`.

**The renderer does not acquire market data.** Everything drawn comes through the presentation
adapter from a report that has already been validated, content-checked and cleared. If a number
appears in the video, it exists in the MarketReport. The adapter may reshape and format; it may
not fetch anything or compute a new market fact.

## Where the code lives

| Layer          | Module                             | Phase 2 status                     |
| -------------- | ---------------------------------- | ---------------------------------- |
| Acquisition    | `providers/`                       | new - wraps market.py / news.py     |
| Legacy fetch   | `market.py`, `news.py`             | still underneath; now record provenance |
| Domain         | `core/`                            | + `sources.py`, independence-aware validation |
| Report build   | `adapters/`                        | builds from provider output         |
| Presentation   | `presentation/`                    | new - the renderer boundary         |
| Rendering      | `video.py`, `chart.py`, `music.py` | unchanged                           |
| Publication    | `upload.py`                        | unchanged                           |
| Orchestration  | `main.py`                          | thin: collect → report → gate → present → render → QA → publish |

## What changed in Phase 2

Phase 1 ran the canonical layer as a *tee*: the report was written after the video, from
whatever the pipeline happened to be holding, and could not affect publication. Phase 2
inverts that dependency. The report is built first, it decides whether publication may
proceed, and the renderer is fed from it.

The renderer itself was not rewritten. `presentation/report_adapter.py` produces the same
structures `video.py` and `chart.py` have always consumed, so the migration moved the
*source* of those structures without touching the code that draws them.

## Two independent gates

```
DATA VALIDATION  (report.publication_ready)
        +
CONTENT SAFETY   (core/content_safety.py, deterministic, no Gemini)
        +
PUBLICATION QA   (final scan of finalized artifacts)
        ↓
   publication allowed
```

Both must pass. Validation failing stops the run before rendering; content safety failing
stops the upload after it. Neither is advisory.

## Still deliberately absent

No database, no post-market edition, no Zerodha, no paid market-data APIs, no new indicators,
no dashboards or APIs. `ReportType.POST_MARKET` exists as an enum only. These stay deferred;
Phase 2 exists so that adding them later does not mean rebuilding the foundation.

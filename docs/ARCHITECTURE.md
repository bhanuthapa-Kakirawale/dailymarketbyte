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
      +---> JSON artifact      output/reports/  - immutable record of this run
      +---> SQLite index       output/data/     - queryable history across runs
      |
      v
Publication Gate     report.publication_ready - an unfit report renders nothing
      |
      +---> Intelligence       canonical history + today -> IntelligenceSnapshot (derived)
      |                        output/intelligence/ - deterministic, never canonical
      v
Editorial Selection  editorial/ - report + snapshot -> ShortsPlan (derived, never canonical)
      |                what is said, in what order, for how long. No LLM, no predictions.
      v
Publication Gate     publication/ - PublicationProfile (default PUBLIC_UNREGISTERED) judges
      |                every candidate fact BEFORE a storyboard exists: no named-stock Radar
      |                analysis / ranking in public; named securities only via an official
      |                event; counts only over a named universe (docs/PUBLICATION_POLICY.md)
      v
Presentation Adapter presentation/ - reshapes the report into renderer structures
      |
      v
Renderer             video.py / chart.py; scenes are built from the plan, acquires nothing
      |
      v
Publication QA       video QA (artifact) + readability QA (plan) + content safety (plan text)
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
| Acquisition    | `providers/`                       | wraps market.py / news.py           |
| Legacy fetch   | `market.py`, `news.py`             | still underneath; record provenance |
| Domain         | `core/`                            | `sources.py`, independence-aware validation, content safety |
| Report build   | `adapters/`                        | builds from provider output         |
| Persistence    | `storage/`                         | Phase 3 - SQLite historical index   |
| Intelligence   | `intelligence/`                    | Phase 4.1 - deterministic historical context |
| Editorial      | `editorial/`                       | Phase 4.1.2 - selection, hook, readability, pacing |
| Publication guards | `core/move_guard.py`, `editorial/movers_gate.py`, `presentation/radar_guard.py` | POST freeze - deterministic move guard (bad data / corporate action / extreme move) and the >= 90% universe coverage gate in front of every Movers / top-mover / Radar publication (`docs/SHORTS_EDITORIAL.md`) |
| Session calendar | `core/trading_calendar.py`         | Data-reliability patch - canonical NSE sessions (holiday list + benchmark bars); every day-over-day / window calculation drops provider rows on non-sessions first, and the benchmark must carry the canonical previous session (`docs/VALIDATION_RULES.md` section 9) |
| Benchmark gap recovery / cache repair | `market.recover_index_gaps`, `radar/cache_repair.py` | Second index source (NSE end-of-day index file) for a canonical session the Yahoo index series lacks, validated + anchored + provenance-recorded; the recap session itself only via
`market.recover_recap_session` once the session is final (section 10.1), else publication is blocked; repair of cached non-final OHLCV bars (`docs/VALIDATION_RULES.md` sections 10-11) |
| Publication boundary | `publication/`, `market_structure/`, `exchange_watch/`, `ipo_watch/`, `presentation/public_intelligence.py`, `presentation/pre_public.py`, `presentation/legacy_public.py`, `presentation/provenance_label.py`, `daily_video/provenance_bar.py`, `daily_video/public_scenes.py` | Public intelligence V1 - PRIVATE_ANALYTICS vs PUBLIC_UNREGISTERED profiles, typed fact classification, deterministic policy + rights registry + language scan, visible SOURCE / DATA AS OF, UNDER THE SURFACE (Market Structure over NIFTY 200), EXCHANGE WATCH, IPO WATCH, `publication_audit.json` and the upload hard-block (docs/PUBLICATION_POLICY.md, MARKET_STRUCTURE.md, EXCHANGE_WATCH.md, IPO_WATCH.md) |
| Hook engine    | `hooks/`                           | Hook Phase 1 - teaser + hook for PRE/POST/CUSTOM; Gemini chooses among approved candidates, strict validation, deterministic fallback (`docs/HOOK_ENGINE.md`) |
| Presentation   | `presentation/`                    | the renderer boundary               |
| PRE-MARKET     | `products/` (router + runner), `providers/premarket.py`, `core/freshness.py`, `core/event_calendar.py`, `presentation/pre_plan.py`, `daily_video/pre_storyboard.py`, `daily_video/pre_scenes.py` | PRE V1 - "before the bell" Short from the previous session's canonical report + dated/timestamped pre-open readings + verified schedules; deterministic `PreEditorialPlanner`; reuses the POST design system and the hook engine; never writes canonical history, never uploads (`docs/PRE_MARKET.md`) |
| Rendering      | `video.py`, `chart.py`, `music.py` | scenes built from the editorial plan |
| Artifact QA    | `qa/`                              | deterministic video + readability checks |
| Publication    | `upload.py`                        | unchanged                           |
| Orchestration  | `main.py`                          | thin: obtain the canonical report (reuse it, else `produce_report`: collect → report → persist) → gate → plan → present → render → QA → publish; `--mode report` / `--mode premarket` route through `products.route(VideoRequest)` (POST is the default and runs `main.run`) |
| Scheduling     | `products/report_job.py`, `operations/` | the REPORT job (canonical report + intelligence + Radar, no video, idempotent) and what the jobs share: calendar session resolution, canonical-report lookup, the GIFT publication gate, the connectivity diagnostic, the official-event reminder (`docs/PRODUCTION_SCHEDULE.md`) |

## What changed in Phase 2

Phase 1 ran the canonical layer as a *tee*: the report was written after the video, from
whatever the pipeline happened to be holding, and could not affect publication. Phase 2
inverts that dependency. The report is built first, it decides whether publication may
proceed, and the renderer is fed from it.

The renderer itself was not rewritten. `presentation/report_adapter.py` produces the same
structures `video.py` and `chart.py` have always consumed, so the migration moved the
*source* of those structures without touching the code that draws them.

## Five independent gates

```
DATA QA            report.publication_ready   - validation, corroboration, required facts
     AND
CONTENT QA         core/content_safety.py     - deterministic, no model
     AND
VIDEO QA           qa/video_qa.py             - deterministic artifact inspection
     AND
READABILITY QA     qa/readability_qa.py       - can the Short actually be read at its pace
     AND
PUBLICATION AUDIT  publication/audit.py       - profile, language scans, visible provenance,
                                                universe visibility, no GMP; upload.upload
                                                re-checks it against the file's sha256
        ↓
   publication allowed
```

All five must pass, and none is advisory. Data QA failing stops the run before rendering;
the rest stop the upload after it, preserving every artifact for diagnosis. Persistence is a
further precondition: if the run cannot be recorded in history, it does not publish, because
auditability is part of publication integrity.

See `docs/PRODUCTION_QA.md`.

## Two artifacts, and the line between them

| | Answers |
| --- | --- |
| MarketReport JSON + canonical SQLite rows | "What market information did this report contain?" |
| `publication_runs` + QA artifact | "What happened when we tried to render and publish it?" |

**Canonical market history is immutable during normal production execution.** The report is
finalized before it is persisted; the JSON is written once and the canonical rows once. Video
QA, the final content scan and the upload result are operational outcomes recorded alongside,
never rewrites of what the market did. Post-report QA does not mutate canonical market
intelligence.

SQLite is not in the rendering path either - the renderer consumes the current MarketReport
through `ReportPresentation`, so a database problem can never change what a video looks like.
See `docs/STORAGE.md` and `docs/PRODUCTION_QA.md`.

A third kind of artifact joined them in Phase 4.1: the **IntelligenceSnapshot**, which is
*derived* - a deterministic function of the canonical report and canonical history that can be
regenerated from them at any time. It is written to `output/intelligence/`, never folded back
into the report, and never inserted into `facts`. See `docs/INTELLIGENCE.md`.

Phase 4.1.2 added a fourth, derived the same way: the **ShortsPlan**. It is what the Short
says, in what order and for how long - a deterministic function of the report and the
snapshot, recorded inside the QA artifact rather than persisted on its own, and written back
into neither input. The distinction it enforces is that a valid fact does not earn screen
time: the canonical record keeps everything, and the plan chooses. See
`docs/SHORTS_EDITORIAL.md`.

## Still deliberately absent

No post-market edition, no historical analytics, no dashboards or API server, no Zerodha, no
paid market-data APIs, no new indicators, no trading signals or recommendations.
`ReportType.POST_MARKET` exists as an enum only. These stay deferred; the phases so far exist
so that adding them later does not mean rebuilding the foundation.

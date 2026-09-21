# Architecture

Daily Market Byte is a verified market-information engine. The YouTube Short is its first
output channel, not its purpose. The architecture exists so that the data foundation outlives
any particular presentation of it.

## Target pipeline

```
Providers            NSE, Yahoo Finance, Google News, Gemini
      |
      v
Observations         what one source said, once, with provenance attached
      |
      v
Facts                one normalized fact, backed by one or more Observations
      |
      v
Validation           date, freshness, range, and cross-source corroboration
      |
      v
MarketReport         the validated, dated contract for any consumer
      |
      v
Presentation         video renderer today; dashboards, email, research later
      |
      v
Video / YouTube
```

## Where the code lives

| Layer          | Module                          | Status after Phase 1            |
| -------------- | ------------------------------- | ------------------------------- |
| Providers      | `market.py`, `news.py`          | unchanged, still legacy dicts   |
| Adapters       | `adapters/`                     | new - dicts to Observations     |
| Domain         | `core/`                         | new - models, validation, report|
| Presentation   | `video.py`, `chart.py`, `music.py` | unchanged                    |
| Publication    | `upload.py`                     | unchanged                       |
| Orchestration  | `main.py`                       | one added call, wrapped         |

## Phase 1 shape: a tee, not a rewrite

The canonical layer currently runs *beside* the pipeline rather than inside it:

```
market.py / news.py  ->  legacy dicts  ->  video.py  ->  MP4   (unchanged path)
                              |
                              +--------->  adapters  ->  core  ->  report JSON   (new path)
```

The renderer consumes the same dicts it always did, so the video is equivalent by
construction rather than by inspection. The report can be wrong, empty, or fail to build
without costing a publication - `build_and_save_report` catches everything and logs.

The cost of that safety is that the report is a *record* of what was published, not a gate
on it. Turning it into a gate is Phase 2 work.

## LLM is not a source of truth

The single most important rule in this system:

> **An LLM never establishes a critical numeric market fact on its own.**

Gemini is a language component. It compresses wording, classifies, and summarizes. It also
performs grounded web search in the current pipeline, which makes it a *discovery* source -
and discovery is not verification.

Every Gemini-derived value is tagged `SourceType.AI`. Validation refuses to mark a critical
numeric fact `VERIFIED` when only AI observations support it, no matter how plausible the
number looks. A plausible wrong number passes a range check; that is exactly why passing a
range check is not verification.

An AI value *may* participate in a `VERIFIED` fact once an acceptable non-AI source
independently agrees with it inside tolerance. That is corroboration, and it is the only way
AI numbers earn full trust here.

See `docs/VALIDATION_RULES.md` for the precise policy and `docs/DATA_MODEL.md` for the types.

## What Phase 1 deliberately did not do

- No database. Reports are JSON files under `output/reports/`.
- No provider abstraction. `market.py` and `news.py` still fetch exactly as before.
- No instrument master, no source-licence registry, no catalyst time-matching.
- No post-market edition. `ReportType.POST_MARKET` exists as an enum only.
- No change to stock selection, indicators, video design, or YouTube behaviour.

These are Phase 2+ candidates. The point of Phase 1 is that none of them now require
rebuilding the data foundation.

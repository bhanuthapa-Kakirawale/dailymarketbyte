# Storage

## Two artifacts, two jobs

```
MarketReport
    │
    ├── output/reports/premarket_YYYY-MM-DD.json   immutable per-run artifact
    │
    └── output/data/market_history.db              queryable historical index
```

| | Question it answers |
| --- | --- |
| **JSON** | "What exactly did this run produce?" |
| **SQLite** | "What has happened across many runs?" |

The JSON file is the record of truth for any single run and is never rewritten after the
fact. SQLite is an index over those files: every `reports` row carries
`json_artifact_path`, so the authoritative artifact is always locatable from a query result.

**SQLite is not in the rendering path.** The renderer consumes the current `MarketReport`
through `ReportPresentation`, exactly as in Phase 2. Nothing reads market data back out of
the database to draw a frame, so a database problem can never change what a video looks like.

Database location defaults to `output/data/market_history.db` and follows `OUT_DIR`. Runtime
databases and QA files are gitignored and must never be committed.

## Schema

Eight tables, modelled on the canonical domain rather than the renderer's structures - the
renderer's shapes have already changed once, the domain is what is worth indexing for years.

```
reports ──┬── facts ──┬── observations ──> sources
          │           └── validation_results
          ├── catalysts
          └── events

publication_runs        (operational history, separate from canonical history)
```

Foreign keys are enforced (`PRAGMA foreign_keys = ON`) and children cascade on delete.

### Identity

`report_id` (e.g. `20260921_PRE_MARKET`) is the primary key for a report. Facts and
observations use **composite** keys — `(report_id, fact_id)` and
`(report_id, fact_id, observation_id)` — because a `fact_id` is canonical *within* a report
but two reports could legitimately describe the same session.

### The `sources` table and its documented assumption

`source_name` is the primary key, and rows are upserted with `first_seen_at` / `last_seen_at`.
This is the deliberate, documented assumption that **`source_name` is stable over time**.

It is safe because per-report independence is never read back from this table: every
observation stores its own `independence_group`, so a later registry change cannot
retroactively alter what an archived report was corroborated by. If a source ever needs
genuine versioning (the same name meaning different things at different times), the change
is to give `sources` a surrogate key plus a validity range and point observations at that —
not to reinterpret history already stored.

## Transactions

One report persists inside one transaction (`with self.conn:`). If any insert fails partway
through, the whole report is rolled back and the database contains no trace of it.

This matters more than it first appears: a *half-written* report is worse than a missing one,
because it looks complete to every later query. `tests/test_storage.py` forces a failure
midway through a real save and asserts nothing survives, then asserts a retry succeeds.

## Idempotency

The same report gets processed repeatedly — Actions retries, manual reruns, debugging,
recovery. Default behaviour:

- **Report already stored → no-op**, `save_report` returns `False` and nothing is touched.
- **`replace=True`** deletes that one report (cascading to its children) and re-inserts it.
  Used by the pipeline once per run, to refresh the row after the final content-safety
  verdict is attached to the JSON, so the index matches the artifact it points at.
- **`publication_runs` always appends.** A run is a statement about one execution, so two
  runs of the same report are two rows, by design.

Idempotency is never achieved by rebuilding the database.

## Demo isolation

Demo runs persist with `is_demo = 1`. Every query excludes them unless `include_demo=True` is
passed explicitly. Synthetic fixtures therefore cannot contaminate real market history, while
remaining available for debugging the pipeline itself.

This was chosen over refusing to persist demo runs at all, because the persistence path is
then exercised on every demo render rather than only in production — a gate that is only
tested in production is a gate nobody trusts.

## Schema versioning

`PRAGMA user_version`, with `SCHEMA_VERSION = 1` in `storage/schema.py`.

- Fresh database → created at the current version.
- Equal version → opened as-is.
- Older version → migrations in `MIGRATIONS` applied in order (none exist yet; the mechanism
  does, so the first real migration is a data change rather than an architecture change).
- **Newer version → `SchemaVersionError`, refused.** Writing into a schema written by a newer
  build would corrupt history quietly, which is the one outcome worth real code to prevent.

## Concurrency

`busy_timeout` (30s) so an overlapping rerun waits for the lock rather than failing
instantly, and WAL journalling so a reader never blocks the writer. That is all — no
distributed locking. The requirement is only that concurrent access waits or fails cleanly
instead of corrupting history.

## Repository API

All SQL lives in `storage/repository.py`; queries return small dataclasses
(`StoredReport`, `StoredFact`, `StoredObservation`, `StoredValidationResult`, `StoredRun`)
rather than raw tuples.

```python
save_report(report, artifact_path, replace=False, is_demo=None) -> bool
report_exists(report_id) -> bool
get_report(report_id) -> StoredReport | None
get_reports_between(start, end, report_type=None, include_demo=False) -> list
get_facts(metric=None, instrument=None, start_date=None, end_date=None,
          report_id=None, include_demo=False) -> list
get_observations(fact_id, report_id=None) -> list
get_validation_results(fact_id, report_id=None) -> list
get_catalysts(report_id) / get_events(report_id) / get_source(source_name)
start_run(mode, report_id=None) / update_run(run_id, **fields) / finish_run(...)
get_publication_runs(report_id=None, limit=50) -> list
```

Analytics are explicitly out of scope for Phase 3 — this is the storage and retrieval layer,
not a research API.

## Security

No credentials of any kind are stored: no API keys, OAuth tokens, Gemini keys or YouTube
credentials. What is stored is market data, source references and public URLs.

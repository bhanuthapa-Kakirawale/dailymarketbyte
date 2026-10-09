# Private Intelligence Refresh + 30-Day Coverage Reconciler V1

## 1. Purpose and hard boundary

`intelligence_refresh/` decouples keeping Private Desk's intelligence data fresh from running
PRE/POST video production. Before this subsystem, Market Structure, Radar candidate history,
Institutional Flow, Market Events, official snapshots and the canonical report only ever
advanced as a side effect of the evening REPORT job
(`products/report_job.py`), which only `run_morning_pre.bat`/`run_evening_full.bat` (and the
PK-D orchestrator) trigger. If neither runs for several sessions, Private Desk silently goes
stale with no independent way to notice or repair it.

`python -m intelligence_refresh {refresh|status}` is a separate, intelligence-only command.

Hard boundary (test-enforced, `tests/test_intelligence_refresh_never_video_upload.py`): this
package **never** renders video, **never** uploads, and **never** changes Radar / Market Regime
/ Editorial Planner V3 *rules*. It only invokes existing, unmodified engines:
`products.report_job.run_report_job`, `market_structure.build.build_for_session`,
`radar.candidate_history_backfill.backfill_candidate_history`.

## 2. The net architectural conclusion

Research against the real call chain established these hard constraints:

1. `products.report_job.run_report_job` already resolves the session (defaults to the latest
   final session), reuses an existing canonical report instead of rebuilding, then
   unconditionally runs Radar (which also builds Market Structure), official-snapshot
   capture, institutional-flow capture, market-events capture, and the derived intelligence
   snapshot. It must be called in-process, never reimplemented.
2. REPORT is architecturally **latest-session-only**: `resolve_session` never builds a session
   older than the latest final session. There is no way, and this project must not add one, to
   retroactively build a *new* canonical report for an old missed evening - the AI/news/GIFT
   facts that fed it were point-in-time and are gone.
3. Institutional Flow, Market Events and official snapshots are **latest-only sources**: their
   fetchers have no historical-date parameter, and the official-snapshot capture window closes
   once the next session opens. A missed capture is permanently lost for that day. The only
   lever is catching up the *current* latest session if it hasn't been captured yet.
4. The one genuinely reconstructable chain is **price-derived**: index/OHLCV history ->
   Market Structure -> Radar candidate history. Two existing, unmodified tools already do this
   safely for an arbitrary past session (`market_structure.build.build_for_session`,
   `radar.candidate_history_backfill.backfill_candidate_history`).
5. Radar editorial selections have **no existing historical-backfill tool**, by design -
   `backfill_candidate_history` deliberately never writes selections for a non-live session.
   This project does not invent one.
6. Market Regime is already **lazy/on-demand** - nothing to trigger.

So "refresh" = (a) ensure the latest final session has a successful REPORT job run (covers 6
of the 10 tracked components in one already-idempotent call), plus (b) a 30-day
price-chain-only self-heal for every other session in the window. Everything else for a
non-latest session is reported as `HISTORICAL_UNAVAILABLE` / `UNSUPPORTED_HISTORICALLY`,
never attempted, never fabricated.

## 3. Capability registry (`intelligence_refresh/registry.py`)

| key | backfill_mode | engine reused |
|---|---|---|
| `ohlcv_benchmark` | RECONSTRUCTABLE | `radar.ohlcv_service`, `radar.relative_acquisition` |
| `market_structure` | RECONSTRUCTABLE | `market_structure.build.build_for_session` |
| `radar_candidate_history` | RECONSTRUCTABLE | `radar.candidate_history_backfill.backfill_candidate_history` |
| `radar_editorial_selections` | UNSUPPORTED_HISTORICALLY | none exists |
| `institutional_flow` | LATEST_SESSION_ONLY | `products.report_job.capture_institutional` |
| `market_events` | LATEST_SESSION_ONLY | `products.report_job.capture_market_events` |
| `official_snapshots` | LATEST_SESSION_ONLY | `products.report_job.capture_official` |
| `canonical_report` | LATEST_SESSION_ONLY | `main.produce_report` via `run_report_job` |
| `intelligence_snapshot` | LATEST_SESSION_ONLY | `main.build_intelligence` |
| `market_regime` | NOT_APPLICABLE | `private_desk.regime.RegimeStore` (lazy) |

## 4. Two correctness details worth knowing

- **Today's unfinished session is never audited as "historical".** `scan.sessions_to_audit`
  bounds the window by the latest *final* session, never by `now.date()` - a session before
  `SESSION_FINAL_TIME` (15:40 IST) has no finalized data to backfill at all, and an explicit
  `--from`/`--to`/`--session-date` that reaches past the latest final session is silently
  bounded by it rather than attempted.
- **The coverage cutoff is compared in IST, not UTC.** `market_structure.store.
  load_revision_as_of` compares `universe_source.retrieved_at` *lexically* against the cutoff
  string, and every stored `retrieved_at` is an IST-offset ISO string (the same convention
  `readiness/evidence.py::cutoff_iso` already follows) - `retrieved_at` itself records when the
  universe list was *downloaded* (real build time), never anything tied to the session date,
  so a revision built today for a session a month ago still carries today's timestamp.
  Comparing a UTC-formatted cutoff against an IST-formatted timestamp can give the wrong
  lexical answer even when the absolute instants compare correctly - verified live: a
  freshly-backfilled session was reported `MISSING` again on the very next run until this was
  fixed to convert the cutoff to IST first (`coverage.py::_cutoff_iso`).

## 5. Dependency order and a known, accepted limitation

`market_structure.build.build_for_session` and `radar.candidate_history_backfill.
backfill_candidate_history` are mutually independent (neither calls the other). Within one
`refresh()` run, the benchmark/spine/universe context (`price_chain_backfill.
fetch_benchmark_context`) is fetched **once** and passed into a single **batched** call to
`backfill_candidate_history` covering every missing session at once (that function already
sorts oldest-first and skips sessions already `COMPLETE`).

`build_for_session` has no way to accept a pre-fetched benchmark - it fetches its own
internally on every call. Backfilling N historical sessions' Market Structure therefore means
N separate (redundant) benchmark fetches. This is an accepted, documented limitation of reusing
that tool completely unmodified, not something this project engineers around.

## 5. CLI reference

```
python -m intelligence_refresh refresh [--days 30] [--from D] [--to D] [--session-date D]
                                        [--resume] [--dry-run] [--no-network] [--json]
python -m intelligence_refresh status [--json]
```

- `--days N` (default 30): the N most recent calendar days up to (not including) the latest
  final session.
- `--from`/`--to`: explicit inclusive date range, overriding `--days`.
- `--session-date D`: audit exactly one historical session.
- `--resume`: records which leftover `RUNNING` manifest this run continues from
  (`resumed_from_run_id`). The underlying work is naturally idempotent (coverage is re-scanned
  fresh every run), so resuming is safe even without this flag - it exists for the audit trail
  and to satisfy an explicit interruption-recovery contract.
- `--dry-run`: every component is evaluated but never executed; results are
  `WOULD_RUN`/`WOULD_BACKFILL`.
- `--no-network`: same effect as `--dry-run` on whether engines are invoked, but the manifest
  is marked as an executed run, not a dry run.
- `--json`: machine-readable output.

Exit codes: `0` = SUCCESS/DEGRADED, `20` = BLOCKED (the current/latest session's own REPORT
job was blocked by its data-quality gate), `30` = FAILED or a held lock.

Paths: `output/intelligence_refresh/run.lock`, `output/intelligence_refresh/latest.json`,
`output/intelligence_refresh/runs/<run_id>.json`.

## 6. Coverage report schema

Run manifest, schema `dmb.intelligence_refresh.run/1`:

```json
{
  "schema": "dmb.intelligence_refresh.run/1",
  "run_id": "...", "command": "refresh", "started_at": "...", "completed_at": "...",
  "orchestrator_status": "SUCCESS|DEGRADED|BLOCKED|FAILED|LOCKED",
  "cli": {"days": 30, "from": null, "to": null, "session_date": null,
          "resume": false, "dry_run": false, "no_network": false},
  "resumed_from_run_id": null,
  "latest_session_result": {"action": "SKIPPED|RAN|WOULD_RUN", "session": "...",
                            "report_job_run_status": "SUCCESS|DEGRADED|BLOCKED|null"},
  "session_results": [
    {"session": "2026-09-15",
     "components": {"ohlcv_benchmark": "...", "market_structure": "...",
                    "radar_candidate_history": "...", "radar_editorial_selections": "...",
                    "institutional_flow": "...", "market_events": "...",
                    "official_snapshots": "...", "canonical_report": "...",
                    "intelligence_snapshot": "...", "market_regime": "..."},
     "session_status": "COMPLETE|PARTIAL"}
  ],
  "summary": {"sessions_audited": 22, "sessions_fully_recoverable": 20,
             "sessions_partially_recoverable": 2, "component_counts": {"...": 0}},
  "warnings": [], "blocking_reasons": [], "failure": null,
  "host": {"hostname": "...", "pid": null}, "git_commit": "..."
}
```

## 7. Private Desk panel

`private_desk/services/intelligence_freshness.py` reads `output/intelligence_refresh/
latest.json` only - no acquisition import, no write path - and is wired into
`private_desk/services/quality.py::data_quality()`'s `"intelligence_refresh"` key. The Data
Quality page shows a new "Intelligence refresh" panel (last run status/time, latest-session
catch-up outcome, session/component summary counts, warnings/blocking reasons).

## 8. Relationship to `production_orchestrator` / `readiness` / REPORT job

`intelligence_refresh` has its own `lock.py`/`manifest.py`/`models.py`, independently
implemented (not imported) from `production_orchestrator`'s, so the two orchestrators stay
independently runnable - a failed video run never stops data intelligence refresh, and a
skipped refresh never blocks PRE/POST (they already build/consume REPORT state on their own
paths unchanged). It calls `products.report_job.run_report_job` exactly as `run_evening.bat`/
`run_evening_full.bat` already do - never a second implementation of the REPORT job.

## 9. Operational runbook

- `scripts\run_intelligence_refresh.bat` - thin wrapper, `python -m intelligence_refresh
  refresh %*`.
- `scripts\check_intelligence_coverage.bat` - thin wrapper, read-only,
  `python -m intelligence_refresh status %*`.
- A held `run.lock` past a crashed run is reclaimed automatically once its PID is verified
  dead (same-host) or, cross-host, once it's older than 4 hours.
- Future scheduling (not installed by this packet): an early-morning and/or evening
  `intelligence_refresh refresh` call ahead of PK-D PRE/POST, each independently runnable.

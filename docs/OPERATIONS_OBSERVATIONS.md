# Operations observations (dated log)

A read-only review of real run records. Each entry says what was looked at, what was found
and what is still open. It never re-decides readiness or business outcomes; those stay in
PK-C/PK-D/PK-E and their own records. Newest entry first.

---

## 2026-10-23: shadow operations, Radar acquisition, Policy 2.0 capture (sessions 10-12..10-22)

Sources (all real, read-only): `output/shadow_scheduler/runs/*.json`,
`output/production_runs/{PRE,POST}/**`, `output/intelligence_refresh/runs/*.json`,
`python -m radar.acquisition_diagnostics --sessions 10 [--json]`,
`python -m production_quality pre-degradation --runs 8`,
`private_candidates.service.run_from_store` (read-only), `python -m shadow_scheduler status`.
Repo HEAD `fec578b`.

### Per session

2026-10-20 (Dussehra) is an NSE holiday, so its evening jobs are NOT_EXPECTED. "AM/PRE" for
session D ran on the morning of D+1's edition. The PRE column below is listed under the PRE
edition date.

| Session D | Intel PM (D eve) | POST (D) | PRE edition (D+1) | Radar acquisition | Radar cand. | Policy 2.0 snapshot |
|---|---|---|---|---|---|---|
| 10-12 | COMPLETED | DEGRADED (pre READY) | 10-13 DEGRADED | **FAILED** 0/200 (pre-V1, inferred) | COMPLETE(0) marker | n/a (pre-2.0) |
| 10-13 | CHILD_NONZERO (30) | BLOCKED (REPORT exit 1: "Only 0 stocks had clean data") | 10-14 BLOCKED (PREVIOUS_SESSION_MISSING) | no Radar artifact (SESSION_NOT_ON_CANONICAL_SPINE); history COMPLETE(15) from backfill | 15 | n/a |
| 10-14 | COMPLETED | BLOCKED (EDITORIAL_PLAN unrenderable + displayed_claims) | 10-15 PRE **EXPIRED** (invoked 16:21) | HEALTHY 200/200 (inferred) | 18 | n/a |
| 10-15 | COMPLETED | SUCCESS (20:17) | 10-16 DEGRADED | HEALTHY 200/200 (inferred) | 27 | n/a |
| 10-16 | COMPLETED | SUCCESS | 10-19 PRE **EXPIRED** (AM stuck RUNNING) | HEALTHY 200/200 (inferred) | 24 | Policy **1.0**: 24, POINT_IN_TIME |
| 10-19 | COMPLETED | DEGRADED (pre DEGRADED, post READY) | 10-20 SKIPPED (holiday) | **FAILED** 0/200 (inferred) | COMPLETE(0) marker | NOT_APPLICABLE ("no Radar candidates"): a data gap, not a quiet day |
| 10-21 | **EXPIRED** (invoked 21:16) | SUCCESS (manual, 21:17) | 10-22 DEGRADED | **HEALTHY** 200/200, native V1 record (requested 200, returned 200, bars 4,200) | 67 | **67**, POINT_IN_TIME (catch-up 10-22 06:40, proof-by-cutoff), as_of 10-21T23:59:59 |
| 10-22 | COMPLETED (started 19:31) | FAILED ×2 (pulse label overlap) → **SUCCESS** replay `20261023T071434Z_91efeefe` | 10-23 DEGRADED | **HEALTHY** 200/200 native | 49 | **49**, POINT_IN_TIME (same evening 19:40:24) |

Policy 2.0 board, from the stored snapshot (not recomputed):

| Session | Inputs | ELIGIBLE | REVIEW | NOT_ACTIONABLE | DATA_INCOMPLETE | Raw ranking (ALL / UPSIDE) | Shortlist (ALL / UPSIDE) | Direction U/D/M |
|---|---|---|---|---|---|---|---|---|
| 10-21 | 67 | 63 | 4 | 0 | 0 | 67 / 7 | 28 / 2 | 7 / 60 / 0 |
| 10-22 | 49 | 43 | 6 | 0 | 0 | 49 / 3 | 24 / 3 | 3 / 46 / 0 |

PIT vs RECONSTRUCTED: both 2.0 sessions are POINT_IN_TIME and 0 are RECONSTRUCTED. The Policy
1.0 history (2026-10-16, 24 files, mtime 10-16) is untouched. No snapshot was rewritten or
backdated. The 10-23 06:42 refresh *reused* the 49 stored 10-22 assessments and wrote nothing.

### Findings

1. **Radar OHLCV reliability fix works.** Every session since `de02d31` (10-21, 10-22) is
   native HEALTHY 200/200. The pre-fix failures (10-08, 10-12, 10-19) are inferred from their
   recorded counts. Their candidate-history markers still read `COMPLETE(0)`, so for 10-19 the
   Policy 2.0 capture reports "no Radar candidates" (NOT_APPLICABLE) for what was really an
   acquisition failure. Repairing them needs the manual-only `invalidate_sessions` +
   `backfill_candidate_history` tools (CLAUDE.md). That is an owner decision and was not done
   here.
2. **The scheduler launches early catch-up invocations.** `config.execution_window`'s contract
   says a start is valid only for `scheduled <= now < deadline`, but `runner.py` only enforces
   the deadline. On 10-21 at 06:48 IST, `intelligence_pm` (19:15) and `post` (19:30) were
   invoked, consistent with Task Scheduler's `StartWhenAvailable` catching up the missed 10-20
   evening run. The runner computed *that day's* evening window, saw it was not past, and
   launched (`child_started=true`). Both records were never closed (RUNNING /
   WAITING_DEPENDENCY), and the log files they reference do not exist. Impact this time: none
   visible, since PRE ran at 07:00 and PK-C/PK-D idempotency guard the rest. Proposed fix
   (next packet): refuse `now < scheduled_dt` as NOT_YET / EXPIRED-for-previous-day, and treat
   orphaned RUNNING records as stale.
3. **Orphaned RUNNING record on 10-19.** `intelligence_am` started at 07:08, never closed, and
   its log is missing. PRE waited on the dependency until its window closed (EXPIRED), so the
   10-19 PRE was not rendered. Root cause is not established; it could be sleep or kill
   mid-run, or log creation failing. Same class as known limitation "RUNNING checkpoint stuck".
4. **Evening windows missed on a sleeping laptop.** 10-21 PM/POST were invoked at 21:16 →
   EXPIRED, and a manual POST at 21:17 succeeded. On 10-22, PM started at 19:31 instead of
   19:15. This is consistent with the power-policy limitation (WakeToRun depends on hardware
   wake timers).
5. **PRE recurring WARNs (DEGRADED, not BLOCKED):** `GLOBAL_CUES:STALE` on 4 of the last 4
   non-blocked PREs (10-23: Hang Seng stale), and `INDIA_VIX:STALE` on 10-22 and 10-23 (10-23:
   "no VIX close for the session before (2026-10-21)"). One `GOVT_SECURITIES_AUCTION`
   SOURCE_UNAVAILABLE on 10-23. Causes were not investigated in this packet.
6. **`run_id` clock inconsistency (cosmetic).** `production_orchestrator` and
   `intelligence_refresh` run_ids are IST wall-clock with a `Z` suffix (e.g.
   `20261023T071434Z` for `started_at 01:44:34+00:00`), while `shadow_scheduler` run_ids are
   true UTC. Sorting and lookup are unaffected, but the suffix is misleading.
7. **POST 2026-10-22 replay closed out.** Freeze-frame QA PASS (6/6 scenes), video QA PASS
   (1080×1920, 30 fps, 32.67 s; sparse session, inside 15–70 s), content QA SAFE, audit
   `final=BLOCK` on `publication_rights` only (REVIEW_REQUIRED: nse_website, yahoo_finance,
   nse_fo_secban, nse_corp_board_meetings, daily_byte_market_structure; policy BLOCK).
   `upload = NOT_ATTEMPTED`. The audit SHA-256 matches the MP4. OPEN/CLOSE labels render
   side by side with no overlap (the open and close were 0.1 pt apart).
8. **Pre-existing on-screen defects in the POST Market Events scene** (not from `fec578b`):
   the chip shows the raw enum `MARKET_EVENTS` (underscore), and the earnings line renders
   `Board meeting to consider results (UNKNOWN)`. `market_events/watch.py:82` interpolates a
   `period` of `UNKNOWN`. It is cosmetic but public-facing, so fix it before any publish
   decision.
9. **Data defects found by the NSE MCP probe** (docs/NSE_MCP_FEASIBILITY.md §4): an IDEA
   2026-09-29 Yahoo volume wrapped by exactly 2³⁰ in `market_ohlcv.db` (affects RVOL), and 183
   holiday placeholder bars on 2026-10-02 stored as `OK` (dropped by calendar-aware readers).

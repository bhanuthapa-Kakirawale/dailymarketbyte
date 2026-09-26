# Production schedule: REPORT -> POST -> PRE

Three jobs, one canonical report per trading session. **Building the market report is its own
job.** POST and PRE are consumers of that report. Neither is the step that creates it.

```
SESSION D (trading day)
  09:15-15:30 IST  NSE session
  15:40 IST        SESSION_FINAL_TIME - D's bars are final (market.SESSION_FINAL_TIME)
  19:30 IST        REPORT job   python main.py --mode report          (market_report.yml)
                     resolve D from the canonical calendar (never mid-session)
                     canonical report for D exists?  -> ALREADY_BUILT (no acquisition)
                     else main.produce_report (acquire -> safety -> build -> validate)
                          fit   -> JSON artifact + MarketHistory (canonical, immutable)
                          unfit -> reports/unfit/ (diagnostic), NOT canonical, retryable
                     intelligence snapshot (derived)   Market Radar for D (idempotent)
                     run record: publication_runs job_type=REPORT_BUILD
  21:30 IST        REPORT retry (same command; a no-op once the report exists)

NEXT SESSION D+1
  07:30 IST        PRE job      python main.py --mode premarket --shadow  (pre_shadow.yml)
                     expected previous session = calendar.previous_session(D+1) = D
                     canonical report for exactly D, publication-ready? else BLOCKED
                     live pre-open data (US closes, Asia 5-min, VIX, GIFT*)
                     PreEditorialPlanner -> render -> QA -> output/pre_shadow/D+1/
                     run record: job_type=PRE_MARKET, publication_status=SHADOW
  07:40 IST        POST job     python main.py --upload               (daily_byte.yml)
                     canonical report for D exists? -> reuse it (REUSED_CANONICAL)
                     else build it with the same code path (BUILT_INLINE, recorded)
                     render -> data/content/video QA -> upload
                     run record: job_type=POST_MARKET
```

`*` GIFT is fetched and audited in shadow mode but shown only when the publication gate is
open. See the GIFT publication policy below.

## Commands

| Job | Command | Writes |
|---|---|---|
| REPORT | `python main.py --mode report [--session-date D] [--skip-radar]` | `output/reports/premarket_<edition>.json`, history rows, `output/intelligence/`, `output/radar/`, `output/report_jobs/<D>/report_job_<run>.json` |
| POST | `python main.py [--upload] [--force]` | video, metadata, QA artifact, run row (the report only if it had to build it) |
| PRE shadow | `python main.py --mode premarket --shadow` | `output/pre_shadow/<D+1>/...`, run row |
| PRE (publication mode, V1: still never uploads) | `python main.py --mode premarket` | `output/premarket/<D+1>/...` |
| Connectivity | `python validate_pre_production.py --connectivity-only` | `connectivity_diagnostic.json` |
| Readiness pack | `python validate_pre_production.py` | `output/pre_production_readiness/` |

## Previous-session resolution (PRE)

`products.premarket.require_previous_report(D+1)`:

1. D+1 must be an NSE session, otherwise the run is **SKIPPED** (`NOT_A_SESSION`).
2. The expected previous session is `SessionCalendar.previous_session(D+1)`. It comes from
   NSE's holiday list plus special sessions, never from calendar day minus one. Monday gives
   Friday. After a holiday it gives the last session before the holiday.
3. `operations.report_lookup.find_canonical_report(expected)` checks four things: a non-demo,
   non-RADAR_SCAN row for exactly that session, an artifact that exists, an artifact that
   parses, and an artifact whose own report_id and session match the row.
4. The report must be `publication_ready`.

If any of these fails, PRE is **BLOCKED**, with code `PREVIOUS_SESSION_MISSING`,
`PREVIOUS_REPORT_UNUSABLE` or `PREVIOUS_REPORT_NOT_PUBLICATION_READY`. An older session's report
is never substituted.

## Report identity and idempotency

- `report_date` is the **edition**: the next canonical session after D (`operations.sessions.
  edition_date_for`). This matches what the 07:40 run always produced, because that run's own
  date is the next session. So a REPORT-job build on the evening of D and a POST build on the
  morning of D+1 give the **same report_id**, and at most one canonical report can exist per
  session.
- A REPORT rerun for a session that is already built acquires nothing and writes nothing
  canonical. It only appends a run row.
- POST re-renders create new video artifacts and a new run row. The canonical report is
  untouched.
- PRE reruns read the same report. PRE never writes canonical history.
- Radar is idempotent: candidate history and editorial selections are keyed on the session,
  and the job skips Radar entirely when D's artifacts already exist and did not fail.

## Failure policy

| Failure | Result |
|---|---|
| REPORT or POST fallback: data validation fails | nothing canonical is written, the diagnostic goes to `reports/unfit/`, the run is BLOCKED, and the job is **retryable** |
| REPORT: benchmark ends before D | SessionAlignmentError, run BLOCKED, nothing written |
| REPORT: `--session-date` not final / not a session | BLOCKED (`SESSION_NOT_FINAL` / `NOT_A_SESSION`) |
| REPORT: Radar or intelligence fails | **DEGRADED**; the report stays canonical |
| REPORT missing at PRE time | PRE **BLOCKED** (`PREVIOUS_SESSION_MISSING`), the missing report is named |
| REPORT missing at POST time | POST builds it inline (`BUILT_INLINE`, recorded). POST does not lose the day's publication |
| POST video/QA failure or crash | POST run FAILED/BLOCKED; the canonical report is untouched and stays valid |
| PRE optional source fails (a global cue, VIX, GIFT unreachable) | **DEGRADED**, still renders |
| GIFT withheld by policy | not a degradation. It is recorded in the GIFT audit |

### Canonical-report rule (owner-approved, PRE shadow readiness)

ONE rule, wherever a report is meant to become canonical (`main.may_become_canonical`, applied in
`main.produce_report` and again in `main.persist_report`): **a MarketReport that fails data
validation never becomes canonical.** It is written to `reports/unfit/` (timestamped, never
indexed), the run is BLOCKED at `DATA_QA` with the blocking issues and the unfit artifact path,
and a later retry (REPORT rerun, or the POST fallback) may build a fit report for the same
session. Reason: a transient 19:30 data gap must not permanently lock an invalid canonical
record, because canonical history is immutable. The POST inline fallback follows the same rule
(it used to persist unfit reports - removed). A POST video may fail or degrade; an invalid report
may not become canonical. Demo reports are exempt (`is_demo=1`, excluded from every query).
"Why wasn't D published?" stays answerable from the run row + the unfit JSON.

## Radar: selected vs published

| | written by | table | meaning |
|---|---|---|---|
| `RADAR_SELECTED` | REPORT job (`radar.daily_pipeline`) | `editorial_selections` | the selector chose it - no viewer has seen it |
| `RADAR_PUBLISHED` | `products.radar_publication.confirm_radar_publication`, after a POST render + every QA gate | `radar_publications` (editorial DB v2) + selection lifecycle `PUBLISHED` | it appeared in a completed, QA-passed POST artifact |

- The REPORT job only selects: its run record carries `radar.selection` (selected count/symbols,
  `published_count: 0`). It never publishes and never advances the cooldown.
- The selector's publication cooldown reads `radar_publications` (`get_prior_publications`),
  never mere selections.
- A failed / QA-blocked POST publishes nothing; only the stories actually rendered count
  (5 selected, 3 shown -> 3 published); confirmation happens once per session - a rerun is
  `ALREADY_CONFIRMED` (flagged if its story set differs), never a duplicate or a second
  cooldown start. Demo never publishes.
- POST run records carry `details.radar`: selected count/symbols, rendered/published
  count/symbols, confirmation status + timestamp, artifact path, QA verdicts.
- **Current production state:** the scheduled POST (`main.run`) still renders the legacy
  `video.py` Short, which has **no Radar section** - so nothing is published and PRE's stock
  watch is omitted. The unified Radar Short (`render_daily_market_byte.py`) confirms publication
  only with `--confirm-publication` (off by default so previews never advance history). The POST
  cut-over to the unified renderer must call `confirm_radar_publication` after its QA gates.
- `python -m operations.stray_cleanup` removed the two known test-contamination REPORT_BUILD
  rows (backup + audit in `output/pre_shadow_readiness/`); `MarketHistory.remove_contaminated_runs`
  is a manual tool - the pipeline never deletes run history (ast-guarded).

## GIFT Nifty publication policy

`operations/gift_policy.py`. It is controlled by two environment variables and is **default OFF**:

- `GIFT_NIFTY_PUBLICATION_ENABLED=true`
- `GIFT_NIFTY_PUBLICATION_APPROVAL=<reference to the written approval or licence>`

Both must be set. The flag alone never enables display. With the gate closed:

- **publication mode** (`--mode premarket`) does not fetch GIFT at all
  (`gift_status=POLICY_DISABLED`);
- **shadow mode** fetches and validates GIFT and writes `gift_audit_<date>.json`, but removes the
  reading from the brief, so no scene, watch card or hook can display it.

Every PRE run records `gift_data_available`, `gift_data_valid`, `gift_publication_allowed`,
`gift_displayed` and `reason`. It also records the reading itself: contract, expiry, live price,
exchange timestamp, previous settlement, computed %, the exchange's DAYCHANGE and PERCHANGE,
whether they reconcile with the settlement, freshness and the verdict. The raw exchange rows are
not copied into the audit or into any test fixture. The rights question is tracked in
[NSEIX_RIGHTS_REVIEW.md](NSEIX_RIGHTS_REVIEW.md).

## Run history (schema v3)

`publication_runs` gains two columns. Both are additive, and legacy rows are never rewritten:

- `job_type`: `REPORT_BUILD` / `POST_MARKET` / `PRE_MARKET`. A pre-v3 row's job is derived from
  its `mode` on read (`StoredRun.job`).
- `source_session_date`: the canonical session whose report the job built or consumed.

Each row carries:

- `target_date`: REPORT and POST use D. PRE uses D+1.
- `run_status`: SUCCESS / DEGRADED / BLOCKED / FAILED / SKIPPED.
- `started_at` / `completed_at`.
- `data_qa_status`.
- `details_json`: report source, readiness, degradations, outputs, GIFT audit.
- The QA columns.
- `publication_status`: POST uses PUBLISHED / NOT_ATTEMPTED / BLOCKED / SKIPPED. PRE uses SHADOW
  or NOT_ATTEMPTED. REPORT uses NOT_APPLICABLE.

`MarketHistory.get_publication_runs(job_type=...)` filters by job.

## Shadow validation period

Run at least **5 consecutive trading-day** PRE shadow mornings. Each
`output/pre_shadow/<D>/` holds:

- `shadow_manifest.json`: the index, verdict, QA, omitted and degraded reasons, and the GIFT
  verdict;
- `pre_section_plan_<D>.json`;
- `pre_provenance_<D>.json`;
- `gift_audit_<D>.json`;
- `pre_run_<D>.json`;
- `full_pre_<D>.mp4`;
- `pre_result_<D>.json` (QA);
- `pre_brief_<D>.json`;
- `pre_acquisition_<D>.json`;
- the contact sheet and the per-scene frames.

A template and the per-morning checklist are in
`output/pre_production_readiness/shadow_run_template/`.

## Operational checks

- `python validate_pre_production.py --connectivity-only` shows whether each source is
  reachable from this machine. On GitHub, use `connectivity.yml`. It never gates publication.
- `operations/official_events.py` warns when an official event's verification is within 30 days
  of the 180-day limit, when the file's coverage ends within 60 days, and in December when next
  year's NSE holiday list is missing. The check is read-only. Fix a warning with
  `python verify_official_events.py --stamp`.

## GitHub Actions state

Runners are ephemeral. The REPORT job's output must survive until the next morning. The
REPORT and PRE-shadow workflows therefore restore and save `output/{data,reports,radar,
intelligence,report_jobs}` through `actions/cache`, under the key prefix `daily-byte-state-`.
They are serialised by one `concurrency` group, so a save can never race another job's save.
The cache is not archival storage: GitHub evicts an entry after 7 days without access, and every
weekday run touches it. The existing `daily_byte.yml` (POST) is deliberately left unchanged
during the shadow period. It still builds its report inline each morning on a fresh runner.

The cut-over after the shadow week is to add the same restore/save steps and `concurrency`
group to `daily_byte.yml`. POST then reuses the evening report.

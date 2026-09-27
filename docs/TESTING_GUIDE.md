# Daily Market Byte — Testing Guide (isolated test runs)

Test runs let you exercise the whole pipeline without touching real state. That covers REPORT,
PRE, POST_UNIFIED, Market Structure, Exchange Watch, IPO Watch, the private Radar, QA and the
publication audit.

> **Never use the test scripts for the normal daily workflow.** Daily:
> `scripts\run_evening.bat`, `scripts\run_morning_pre.bat`, `scripts\run_morning_post.bat`
> (docs/USER_GUIDE.md). Testing: `scripts\test_*.bat`.

## 1. What test mode is

A test run executes the **same** application code as the daily scripts (`python main.py ...`).
The difference is where things go and what is forbidden. Every test run is one folder,
`output\test_runs\<run_id>\`, and that folder is its **entire** world: its own history
databases, reports, Radar, Market Structure, official snapshots, audits, QA, videos, logs and
a `manifest.json`.

## 2. Normal run vs test run

| | Normal (`run_*.bat`) | Test (`test_*.bat`) |
|---|---|---|
| Output root | `output\` | `output\test_runs\<run_id>\` |
| History / Radar databases | the real ones | **copies** (live / replay) or empty (fixture) |
| Writes into `output\data`, `reports`, `radar`, `market_structure`, `official_snapshots`, `private_radar`, ... | yes | **refused** (fail closed) |
| Upload | only with an explicit `--upload` (refused while rights are BLOCK) | **impossible**: `UPLOAD_DISABLED_TEST_CONTEXT` |
| Remote state store (GCS) | if configured | **refused** |

How isolation is enforced:
- `DMB_RUN_CONTEXT=test` + `DAILY_BYTE_OUT=<run folder>`: `config.py` refuses to start a test
  process whose output root is not one folder directly under `output\test_runs\`.
- The writers of durable state (the four databases, reports, Radar, Market Structure, official
  snapshots, audits, QA, run records, PRE/POST renders) call `operations.run_context.guard_write`,
  which refuses production paths.
- `main.py --upload` and `upload.upload` are hard-blocked.
- Production databases are never opened by a test. The run gets **byte copies** of them. A
  SQLite connection, even a read-only one, would create `-wal` / `-shm` files next to a WAL-mode
  database; the isolation proof caught exactly that during development, and it is fixed.
- Production state is fingerprinted (every file, SHA-256) before and after every step. The
  manifest records `production_state_modified`, computed from that comparison.

## 3. Where test output goes

`output\test_runs\<run_id>\`, where `run_id` = `YYYYMMDD_HHMMSS_<mode>`:

| Inside the run folder | What |
|---|---|
| `manifest.json` | mode, steps, statuses, QA, audit, network used, `production_state_modified` |
| `logs\report.log`, `pre.log`, `post.log` | full console output of each step |
| `data\` | the run's databases (copies, or new) |
| `reports\`, `report_jobs\` | the run's canonical report and REPORT run record |
| `radar\`, `market_structure\`, `official_snapshots\`, `intelligence\` | the run's Radar / structure / snapshots |
| `pre_shadow\<date>\` (live, replay) or `premarket\DEMO\` (fixture) | the PRE video, contact sheet, result, audit |
| `post\<tag>\`, `daily_byte_<tag>.mp4`, `publication\<tag>\`, `qa\` | the POST manifest, frames, contact sheet, video, audit, QA |

## 4. Setup check

```
scripts\check_setup.bat
```
It must end with `VERDICT: READY` (the same check as the daily flow).

## 5–8. Running tests

| Test | Command |
|---|---|
| Evening (REPORT + Radar + Market Structure + official snapshots) | `scripts\test_evening.bat` |
| PRE, using the latest test run | `scripts\test_pre.bat` |
| POST, using the latest test run | `scripts\test_post.bat` |
| Full cycle: REPORT → PRE → POST → QA → audit → summary | `scripts\test_full_cycle.bat` |
| Summary of the latest (or one) test run | `scripts\check_test_run.bat` / `scripts\check_test_run.bat --run-id <run_id>` |
| List all test runs | `python -m operations.test_run list` |

Options for every `test_*` script:
- `--mode live` (default for evening / full cycle), `--mode replay --session-date YYYY-MM-DD`,
  or `--mode fixture`;
- `--run-id <run_id>` to continue a specific run (PRE / POST).

- `test_pre` / `test_post` with no options use the **latest** test run. If none exists they
  stop with `TEST STATE MISSING`. They never fall back to production state.
- `--mode replay ...` and `--mode fixture` on `test_pre` / `test_post` start a new run of their own.
- A test on a non-trading day: PRE is `SKIPPED` (not a session). That is expected and not a
  failure.

## 9. Replaying an old session (REPLAY)

```
scripts\test_full_cycle.bat --mode replay --session-date 2026-09-24
scripts\test_post.bat --mode replay --session-date 2026-09-24
```
- It starts from a **read-only copy** of production state.
- The session's canonical report must already exist. A replay never builds one; it stops with
  `REPLAY: production state holds no canonical report for ...`.
- REPORT: `--mode report --session-date D --skip-radar`, which reports `ALREADY_BUILT`. Official
  lists are never captured for a past session.
- PRE: reconstructs the next session's morning (`--mode premarket --shadow --session-date
  <next session>`). It reads dated historical market bars; GIFT is never shown in a
  reconstruction.
- POST: `python main.py --session-date D`, the production replay path. It never fetches, and
  Exchange / IPO Watch report `HISTORICAL_SNAPSHOT_UNAVAILABLE` when no snapshot was preserved.
- Historical source files are never modified: the run works on copies, and the fingerprint proves it.
- **Known limitation:** a PRE replay reconstructs a past morning with cues fetched *today*. Its
  truthful provenance line (`FETCHED: 27 SEP 2026 10:29 AM IST`) is too long for the frame, so
  freeze-frame QA fails the PRE step (`ATTENTION REQUIRED`). Live mornings are unaffected: a
  same-day fetch shows only the time. To replay just the POST, use `scripts\test_post.bat
  --mode replay --session-date D`.

## 10. Live sandbox test (LIVE_SANDBOX)

```
scripts\test_full_cycle.bat --mode live
```
- Fetches the real sources exactly like the evening and morning runs: Yahoo, NSE lists, Gemini
  if configured.
- Starts from a read-only copy of production state, so the Radar has its history.
- Everything it downloads or builds stays in the run folder and is **never promoted** to real
  history. The manifest says `"source_mode": "LIVE_SANDBOX"`.
- The same rules apply as for real runs, e.g. official lists are captured only while the
  session is current (before the next 09:15 open).

**Fixture test (synthetic data, no network):** `scripts\test_full_cycle.bat --mode fixture`.
Every screen carries the SYNTHETIC / DEMO watermark.

## 11. Files to validate

Validate the same things as a real day (docs/USER_GUIDE.md sections 4–5), using the paths in
section 3 above. Look at the videos and contact sheets listed in the summary.

## 12–14. Reading the result

The summary ends with:
- **`VERDICT: PASS`**: every step exited 0; REPORT is SUCCESS or DEGRADED; PRE and POST QA passed
  (or PRE was SKIPPED on a non-trading day); the audits are PASS or RIGHTS_BLOCK_ONLY; and
  `production_state_modified: False`.
- **`RIGHTS_BLOCK_ONLY`**: the publication audit failed **only** `publication_rights`. Every
  content check passed; the video is blocked from YouTube solely by the pending rights review.
  This is expected.
- **`VERDICT: ATTENTION REQUIRED`**: one of the following. Open the step's log
  (`logs\<step>.log`) and the troubleshooting table in docs/USER_GUIDE.md section 6.
  - a step failed or was BLOCKED;
  - QA failed;
  - an audit has a `CONTENT_FAILURE`;
  - production state changed (it never should; report it).

## 15–16. Cleaning test output

```
scripts\clean_test_artifacts.bat                          DRY RUN: lists runs, file count, size
scripts\clean_test_artifacts.bat --run-id <run_id>        just one run (dry run)
scripts\clean_test_artifacts.bat --older-than-days 7      runs older than a week (dry run)
scripts\clean_test_artifacts.bat --execute                delete ALL test runs
scripts\clean_test_artifacts.bat --run-id <run_id> --execute
```
Only folders directly under `output\test_runs\` can be deleted. Run ids are validated (no
`..`, no path separators), symlinks are never followed, and nothing else can be selected.

## 17. Confirming production state was not modified

- Every test summary prints `production_state_modified: False`.
- The manifest holds the evidence per step: `steps.<STEP>.production_proof` gives
  `files_checked` and the changed / added / removed lists, which are empty. `seed_proof` covers
  the initial copy.
- To check by hand, note the timestamps of `output\data\*.db` before a test and compare after.
  They do not change.

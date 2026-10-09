# Daily Market Byte — Operator Guide (local, manual)

This guide is for running Daily Market Byte (DMB) by hand, every trading day, on the owner's
Windows machine. Nothing here uploads to YouTube. In this guide, "session" means an NSE
trading day.

## DAILY NORMAL OPERATION

Two commands a day. Nothing else is needed on a normal day.

**MORNING — around 07:00 IST** (any time before the 09:15 open)

```
scripts\run_morning_pre.bat
```
Purpose: generate today's PRE-MARKET ("before the bell") video.

**EVENING — around 19:30 IST** (after NSE's end-of-day files)

```
scripts\run_evening_full.bat
```
Purpose: collect today's completed-session data and generate today's POST-MARKET recap.

Each command prints one summary and a **VERDICT** line at the end:
- `PASS FOR SHADOW REVIEW` (evening) / `REVIEW THE VIDEO` (morning), exit code 0: review the
  video (section 5).
- `ATTENTION REQUIRED` (evening) / `STOP` (morning), exit code 2: read section 6.

Both commands first run the **production readiness gate** (docs/PRODUCTION_READINESS.md):
`READINESS BLOCKED` means nothing was rendered (read the `fix` lines it prints);
`READINESS DEGRADED` means the video is still generated, without the sections it lists. Run it
by hand any time with `python -m readiness pre` / `python -m readiness post`.

During the shadow trial `PUBLICATION RIGHTS  BLOCKED  rights review only` is **expected** and is
not a video failure. Upload is always `NOT ATTEMPTED`: no normal command uploads.

Other scripts are for troubleshooting only — see section 9.

**PK-D (optional, preferred):** `scripts\run_production_pre.bat` and
`scripts\run_production_post.bat` wrap these same two commands with a run lock, idempotency
(re-running the same morning/evening twice is a safe no-op), `--resume` after an interrupted run,
and one immutable JSON record per run under `output\production_runs\`. `scripts\
check_production_status.bat` shows the latest PRE/POST run without running anything. See
docs/PRODUCTION_ORCHESTRATOR.md. The two commands above keep working unchanged either way.

**Intelligence refresh (optional):** `scripts\run_intelligence_refresh.bat` keeps Private
Desk's intelligence data current without requiring PRE/POST to have run, and self-heals the
previous 30 days of price-derived coverage (index/OHLCV history, Market Structure, Radar
candidate history) where that is safely reconstructable; everything else (Institutional Flow,
Market Events, official snapshots, canonical reports, editorial selections) for a missed day
is permanently unavailable and is reported, never fabricated. `scripts\
check_intelligence_coverage.bat` is read-only. See docs/INTELLIGENCE_REFRESH.md.

---

## 1. Quick start (once, and after every `git pull`)

Open **Command Prompt** (or PowerShell) and run:

```
cd /d D:\Claude_WS\daily_byte
venv\Scripts\activate
pip install -r requirements.txt
scripts\check_setup.bat
```

- **Project directory:** `D:\Claude_WS\daily_byte`.
- **Python environment:** `venv\` (the scripts activate it themselves, and also accept `.venv\`).
- **Configuration:** `.env` in the project directory. Copy `.env.example` to `.env`. Never
  commit it, and never paste its contents anywhere.
  - `GEMINI_API_KEY` is **optional**. Without it, the hook is the deterministic one and no AI
    facts are used.
  - Leave `PUBLIC_REVIEW_REQUIRED_POLICY` **unset** (it defaults to `BLOCK`).
  - Leave `GIFT_NIFTY_PUBLICATION_ENABLED` unset (off).
  - Leave the `DMB_STATE_*` variables unset: the output folder is the state.
- **Optional services:** Gemini (free tier), and the NSE website (often reachable from home;
  when it isn't, the affected optional sections are omitted with a reason). YouTube credentials
  (`token.json`) are **not** used by any script in this guide.
- **Ready check:** `scripts\check_setup.bat` must end with `VERDICT: READY`. It shows whether
  each setting is present, never its value.

---

## 2. Daily manual schedule

### Evening, about 19:30 IST — `scripts\run_evening_full.bat`

One command, run once. In order, and once each:

1. **Session context.** It prints the time (IST), whether today is an NSE session, and the
   latest **completed** session (from NSE's trading calendar).
2. **Evening REPORT** (`python main.py --mode report`, the same command as `run_evening.bat`).
   For the session that just closed it builds:
   1. the **canonical MarketReport**, validated (session alignment, cross-source checks,
      publication readiness). An unfit report is **not** committed; it goes to
      `output\reports\unfit\` for diagnosis;
   2. the historical intelligence snapshot;
   3. the **private Market Radar** (named-stock detectors, candidate history, editorial selection);
   4. **Market Structure** (NIFTY 200 breadth / unusual volume / range, with sector mapping);
   5. the **official daily snapshots**: NSE IPO issue lists, F&O ban file (for the NEXT trade
      date), ASM and GSM. ESM is recorded as NOT_SUPPORTED;
   6. the run record in the history database plus a JSON run record.
3. **REPORT gate.** If the REPORT is BLOCKED or FAILED, crashed, wrote no record, or describes
   another session than the latest completed one, the command **STOPS**: no POST is rendered,
   and the verdict is `ATTENTION REQUIRED`. SUCCESS, or DEGRADED (only an optional part
   failed), continues.
4. **Same-day POST** (`python main.py --no-fetch-public`, the `run_post.bat` command). It renders
   POST_UNIFIED from the canonical report the REPORT just built (`REUSED_CANONICAL`; it never
   builds a second one) and reads only the snapshots the REPORT stored (nothing is fetched
   twice). It then runs video QA, freeze-frame QA, the final content scan and the publication
   audit.
5. **Summary and VERDICT.** One table: REPORT, PRIVATE RADAR, MARKET STRUCTURE, EXCHANGE WATCH,
   IPO WATCH, POST_UNIFIED, VIDEO QA, CONTENT AUDIT, PUBLICATION RIGHTS, the video path and
   duration, notes (degradations, section codes such as `NO_ELIGIBLE_EVENT` or
   `CHANGE_UNKNOWN`), upload status, and the verdict.

**REPORT outcomes:**
- **SUCCESS:** everything was built.
- **DEGRADED:** the canonical report is fine, but an optional part failed (for example the NSE
  lists were unreachable, or the Radar failed). The row shows `DEGRADED`; the POST simply omits
  that section. Not a stop. Institutional-flow capture (NSE/CDSL/NSDL,
  docs/INSTITUTIONAL_FLOW_INTELLIGENCE.md) is strictly additive and can never itself produce a
  DEGRADED or BLOCKED verdict.
- **BLOCKED:** the report failed data validation, or the session is not final or is not on the
  calendar. Nothing canonical was written, no POST is rendered. See section 6.

**Verdict.** `PASS FOR SHADOW REVIEW` needs the REPORT to be SUCCESS or DEGRADED for the right
session, and the POST to describe that same session and pass video QA, freeze-frame QA, the
content scan and an audit of PASS or RIGHTS_BLOCK_ONLY. Anything else is `ATTENTION REQUIRED`.
`CHANGE_UNKNOWN` (the first trial day has no previous official snapshot to compare with),
`NO_ELIGIBLE_EVENT`, `NO_ELIGIBLE_IPO_EVENT` and an optional `SOURCE_UNAVAILABLE` are notes,
never a stop.

**Weekend / holiday / before 15:40 IST.** Nothing is ever built for a day that is not a
completed session. The command says so on its `context` line ("today (Sat 03 Oct) is NOT an
NSE trading session - nothing is built for today; the last completed session is Fri 02 Oct")
and works on that last completed session: the REPORT reports `ALREADY_BUILT`, and the POST is
not rendered again if that session already has a passing one. If that session was missed, it
is built and its POST rendered now (catch-up).

**Rerun (same evening).** Safe:
- the session is reported as `ALREADY_BUILT`; nothing is rebuilt or rewritten, and the Radar is
  not re-run;
- a **validated** official snapshot is never replaced; a **failed** one is re-attempted and
  stored as a new revision (`...r2.json`);
- a POST that already passed for the session is **not** rendered again (`ALREADY RENDERED`); the
  summary is printed from its artifacts. When a rerun recovered a failed snapshot, render the
  POST again with it: `scripts\run_evening_full.bat --rerender-post`;
- a BLOCKED report may be rebuilt once the data gap is fixed.

Retry once or twice with a reason, such as the NSE file being late or a connection error. Do
**not** loop until the result changes.

### Morning, about 07:00 IST — `scripts\run_morning_pre.bat`

```
scripts\run_morning_pre.bat      (runs: python main.py --mode premarket --shadow)
```

- **PRE** previews the session about to open, using the previous session's canonical report
  (built by last evening's command) plus live overnight readings (US closes, Asian markets,
  India VIX). It runs in shadow mode: it renders, checks and records, and **never uploads**.
- **07:00 is fine**; run it before 09:15. The product is driven by the session and the stored
  data, not the clock.
- No POST in the morning: the POST for the previous session was made the evening before.
- If last evening's command did not run or was BLOCKED, PRE is BLOCKED
  (`PREVIOUS_SESSION_MISSING`). Run `scripts\run_evening_full.bat` first (before 09:15 the
  previous session's official lists can still be captured), then PRE.

---

## 3. Where every file is

Dates in the paths:
- `<SESSION>` = the trading session described (e.g. Fri `2026-09-25`).
- `<EDITION>` = the next trading day it is published for (e.g. Mon `2026-09-28`).
- `<RUNDATE>` = the day the script ran. For the POST made by the evening command this is the
  session day itself; a POST made next morning with a troubleshooting script uses that
  morning's date.

| What | Path (under `D:\Claude_WS\daily_byte\`) |
|---|---|
| Canonical report | `output\reports\premarket_<EDITION>.json` (the name says "premarket" for historical reasons; it is THE report for `<SESSION>`) |
| Evening run record | `output\report_jobs\<SESSION>\report_job_<run_id>.json` |
| Market Structure snapshot | `output\market_structure\market_structure_<SESSION>.json` |
| Official snapshot manifest | `output\official_snapshots\<SESSION>\official_snapshot_manifest.json` |
| IPO snapshot | `output\official_snapshots\<SESSION>\ipo_snapshot.json` (a retry: `ipo_snapshot.r2.json`) |
| Exchange Watch snapshots | `output\official_snapshots\<SESSION>\fno_ban_snapshot.json`, `asm_snapshot.json`, `gsm_snapshot.json` |
| Private Radar result | `output\radar\daily_radar_<SESSION>.json`, `output\radar\presentation\radar_presentation_<SESSION>.json` |
| History databases | `output\data\market_history.db`, `market_ohlcv.db`, `radar_candidate_history.db`, `editorial_selections.db` |
| PRE video | `output\pre_shadow\<RUNDATE>\full_pre_<RUNDATE>.mp4` |
| PRE contact sheet | `output\pre_shadow\<RUNDATE>\pre_contact_sheet_<RUNDATE>.png` |
| PRE result / audit / manifest | `output\pre_shadow\<RUNDATE>\pre_result_<RUNDATE>.json`, `publication_audit.json`, `shadow_manifest.json` |
| POST video + metadata | `output\daily_byte_<RUNDATE>.mp4`, `output\daily_byte_<RUNDATE>.json` |
| POST run manifest | `output\post\<RUNDATE>\production_manifest.json` (scenes, inputs, QA, audit, upload status) |
| POST contact sheet | `output\post\<RUNDATE>\post_contact_sheet.png` (written by the check script) |
| POST freeze frames | `output\post\<RUNDATE>\freeze_frames\` |
| POST publication audit | `output\publication\<RUNDATE>\publication_audit.json` |
| POST QA artifact | `output\qa\qa_<EDITION>.json` |
| Run logs | the script's console window. To keep it: `scripts\run_evening_full.bat > output\evening_log.txt 2>&1` |

---

## 4. Evening checklist (before accepting the REPORT)

`scripts\run_evening_full.bat` prints the essentials in its summary; every line below
(metrics, per-list statuses, file paths) is printed by `scripts\check_daily_run.bat`.
Open the files only when something looks wrong.

**SESSION**
- [ ] `session` is the trading day that just closed
- [ ] `report` ID is `<EDITION as YYYYMMDD>_PRE_MARKET` (e.g. `20260928_PRE_MARKET` for Fri 25 Sep) and the source is `BUILT` (or `ALREADY_BUILT` on a retry)
- [ ] `publication ready` is `True`, with no blocking issues

**MARKET DATA**
- [ ] Nifty close in the report matches NSE's published close (spot-check `nifty.close` in the report)
- [ ] no `index fallbacks` line, or it names a recovery you understand
- [ ] `movers coverage` is at least 90%

**MARKET STRUCTURE**
- [ ] the universe is `NIFTY 200 · 200 constituents`
- [ ] every metric's coverage is at least 95% (`PUBLISHABLE`). PARTIAL shows the real
      denominator; SUPPRESSED is never shown.
- [ ] denominators read `/ 200` (or the real partial count)
- [ ] the sector counts in the snapshot add up to the headline count (reconciled at build time;
      a mismatch refuses to load)

**EXCHANGE WATCH**
- [ ] `official snapshots` is SUCCESS or DEGRADED with a reason
- [ ] FNO_BAN / ASM / GSM each show SUCCESS or NO_DATA. The F&O `list date` is the **next**
      trade date.
- [ ] with no previous session snapshot, nothing claims ENTERED or EXITED
      (the change is CHANGE_UNKNOWN; the first trial day is always like this)

**IPO WATCH**
- [ ] IPO snapshot SUCCESS or NO_DATA, with connectivity REACHABLE
- [ ] `list date` is today (the day the list was read)
- [ ] no GMP anywhere (the model cannot hold one)
- [ ] no reconstruction of an older day: a past session is never captured from today's page

**PRIVATE RADAR**
- [ ] `private Radar` is `BUILT` (or `ALREADY_BUILT`) and the pipeline status is not FAILED
- [ ] `output\radar\daily_radar_<SESSION>.json` exists (private candidates preserved)
- [ ] no DEGRADED line naming MARKET_RADAR

---

## 5. Video checklist (before accepting PRE or POST)

The evening / morning command prints the QA and audit lines (`scripts\check_daily_run.bat`
also writes the POST contact sheet). Then **watch the video** and look at the contact sheet.

- [ ] correct market / session date in the header
- [ ] Nifty level and % match the report
- [ ] sector values match the report
- [ ] FII / DII values are correct and marked **provisional**
- [ ] Under the Surface: the denominator is correct (e.g. `179 / 200`) and the universe is named (NIFTY 200)
- [ ] unusual-volume definition reads "at least 2x their own 20-session average"
- [ ] Exchange Watch wording is factual (the exchange's own status: "in the F&O ban period", "Stage I")
- [ ] IPO wording is factual (dates, price band, "closed for bidding"), never "apply" or "good IPO"
- [ ] **SOURCE** and **DATA AS OF** visible on every factual scene
- [ ] no BUY / SELL / HOLD / target / stop-loss / "should" language
- [ ] no named-stock Radar technical analysis (breakouts, volume spikes) in the public video
- [ ] no broken, wrapped, clipped or overlapping text
- [ ] reasonable duration (about 30–60 s; shorter on a quiet day is correct)
- [ ] QA: `video=PASS freeze-frame=True content=SAFE` (POST); `ok=True` (PRE)
- [ ] publication audit status understood (below)

**Content failure vs rights block.** During the shadow trial, `publication audit: BLOCK
['publication_rights'] -> RIGHTS_BLOCK_ONLY` is **EXPECTED**. It means every content check
passed, and the only thing stopping publication is the owner's pending rights review of the
NSE / Yahoo / SEBI data (the default policy is BLOCK). The video is fine to review.

Anything else in the failed list (`displayed_claims`, `recommendation_language`,
`source_visibility`, `language`, ...) is a **CONTENT FAILURE**: the check prints
`CONTENT_FAILURE` and `STOP`. Do not accept that video; see section 6.

---

## 6. When something fails

| Signal | Meaning | Stop the video? | What to inspect | Rerun? |
|---|---|---|---|---|
| `SOURCE_UNAVAILABLE` | A request really failed (timeout, blocked, HTTP error). | No: that section is omitted. | The `connectivity` value in the manifest / snapshot. | Yes, once later (network or NSE hiccup). |
| `PARSE_ERROR` | The source answered, but not in the shape we read (e.g. an NSE page change). | No: section omitted. | The snapshot's `reason`. | No: report it; the adapter may need an update. |
| `VALIDATION_FAILED` | Read fine but wrong, e.g. the F&O file is still for today's trade date. | No: section omitted. | Snapshot `reason` (list date vs expected). | Yes, later that evening: NSE may not have published the next day's file yet. |
| `SNAPSHOT_NOT_CAPTURED` | Current session, but nothing was stored (the evening run was skipped or failed). | No. | `output\official_snapshots\<SESSION>\` | Re-run `run_evening_full.bat --rerender-post` (before 09:15). |
| `HISTORICAL_SNAPSHOT_UNAVAILABLE` | A replay of a past day that has no stored snapshot. | No. | Expected for days before the trial. | No: a past day is never rebuilt from today's pages. |
| `INSUFFICIENT_COVERAGE` | Market Structure coverage below 95%. | No: section omitted. | The metrics lines in the evening check. | Only if the Yahoo data was incomplete; retry once later. |
| `NO_ELIGIBLE_EVENT` / `NO_ELIGIBLE_IPO_EVENT` / `NO_NEW_EVENT` | Lists read fine; nothing qualified or changed today. | No. | Nothing: this is a normal quiet day. | No. |
| Evening `DEGRADED` | Report fine; an optional part failed. | No. | The `DEGRADED` lines. | Yes, once, if the cause is transient. |
| Evening `BLOCKED` / `ATTENTION REQUIRED` before the POST | Report failed validation, the session is not final, or the REPORT describes another session. The POST was not rendered. | No video exists for that session. | `blocking issues`; `output\reports\unfit\`. | Yes, after the data issue is resolved (e.g. Yahoo backfill). Not in a loop. |
| `READINESS BLOCKED` (morning script exit 2 / evening `READINESS` row) | The readiness gate found a broken required input, a timing problem (PRE after 09:15, POST before the session is final) or a publication defect. Nothing was rendered. | No video exists. | The `FAIL` rows and their `fix` lines; `output\readiness\readiness_<EDITION>_<date>.json`. | After fixing the cause (docs/PRODUCTION_READINESS.md "Operator remediation flow"). |
| `READINESS DEGRADED` | Optional inputs missing or stale; those sections are omitted. | No. | The `WARN` rows. | Optional: re-run the REPORT later if a source had not published yet. |
| `READINESS could not evaluate` (exit 30 / 1) | The gate itself failed - never treat it as READY. | Yes (morning script stops). | The error line. | Fix, or run `main.py` by hand if you accept the risk. |
| PRE `BLOCKED` (`PREVIOUS_SESSION_MISSING`) | No canonical report for the previous session. | Yes. | Did the evening run succeed? | Run `run_evening_full.bat`, then PRE. |
| POST `ALREADY RENDERED` | A rerun: this session already has a passing POST. | No. | The summary (read from its artifacts). | Only with `--rerender-post`. |
| QA FAIL (`video`, `freeze-frame`, `content`) | The rendered file or a frame failed a check. | **Yes.** | The QA artifact / freeze-frame QA issues. | Only after understanding why; report it. |
| `CONTENT_FAILURE` in the audit | A content rule failed. | **Yes.** | `publication_audit.json` → `failed_checks`. | No: report it; do not publish. |
| `RIGHTS_BLOCK_ONLY` (`publication_rights`) | Expected during the trial. | No (review the video). | Nothing. | No. |
| Wrong session date | The video describes the wrong day. | **Yes.** | The `session` line, then the report's `session_date`. | Report it; never accept. |
| Missing Market Structure | No snapshot for the session. | No: section omitted. | `output\market_structure\`; the evening Radar status. | Re-run the evening script if the Radar failed. |
| Missing IPO / Exchange Watch | See the section's code in the POST check. | No. | The code tells you which row above applies. | As that row says. |

---

## 7. Testing / dry runs

For isolated test runs, see **docs/TESTING_GUIDE.md**.

- `scripts\test_evening.bat`, `test_pre.bat`, `test_post.bat` and `test_full_cycle.bat`
  (live, replay or fixture) run the same pipeline inside `output\test_runs\<run_id>\`.
- They can never touch real history, the private Radar databases or YouTube.
- **Never use the test scripts for the normal daily workflow.** Daily:
  `run_morning_pre.bat` and `run_evening_full.bat`.
- Clean test output with `scripts\clean_test_artifacts.bat`. It is a dry run until you add
  `--execute`.

## 8. 5-day shadow trial

Use `docs/SHADOW_TRIAL_CHECKLIST.md`: one row per trading day, filled from the check output
and your own review. Nothing is uploaded during the trial.

---

## 9. ADVANCED / TROUBLESHOOTING

Not needed on a normal day. Use these to redo or inspect one step.

| Script | Role | Runs |
|---|---|---|
| `run_production_pre.bat` / `run_production_post.bat` | **Preferred production entry points** (PK-D, docs/PRODUCTION_ORCHESTRATOR.md): the two rows below, plus a run lock, idempotency, `--resume` and an immutable run manifest. | `python -m production_orchestrator pre\|post` |
| `check_production_status.bat` | Read-only: the latest PK-D PRE/POST run (status, readiness, warnings). Runs nothing. | `python -m production_orchestrator status` |
| `install_shadow_tasks.ps1` (no switch / `-WhatIf`) | **Preview only** (PK-E, docs/SHADOW_PRODUCTION_OPERATIONS.md): plans the 4 DMB Windows Task Scheduler entries (intelligence_am/pre/intelligence_pm/post). Installs nothing. | `python -m shadow_scheduler status` underneath, for comparison |
| `install_shadow_tasks.ps1 -Install` | Registers the 4 tasks for real. **Owner-approved step only** - never run automatically. | `Register-ScheduledTask` |
| `check_shadow_tasks.ps1` | Read-only: are the 4 DMB tasks registered correctly (missing/disabled/drift). Runs nothing. | - |
| `remove_shadow_tasks.ps1 -Remove` | Removes only the 4 DMB-owned tasks. | `Unregister-ScheduledTask` |
| `check_shadow_operations.bat` | Read-only: each shadow job's last run + MISSED/PENDING + HEALTHY/ATTENTION/BROKEN. Runs nothing. | `python -m shadow_scheduler status` |
| `run_morning_pre.bat` | **Normal morning command.** Generates PRE. | `python main.py --mode premarket --shadow` + PRE check |
| `run_evening_full.bat` | **Normal evening command.** REPORT + same-day POST + summary. | `python -m operations.evening_full` |
| `run_evening.bat` | Advanced / debug helper. REPORT / acquisition only, no video. | `python main.py --mode report` + evening check |
| `run_post.bat` | Advanced / debug helper. POST render only, for the latest completed session (reuses its canonical report; builds it inline only if it is missing, recorded `BUILT_INLINE`). Extra arguments pass through (e.g. `--no-fetch-public`). | `python main.py` + POST check |
| `run_morning_post.bat` | Old name of `run_post.bat`, kept for compatibility. Same command. | `python main.py` + POST check |
| `check_daily_run.bat` | Read-only status / diagnostic summary of the latest evening, PRE and POST runs (also writes the POST contact sheet). Runs nothing, fetches nothing. | `python -m operations.daily_check all` |
| `check_setup.bat` | Is this machine ready? (no secret values shown) | `python -m operations.daily_check setup` |
| `test_*.bat`, `check_test_run.bat`, `clean_test_artifacts.bat` | **Isolated testing only** (docs/TESTING_GUIDE.md). Never use them for the real daily production-history run. | `python -m operations.test_run ...` |

**`check_daily_run.bat`** is not needed after a successful `run_evening_full.bat`: the evening
summary already shows the essentials. Use it to look at a day later, to troubleshoot, to check
an interrupted run, or to see every detail (Market Structure metrics, per-list snapshot
statuses, file paths) without rerunning anything.

Examples:
- The REPORT was fine but the POST needs another look: `scripts\run_post.bat` (renders it
  again for the latest completed session; never uploads).
- Only the evening data, no video: `scripts\run_evening.bat`.

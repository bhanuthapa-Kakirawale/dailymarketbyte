# 5-day local shadow trial

Nothing is uploaded. Fill one row per trading day from the evening, PRE and POST checks
(`scripts\check_daily_run.bat`) and your own review of the videos (docs/USER_GUIDE.md
sections 4–5).

How to fill each column:
- **REPORT:** SUCCESS / DEGRADED / BLOCKED.
- **PRE / POST:** the VERDICT line (REVIEW / STOP).
- **Market Structure:** RENDERED / code.
- **IPO / Exchange Watch:** code, plus the snapshot status.
- **Radar Private:** BUILT / FAILED.
- **QA:** PASS / FAIL.
- **Audit:** RIGHTS_BLOCK_ONLY (expected) / CONTENT_FAILURE.
- **Manual Data Verification:** Nifty close, sectors and FII/DII cross-checked against NSE by hand (Y/N).

| Date | REPORT | PRE | POST | Market Structure | IPO | Exchange Watch | Radar Private | QA | Audit | Manual Data Verification | Issue / Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Day 1 |  |  |  |  |  |  |  |  |  |  |  |
| Day 2 |  |  |  |  |  |  |  |  |  |  |  |
| Day 3 |  |  |  |  |  |  |  |  |  |  |  |
| Day 4 |  |  |  |  |  |  |  |  |  |  |  |
| Day 5 |  |  |  |  |  |  |  |  |  |  |  |

Expected on Day 1: Exchange Watch changes are `CHANGE_UNKNOWN`, since there is no previous
snapshot yet. From Day 2, ENTERED / EXITED / STAGE_CHANGED can appear.

**Pass criteria for the trial:**
- five trading days with REPORT SUCCESS or explained DEGRADED;
- PRE and POST QA PASS;
- audit RIGHTS_BLOCK_ONLY, with no CONTENT_FAILURE;
- manual data verification Y every day;
- no wrong-session video.

A public YouTube trial is a separate owner decision after this, together with the rights review.

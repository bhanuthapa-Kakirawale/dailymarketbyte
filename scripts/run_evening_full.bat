@echo off
REM Daily Market Byte - EVENING, the ONE normal evening command (about 19:30 IST, after NSE's
REM end-of-day files). Runs, in order and once each:
REM   1. the evening REPORT (same command as run_evening.bat): canonical MarketReport, private
REM      Radar, Market Structure, official snapshots (NSE IPO lists, F&O ban, ASM, GSM)
REM   2. a REPORT gate - a failed / unresolved / wrong-session REPORT STOPS here, no POST
REM   3. the same-day POST_UNIFIED (same command as run_post.bat) from that canonical report
REM   4. QA + publication audit, then ONE summary and VERDICT
REM Never uploads. Safe to re-run: a built session is not rebuilt, a validated snapshot is not
REM replaced, and a session that already has a passing POST is not re-rendered
REM (--rerender-post forces it). Exit code 2 = ATTENTION REQUIRED (docs\USER_GUIDE.md).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m operations.evening_full %*
exit /b %ERRORLEVEL%

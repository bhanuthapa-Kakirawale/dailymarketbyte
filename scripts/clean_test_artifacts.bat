@echo off
REM Daily Market Byte - TEST CLEANUP of test runs - DRY RUN unless --execute.
REM Isolated: writes only to output\test_runs\<run_id>\ and never uploads. See docs\TESTING_GUIDE.md.
REM Optional: --mode live^|replay^|fixture  --session-date YYYY-MM-DD  --run-id ID
REM Never use test scripts for the normal daily workflow (run_evening / run_morning_*).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python scripts\clean_test_artifacts.py %*
exit /b %ERRORLEVEL%

@echo off
REM Daily Market Byte - TEST CHECK: summary and VERDICT of the latest (or --run-id) test run.
REM Isolated: writes only to output\test_runs\<run_id>\ and never uploads. See docs\TESTING_GUIDE.md.
REM Optional: --mode live^|replay^|fixture  --session-date YYYY-MM-DD  --run-id ID
REM Never use test scripts for the normal daily workflow (run_evening / run_morning_*).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m operations.test_run check %*
exit /b %ERRORLEVEL%

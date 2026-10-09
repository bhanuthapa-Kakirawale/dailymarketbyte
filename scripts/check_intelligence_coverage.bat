@echo off
REM Daily Market Byte - read-only: the last intelligence_refresh run's recorded coverage
REM (sessions audited, complete/backfilled/historical-unavailable counts, blocking reasons).
REM Never acquires, never fetches, never renders.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m intelligence_refresh status %*
exit /b %ERRORLEVEL%

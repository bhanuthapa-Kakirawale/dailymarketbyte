@echo off
REM Daily Market Byte - PK-E read-only status: the 4 shadow-scheduled jobs' last run + a
REM MISSED/PENDING/NOT_EXPECTED check + HEALTHY/ATTENTION/BROKEN. Never runs a job, never
REM touches Task Scheduler, never writes. See docs\SHADOW_PRODUCTION_OPERATIONS.md.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m shadow_scheduler status %*
exit /b %ERRORLEVEL%

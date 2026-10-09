@echo off
REM Daily Market Byte - PK-D read-only status: the latest PRE/POST production run (session,
REM orchestrator_status, both PK-C readiness verdicts, video path, warnings). Never acquires,
REM evaluates readiness or renders anything.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m production_orchestrator status %*
exit /b %ERRORLEVEL%

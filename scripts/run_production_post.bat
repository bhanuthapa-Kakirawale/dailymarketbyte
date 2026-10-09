@echo off
REM Daily Market Byte - PK-D PRODUCTION ENTRY POINT (POST). The preferred evening command: a
REM run lock, idempotency and --resume around the same lower-level steps run_evening_full.bat
REM already runs (REPORT, the REPORT gate, PK-C readiness, POST_UNIFIED), plus a PK-C
REM POST_RENDER check and one immutable run manifest under output\production_runs\POST\.
REM Never uploads. run_evening_full.bat remains available for diagnosis.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m production_orchestrator post %*
exit /b %ERRORLEVEL%

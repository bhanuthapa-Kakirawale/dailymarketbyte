@echo off
REM Daily Market Byte - PK-D PRODUCTION ENTRY POINT (PRE). The preferred morning command: a
REM run lock, idempotency and --resume around the same lower-level steps run_morning_pre.bat
REM already runs (readiness pre, then main.py --mode premarket --shadow), plus a PK-C
REM POST_RENDER check and one immutable run manifest under output\production_runs\PRE\.
REM Never uploads (PRE has no upload path). run_morning_pre.bat remains available for diagnosis.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m production_orchestrator pre %*
exit /b %ERRORLEVEL%

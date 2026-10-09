@echo off
REM Daily Market Byte - read-only scan of output/ for instruction-shaped payloads.
REM Never executes/deletes/writes anything; see docs\REPOSITORY_AGENT_SAFETY.md.
REM Optional: --path DIR  --max-bytes N  --ext json,txt,log
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python scripts\check_agent_safety.py %*
exit /b %ERRORLEVEL%

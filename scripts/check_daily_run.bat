@echo off
REM Daily Market Byte - summary of the latest EVENING / PRE / POST runs (read-only; also writes
REM the POST contact sheet). Exit code 2 = something needs attention (docs\USER_GUIDE.md).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m operations.daily_check all
exit /b %ERRORLEVEL%

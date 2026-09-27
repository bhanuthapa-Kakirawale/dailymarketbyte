@echo off
REM Daily Market Byte - is this machine ready? (packages, .env present, ffmpeg, output folder).
REM Shows whether each setting is present, never its value.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m operations.daily_check setup
exit /b %ERRORLEVEL%

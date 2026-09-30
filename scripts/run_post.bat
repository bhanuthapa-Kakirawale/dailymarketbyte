@echo off
REM Daily Market Byte - POST (POST_UNIFIED recap of the latest completed session). Advanced /
REM troubleshooting helper: the normal evening command scripts\run_evening_full.bat already runs
REM this step after the REPORT. Same canonical command as run_morning_post.bat (kept as a
REM backward-compatible alias); extra arguments pass through, e.g. --no-fetch-public.
REM This script NEVER uploads: it never passes --upload (and the publication audit would refuse
REM an upload anyway while the rights policy is BLOCK).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
echo ================================================================
echo  POST  -  started %DATE% %TIME%
echo  output: %CD%\output\post  (video: %CD%\output\daily_byte_^<date^>.mp4)
echo ================================================================
python main.py %*
set RC=%ERRORLEVEL%
echo.
python -m operations.daily_check post
set CHECK=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo POST exited with code %RC% - see docs\USER_GUIDE.md section 6
    exit /b %RC%
)
exit /b %CHECK%

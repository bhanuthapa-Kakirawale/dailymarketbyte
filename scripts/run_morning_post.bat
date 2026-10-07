@echo off
REM Daily Market Byte - MORNING POST (POST_UNIFIED recap of the previous session). Any time
REM before 09:15 IST (07:00 is fine). Renders from the canonical report + snapshots the evening
REM REPORT stored. This script NEVER uploads: it never passes --upload (and the publication
REM audit would refuse an upload anyway while the rights policy is BLOCK).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
echo ================================================================
echo  MORNING POST  -  started %DATE% %TIME%
echo  output: %CD%\output\post  (video: %CD%\output\daily_byte_^<date^>.mp4)
echo ================================================================
python main.py
set RC=%ERRORLEVEL%
echo.
python -m operations.daily_check post
set CHECK=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo POST exited with code %RC% - see docs\USER_GUIDE.md section 6
    exit /b %RC%
)
exit /b %CHECK%

@echo off
REM Daily Market Byte - EVENING (about 19:30 IST, after NSE's end-of-day files).
REM Canonical MarketReport + validation + private Radar + Market Structure + official snapshots
REM (NSE IPO lists, F&O ban, ASM, GSM). No video. Never uploads. Safe to re-run: a built session
REM is not rebuilt, and a validated snapshot is never replaced.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
echo ================================================================
echo  EVENING REPORT  -  started %DATE% %TIME%
echo  output: %CD%\output
echo ================================================================
python main.py --mode report
set RC=%ERRORLEVEL%
echo.
python -m operations.daily_check evening
set CHECK=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo REPORT job exited with code %RC% - see docs\USER_GUIDE.md section 6
    exit /b %RC%
)
exit /b %CHECK%

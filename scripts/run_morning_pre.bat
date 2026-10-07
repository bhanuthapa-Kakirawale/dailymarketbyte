@echo off
REM Daily Market Byte - MORNING PRE ("before the bell"), any time before 09:15 IST (07:00 is fine).
REM Shadow mode: renders into output\pre_shadow\<date>\ and NEVER uploads (PRE has no upload path).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
echo ================================================================
echo  MORNING PRE (shadow)  -  started %DATE% %TIME%
echo  output: %CD%\output\pre_shadow
echo ================================================================
python main.py --mode premarket --shadow
set RC=%ERRORLEVEL%
echo.
python -m operations.daily_check pre
set CHECK=%ERRORLEVEL%
if not "%RC%"=="0" (
    echo PRE exited with code %RC% - see docs\USER_GUIDE.md section 6
    exit /b %RC%
)
exit /b %CHECK%

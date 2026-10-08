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
REM Production readiness gate (docs\PRODUCTION_READINESS.md): 0 READY, 10 DEGRADED (continue),
REM 20 BLOCKED (nothing is rendered), anything else = the gate could not evaluate (stop).
python -m readiness pre
set READY_RC=%ERRORLEVEL%
if "%READY_RC%"=="20" (
    echo READINESS BLOCKED - nothing rendered. See the blocking reasons above and
    echo docs\PRODUCTION_READINESS.md. VERDICT: STOP
    exit /b 2
)
if "%READY_RC%"=="10" echo READINESS DEGRADED - continuing; what the warnings above affect is omitted safely.
if not "%READY_RC%"=="0" if not "%READY_RC%"=="10" (
    echo READINESS could not evaluate ^(exit %READY_RC%^) - nothing rendered. VERDICT: STOP
    exit /b 1
)
echo.
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

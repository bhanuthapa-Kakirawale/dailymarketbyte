@echo off
REM Shared setup for the manual Daily Market Byte scripts (called by the other scripts).
REM Activates the project's virtual environment. Prints no secret; never uploads anything.
if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo ERROR: no virtual environment found ^(venv\ or .venv\^) - see docs\USER_GUIDE.md section 1
    exit /b 1
)
REM UTF-8 console output (the rupee sign and arrows in the run log)
set PYTHONIOENCODING=utf-8
exit /b 0

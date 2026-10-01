@echo off
REM Daily Market Byte - PRIVATE TRADING INTELLIGENCE DESK (local, read-only web dashboard).
REM Opens http://127.0.0.1:8765 . Never trades, never places orders, never uploads anything,
REM needs no YouTube or broker credentials. Stop with Ctrl+C. Guide: docs\PRIVATE_DESK_USER_GUIDE.md
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m private_desk.app %*
set RC=%ERRORLEVEL%
exit /b %RC%

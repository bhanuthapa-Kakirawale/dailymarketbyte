@echo off
REM Daily Market Byte - Private Desk health / freshness check (read-only, starts no server).
REM Exit code 2 = something needs attention (docs\PRIVATE_DESK_USER_GUIDE.md).
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m private_desk.check %*
exit /b %ERRORLEVEL%

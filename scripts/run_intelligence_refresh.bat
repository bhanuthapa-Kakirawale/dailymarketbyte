@echo off
REM Daily Market Byte - keeps Private Desk intelligence fresh WITHOUT running PRE/POST video
REM production: catches up the latest session (via the existing REPORT job) and self-heals
REM the previous 30 calendar days of price-derived coverage (index/OHLCV history, Market
REM Structure, Radar candidate history) where that is safely reconstructable. Never renders
REM video, never uploads. See docs/INTELLIGENCE_REFRESH.md.
setlocal
cd /d "%~dp0.."
call "%~dp0_env.bat" || exit /b 1
python -m intelligence_refresh refresh %*
exit /b %ERRORLEVEL%

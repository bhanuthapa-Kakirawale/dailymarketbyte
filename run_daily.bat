@echo off
REM Called by Windows Task Scheduler at 8:00 AM. Logs go to output\run.log
cd /d %~dp0
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
if not exist output mkdir output
python main.py --upload >> output\run.log 2>&1

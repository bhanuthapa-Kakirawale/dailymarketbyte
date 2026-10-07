@echo off
REM LEGACY entry point (was: Task Scheduler at 8:00 AM with --upload). During the local manual
REM trial it NEVER uploads: it runs the morning POST script (render + QA + audit only).
REM Uploading is a separate, explicit owner decision: python main.py --upload
REM (refused anyway while PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK). See docs\USER_GUIDE.md.
cd /d %~dp0
call scripts\run_morning_post.bat
exit /b %ERRORLEVEL%

#!/usr/bin/env bash
# LEGACY entry point (was: cron at 8:00 AM IST with --upload). It NEVER uploads now: it renders
# the POST (QA + publication audit) only. Uploading is a separate, explicit owner decision
# (`python main.py --upload`, refused anyway while PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK).
# See docs/USER_GUIDE.md. Logs go to output/run.log
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null
python main.py >> output/run.log 2>&1

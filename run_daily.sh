#!/usr/bin/env bash
# Called by cron at 8:00 AM IST. Logs go to output/run.log
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null
export GEMINI_API_KEY="${GEMINI_API_KEY:-}"   # free key from aistudio.google.com
python main.py --upload >> output/run.log 2>&1

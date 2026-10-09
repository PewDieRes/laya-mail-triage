#!/bin/sh
# Resume the month-by-month backfill in the background. Already-labelled mail is skipped,
# so it is always safe to re-run. Usage: scripts/resume_backfill.sh [YYYY-MM to start from]
set -e
cd "$(dirname "$0")/.."
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running: open Docker Desktop, wait until it says 'Running', then re-run this." >&2
  exit 1
fi
START=${1:-$(date +%Y-%m)}
echo "# resumed $(date '+%Y-%m-%d %H:%M') from $START" >> data/backfill-progress.txt
nohup caffeinate -is docker compose run --rm -v ./triage:/app/triage -v ./scripts:/app/scripts triage \
  python scripts/backfill_months.py "$START" >> data/backfill.log 2>&1 &
echo "Backfill running in the background from $START."
echo "Progress: cat data/backfill-progress.txt"

#!/usr/bin/env bash
#
# Sends the boss the morning number, from the server, every day.
#
# The point is that nobody decides to send it. "Everything is working" from the
# person building it is worth little; a figure that arrives on a schedule is
# worth something, and on the day it does not arrive that is information too.
#
# Runs from cron at 06:05 UTC:
#
#   5 6 * * * /opt/call_platform/deploy/daily_status.sh >> /var/log/avortyx-daily-status.log 2>&1
#
# Lives on the host rather than in Celery on purpose. The watchdog's log is on
# the host and the container cannot read it, and a report that stops arriving
# because Celery died would be the one morning it mattered most.
#
set -uo pipefail

APP_DIR=/opt/call_platform
WATCHDOG_LOG=/var/log/avortyx-watchdog.log

# ── measure availability from what the watchdog actually recorded ────────────
# It writes one line per run. A run that found everything healthy ends in
# "ok routing=403 ..."; a run that found a fault wrote an ALERT line. Counting
# them is a measurement rather than a claim.

YESTERDAY=$(date -u -d 'yesterday' +%Y-%m-%d)

CHECKS=0
FAILED=0
if [ -r "$WATCHDOG_LOG" ]; then
    CHECKS=$(grep -c "^${YESTERDAY}T" "$WATCHDOG_LOG" 2>/dev/null || echo 0)
    FAILED=$(grep "^${YESTERDAY}T" "$WATCHDOG_LOG" 2>/dev/null | grep -c 'ALERT' || echo 0)
fi

ARGS=()
# Only passed when there is something real to pass. Without them the report says
# "not measured", which is the honest answer and better than a comforting 100%
# that came from an empty log.
if [ "$CHECKS" -gt 0 ]; then
    ARGS+=(--checks "$CHECKS" --failed "$FAILED")
fi

cd "$APP_DIR" || exit 1
docker compose exec -T web python manage.py daily_status "${ARGS[@]}"

echo "$(date -u +%FT%TZ) daily status run: checks=$CHECKS failed=$FAILED"

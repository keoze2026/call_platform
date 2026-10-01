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
# It writes one status line per run:
#
#   2026-10-01T14:02:01Z ok routing=403 portal=200 disk=17%
#
# Healthy means routing answered 403 (it wants the shared secret, so anything
# else means calls cannot be routed) and the portal answered 200.
#
# Counting ALERT lines instead would be wrong, and wrong in the flattering
# direction: the watchdog alerts once per fault and not once per check, so a
# two-hour outage leaves a single ALERT line among sixty failed checks and the
# morning report would claim 99.9% availability for a morning the platform was
# down. Every status line is counted, and a line that is not healthy is a
# failure.

YESTERDAY=$(date -u -d 'yesterday' +%Y-%m-%d)

CHECKS=0
HEALTHY=0
FAILED=0
if [ -r "$WATCHDOG_LOG" ]; then
    CHECKS=$(grep -c "^${YESTERDAY}T.*routing=" "$WATCHDOG_LOG" 2>/dev/null || echo 0)
    HEALTHY=$(grep "^${YESTERDAY}T" "$WATCHDOG_LOG" 2>/dev/null \
              | grep -c 'routing=403 portal=200' || echo 0)
    FAILED=$(( CHECKS - HEALTHY ))
    [ "$FAILED" -lt 0 ] && FAILED=0
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

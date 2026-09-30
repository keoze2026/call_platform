#!/usr/bin/env bash
#
# Tells you when the platform stops working, before a client does.
#
# Twice in two days the platform broke and the client noticed first: routing
# returned 500 for two hours, and nginx sat down for eight. Both were visible on
# the server the whole time and nobody was looking.
#
# Runs from cron every two minutes. Deliberately outside Django and outside the
# call path: it only reads. If this script itself breaks, nothing breaks with it -
# you simply stop getting alerts, which is why it also sends a daily heartbeat.
#
# Alerts go to the Telegram group already used for support.
#
#   */2 * * * * /opt/call_platform/deploy/watchdog.sh >> /var/log/avortyx-watchdog.log 2>&1
#
set -uo pipefail

APP_DIR=/opt/call_platform
STATE=/var/lib/avortyx-watchdog
mkdir -p "$STATE"

# Calls are not expected around the clock. Outside these hours a quiet line is
# normal and must not page anyone.
BUSY_START_UTC=13
BUSY_END_UTC=23
# How long without a call, during busy hours, before that is worth saying.
QUIET_MINUTES=25

# ── talking to Telegram ──────────────────────────────────────────────────────

send() {
    local text="$1"
    local token chat
    token=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)
    chat=$(grep -m1 '^TELEGRAM_SUPPORT_CHAT_ID=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)
    [ -n "$token" ] && [ -n "$chat" ] || { echo "no telegram credentials"; return; }
    curl -s -o /dev/null --max-time 10 \
        -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat}" \
        --data-urlencode "text=${text}" \
        -d "disable_web_page_preview=true"
}

# Alert once per fault, and again when it clears. Without this a two-hour outage
# sends sixty identical messages and the next real one gets ignored.
alert() {
    local key="$1" msg="$2"
    if [ ! -f "$STATE/$key" ]; then
        date -u +%s > "$STATE/$key"
        send "🔴 AVORTYX — $msg"
        echo "$(date -u +%FT%TZ) ALERT $key: $msg"
    fi
}

clear_alert() {
    local key="$1" msg="$2"
    if [ -f "$STATE/$key" ]; then
        local since
        since=$(( ($(date -u +%s) - $(cat "$STATE/$key")) / 60 ))
        rm -f "$STATE/$key"
        send "🟢 AVORTYX — $msg (was down ~${since} min)"
        echo "$(date -u +%FT%TZ) CLEARED $key after ${since}m"
    fi
}

# ── 1. the routing endpoint, which is what actually matters ──────────────────
# 403 is the correct answer: it wants the shared secret. Anything else - 500, 502,
# or no answer at all - means calls cannot be routed.

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
       -X POST http://127.0.0.1:8000/api/twilio/asterisk/route/ 2>/dev/null)

if [ "$code" = "403" ]; then
    clear_alert routing "routing is answering again"
else
    alert routing "ROUTING IS DOWN — /api/twilio/asterisk/route/ returned '${code:-no response}' (expected 403). Calls cannot be routed."
fi

# ── 2. the portal, as the outside world sees it ──────────────────────────────

portal=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://avortyx.io/api/docs 2>/dev/null)
if [ "$portal" = "200" ]; then
    clear_alert portal "portal is back"
else
    alert portal "PORTAL IS DOWN — https://avortyx.io returned '${portal:-no response}'."
fi

# ── 3. nginx ─────────────────────────────────────────────────────────────────
# It stopped on 30 September during a certificate renewal and stayed down,
# because a config it could not load stops it starting at all.

if systemctl is-active --quiet nginx; then
    clear_alert nginx "nginx is running again"
else
    alert nginx "NGINX IS DOWN — nothing is serving the portal or recordings."
fi

# ── 4. asterisk ──────────────────────────────────────────────────────────────

if systemctl is-active --quiet asterisk; then
    clear_alert asterisk "asterisk is running again"
else
    alert asterisk "ASTERISK IS DOWN — no calls can arrive at all."
fi

# ── 5. the containers ────────────────────────────────────────────────────────

down=$(cd "$APP_DIR" && docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null \
       | grep -v ' running' | awk '{print $1}' | paste -sd, -)
if [ -z "$down" ]; then
    clear_alert containers "all containers are running again"
else
    alert containers "CONTAINERS DOWN — ${down}"
fi

# ── 6. calls actually arriving, during hours when they should be ─────────────
# The subtlest failure: everything answers, and no calls come. That is what both
# outages looked like from the outside.

hour=$(date -u +%-H)
if [ "$hour" -ge "$BUSY_START_UTC" ] && [ "$hour" -lt "$BUSY_END_UTC" ]; then
    # The shell prints a banner line first ("75 objects imported automatically"),
    # so the value is marked and only the marked line is read. Stripping the whole
    # output to digits glued the banner's numbers onto the answer and reported
    # 75234 minutes for a 20 hour gap.
    mins=$(cd "$APP_DIR" && docker compose exec -T web python manage.py shell -c "
from django.utils import timezone
from routing.models import CallLog
c = CallLog.objects.order_by('-created_at').first()
print('WATCHDOG_MINS', int((timezone.now() - c.created_at).total_seconds() // 60) if c else 99999)
" 2>/dev/null | grep '^WATCHDOG_MINS' | awk '{print $2}')

    if [ -n "$mins" ]; then
        if [ "$mins" -gt "$QUIET_MINUTES" ]; then
            if [ "$mins" -gt 180 ]; then
                human="$(( mins / 60 )) hours"
            else
                human="${mins} minutes"
            fi
            alert traffic "NO CALLS for ${human} during busy hours. Everything is answering, so either nothing is being sent or calls are failing before they reach us."
        else
            clear_alert traffic "calls are arriving again"
        fi
    fi
fi

# ── 7. disk ──────────────────────────────────────────────────────────────────
# 45GB of unrotated Asterisk logs nearly filled this once.

used=$(df / --output=pcent | tail -1 | tr -cd '0-9')
if [ "${used:-0}" -ge 85 ]; then
    alert disk "DISK AT ${used}% — the platform stops writing when it fills."
else
    clear_alert disk "disk usage back to ${used}%"
fi

# ── 8. a daily heartbeat ─────────────────────────────────────────────────────
# So silence means "nothing is wrong", not "the watchdog died".

today=$(date -u +%F)
if [ "$(cat "$STATE/heartbeat" 2>/dev/null)" != "$today" ]; then
    if [ "$hour" -eq "$BUSY_START_UTC" ]; then
        echo "$today" > "$STATE/heartbeat"
        calls=$(cd "$APP_DIR" && docker compose exec -T web python manage.py shell -c "
from django.utils import timezone
from routing.models import CallLog
print('WATCHDOG_CALLS', CallLog.objects.filter(created_at__date=timezone.now().date()).count())
" 2>/dev/null | grep '^WATCHDOG_CALLS' | awk '{print \$2}')
        send "✅ AVORTYX — all checks passing. Calls so far today: ${calls:-0}. Disk ${used}%."
    fi
fi

echo "$(date -u +%FT%TZ) ok routing=$code portal=$portal disk=${used}%"

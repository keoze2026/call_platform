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
    # WATCHDOG_DRY_RUN=1: print the message instead of sending it.
    if [ -n "${WATCHDOG_DRY_RUN:-}" ]; then printf '%s\n---\n' "$text"; return; fi
    local token chat
    token=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)
    chat=$(grep -m1 '^TELEGRAM_SUPPORT_CHAT_ID=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)
    [ -n "$token" ] && [ -n "$chat" ] || { echo "no telegram credentials"; return; }
    curl -s -o /dev/null --max-time 10 \
        -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat}" \
        --data-urlencode "text=${text}" \
        -d "parse_mode=HTML" \
        -d "disable_web_page_preview=true"
}

# Alert once per fault, and again when it clears. Without this a two-hour outage
# sends sixty identical messages and the next real one gets ignored.
#
# Wording: Sir's template (24 Oct, "very professional language under every
# notification"), sent as Telegram HTML:
#
#   🔴 <b>AVORTYX | Service Disruption</b>
#   The AVORTYX portal is currently unreachable. Our team has been notified
#   and is investigating.
#   <b>Detected:</b> Oct 24, 2026, 8:08 AM ET
#
#   🟢 <b>AVORTYX | Service Restored</b>
#   The AVORTYX portal is fully operational. Service was interrupted for
#   approximately 1 minute.
#   <b>Resolved:</b> Oct 24, 2026, 8:09 AM ET
#
#   alert KEY ICON "Title" "Body" [START_EPOCH]
#       START_EPOCH: when the fault really began, if earlier than now (the
#       traffic check knows when the last call came in).
#   clear_alert KEY "Title" "Body with {dur}"
#       {dur} becomes how long it lasted, e.g. "approximately 3 minutes".

# Times are shown in the reports' zone, Eastern by default.
WATCHDOG_TZ="${WATCHDOG_TZ:-America/New_York}"
WATCHDOG_TZ_LABEL="${WATCHDOG_TZ_LABEL:-ET}"

stamp() {
    echo "$(TZ="$WATCHDOG_TZ" date "+%b %-d, %Y, %-I:%M %p") ${WATCHDOG_TZ_LABEL}"
}

alert() {
    local key="$1" icon="$2" title="$3" body="$4" began="${5:-}"
    if [ ! -f "$STATE/$key" ]; then
        echo "${began:-$(date -u +%s)}" > "$STATE/$key"
        send "${icon} <b>AVORTYX | ${title}</b>
${body}
<b>Detected:</b> $(stamp)"
        echo "$(date -u +%FT%TZ) ALERT $key: $title - $body"
    fi
}

clear_alert() {
    local key="$1" title="$2" body="$3"
    if [ -f "$STATE/$key" ]; then
        local since
        since=$(( ($(date -u +%s) - $(cat "$STATE/$key")) / 60 ))
        rm -f "$STATE/$key"
        body="${body//\{dur\}/approximately $(duration "$since")}"
        send "🟢 <b>AVORTYX | ${title}</b>
${body}
<b>Resolved:</b> $(stamp)"
        echo "$(date -u +%FT%TZ) CLEARED $key after ${since}m"
    fi
}

# "1 minute", "27 minutes", "3 hours 10 minutes".
duration() {
    local m="$1"
    if [ "$m" -lt 1 ]; then echo "1 minute"; return; fi
    if [ "$m" -lt 60 ]; then
        [ "$m" -eq 1 ] && echo "1 minute" || echo "$m minutes"; return
    fi
    local h=$(( m / 60 )) r=$(( m % 60 ))
    local hs="$h hours"; [ "$h" -eq 1 ] && hs="1 hour"
    local rs="$r minutes"; [ "$r" -eq 1 ] && rs="1 minute"
    if [ "$r" -eq 0 ]; then echo "$hs"; else echo "$hs $rs"; fi
}

# ── 1. the routing endpoint, which is what actually matters ──────────────────
# 403 is the correct answer: it wants the shared secret. Anything else - 500, 502,
# or no answer at all - means calls cannot be routed.

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
       -X POST http://127.0.0.1:8000/api/twilio/asterisk/route/ 2>/dev/null)

if [ "$code" = "403" ]; then
    clear_alert routing "Call Routing Restored" "AVORTYX call routing is fully operational. Calls were not forwarded for {dur}."
else
    alert routing "🔴" "Call Routing Disruption" "AVORTYX call routing is currently unavailable, and incoming calls cannot be forwarded. Our team has been notified and is investigating."
fi

# ── 2. the portal, as the outside world sees it ──────────────────────────────

portal=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://avortyx.io/api/docs 2>/dev/null)
if [ "$portal" = "200" ]; then
    clear_alert portal "Service Restored" "The AVORTYX portal is fully operational. Service was interrupted for {dur}."
else
    alert portal "🔴" "Service Disruption" "The AVORTYX portal is currently unreachable. Our team has been notified and is investigating."
fi

# ── 3. nginx ─────────────────────────────────────────────────────────────────
# It stopped on 30 September during a certificate renewal and stayed down,
# because a config it could not load stops it starting at all.

if systemctl is-active --quiet nginx; then
    clear_alert nginx "Web Service Restored" "The AVORTYX web service is fully operational. The portal and call recordings were unavailable for {dur}."
else
    alert nginx "🔴" "Web Service Disruption" "The AVORTYX web service is currently down, and the portal and call recordings are unavailable. Our team has been notified and is investigating."
fi

# ── 4. asterisk ──────────────────────────────────────────────────────────────

if systemctl is-active --quiet asterisk; then
    clear_alert asterisk "Call Service Restored" "The AVORTYX phone system is fully operational. Calls could not be received for {dur}."
else
    alert asterisk "🔴" "Call Service Disruption" "The AVORTYX phone system is currently down, and no calls can be received. Our team has been notified and is investigating."
fi

# ── 5. the containers ────────────────────────────────────────────────────────

down=$(cd "$APP_DIR" && docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null \
       | grep -v ' running' | awk '{print $1}' | paste -sd, -)
if [ -z "$down" ]; then
    clear_alert containers "Platform Services Restored" "All AVORTYX platform services are fully operational. Service was interrupted for {dur}."
else
    alert containers "🔴" "Platform Service Disruption" "Some AVORTYX platform services are currently down (${down}), and some features may be unavailable. Our team has been notified and is investigating."
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
    quiet=$(cd "$APP_DIR" && docker compose exec -T web python manage.py shell -c "
from django.utils import timezone
from routing.models import CallLog
from buyers.destination import Destination
c = CallLog.objects.order_by('-created_at').first()
print('WATCHDOG_MINS', int((timezone.now() - c.created_at).total_seconds() // 60) if c else 99999,
      Destination.objects.filter(enabled=True).count())
" 2>/dev/null | grep '^WATCHDOG_MINS')
    mins=$(echo "$quiet" | awk '{print $2}')
    # Live destinations, so "all destinations are active" is a fact, not a guess.
    live_dest=$(echo "$quiet" | awk '{print $3}')

    if [ -n "$mins" ]; then
        if [ "$mins" -gt "$QUIET_MINUTES" ]; then
            if [ "${live_dest:-0}" -gt 0 ]; then
                dest_line="All systems are operational and ${live_dest} destinations are active, which points to an upstream issue. Please confirm traffic status with the source."
            else
                dest_line="All systems are operational, but no destination is currently active. Please review destination settings."
            fi
            # Counted from the last call, so the restore message gives the real gap.
            alert traffic "🔴" "Traffic Alert" "No inbound calls have been received for $(duration "$mins") during peak hours. ${dest_line}" \
                "$(( $(date -u +%s) - mins * 60 ))"
        else
            clear_alert traffic "Traffic Restored" "Inbound calls are being received again. No calls arrived for {dur}."
        fi
    fi
fi

# ── 7. disk ──────────────────────────────────────────────────────────────────
# 45GB of unrotated Asterisk logs nearly filled this once.

used=$(df / --output=pcent | tail -1 | tr -cd '0-9')
if [ "${used:-0}" -ge 85 ]; then
    alert disk "🟠" "Storage Warning" "AVORTYX server storage is ${used}% full. Action is required: once storage is full, calls and recordings can no longer be saved. Our team has been notified."
else
    clear_alert disk "Storage Normal" "AVORTYX server storage is back to ${used}% and operating normally. The warning lasted {dur}."
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
        send "✅ <b>AVORTYX | Daily Status</b>
All systems are fully operational. Calls received today: ${calls:-0}. Server storage used: ${used}%.
<b>Checked:</b> $(stamp)"
    fi
fi

echo "$(date -u +%FT%TZ) ok routing=$code portal=$portal disk=${used}%"

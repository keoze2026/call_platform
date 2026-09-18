#!/usr/bin/env bash
#
# Reports Asterisk's live channels to the platform so rows left behind by a
# missing end-of-call webhook get closed.
#
# Runs on the HOST, not in a container — only the host can reach the Asterisk
# CLI. Install as a minute cron:
#
#   chmod +x /opt/call_platform/scripts/asterisk_channel_sync.sh
#   ( crontab -l 2>/dev/null; echo "* * * * * /opt/call_platform/scripts/asterisk_channel_sync.sh >> /var/log/asterisk_channel_sync.log 2>&1" ) | crontab -
#
# Sends nothing but a count and a list of channel ids. The server decides what
# to close, and refuses to close anything if the ids do not match what it holds.
set -uo pipefail

ENV_FILE="${ENV_FILE:-/opt/call_platform/.env}"
URL="${SYNC_URL:-http://127.0.0.1:8000/api/twilio/asterisk/active-channels/}"

if [ ! -r "$ENV_FILE" ]; then
    echo "$(date -Is) cannot read $ENV_FILE" >&2
    exit 1
fi

# Read the one value directly rather than sourcing the file — .env holds
# placeholders with characters bash will not parse.
SECRET=$(grep -E '^ASTERISK_SHARED_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'\''' | tr -d '[:space:]')

if [ -z "$SECRET" ]; then
    echo "$(date -Is) ASTERISK_SHARED_SECRET not set in $ENV_FILE" >&2
    exit 1
fi

# concise output is one channel per line, '!' separated, uniqueid last
CHANNELS=$(asterisk -rx "core show channels concise" 2>/dev/null)
ASTERISK_RC=$?

if [ $ASTERISK_RC -ne 0 ]; then
    # Asterisk unreachable. Report nothing rather than an empty list, which the
    # server would read as "no calls are up" and act on.
    echo "$(date -Is) asterisk CLI unavailable, skipping" >&2
    exit 1
fi

COUNT=$(printf '%s' "$CHANNELS" | grep -c '[^[:space:]]' || true)
COUNT=${COUNT:-0}

if [ "$COUNT" -eq 0 ]; then
    IDS="[]"
else
    IDS=$(printf '%s\n' "$CHANNELS" \
        | awk -F'!' 'NF>1 {printf "\"%s\",", $NF}' \
        | sed 's/,$//')
    IDS="[${IDS}]"
fi

RESPONSE=$(curl -s --max-time 10 -X POST "$URL" \
    -H 'Content-Type: application/json' \
    -H "X-Asterisk-Secret: ${SECRET}" \
    -d "{\"active_call_ids\": ${IDS}, \"active_count\": ${COUNT}}")

# Only log when something happened, so the cron log stays readable
case "$RESPONSE" in
    *'"closed": 0'*) : ;;
    *) echo "$(date -Is) active=${COUNT} -> ${RESPONSE}" ;;
esac

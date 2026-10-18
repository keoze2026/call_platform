#!/bin/bash
# Install as /usr/local/bin/call_ended.sh (chmod 755).
#
# Tells the platform a call has ended. Two ways to run it:
#
#   call_ended.sh <call_log_id> <answeredtime> <dialstatus> <recording> <caller> <dialedtime>
#       called by macro-call_end when a call hangs up
#
#   call_ended.sh --replay
#       resends reports that could not be delivered; run it from cron:
#       * * * * * /usr/local/bin/call_ended.sh --replay >> /var/log/call_ended_replay.log 2>&1
#
# Why it retries: the report used to be one curl with no timeout and no second
# attempt. A restart of the web container, a slow request or a dropped
# connection lost the hangup for good, and the call sat open until the sweep
# closed it as no_answer. Now a report that cannot be delivered is written to
# the spool folder and resent every minute for up to 24 hours. Nothing is lost
# while the platform is down.
#
# The shared secret is NOT kept in this file. It is read, in this order, from:
#   1. the ASTERISK_SHARED_SECRET environment variable
#   2. /etc/asterisk/call_ended.secret   (one line, the secret)
#   3. ASTERISK_SHARED_SECRET= in /opt/call_platform/.env
# A report is spooled even when no secret can be found, so fixing the secret
# later delivers everything that waited.

URL="${CALL_ENDED_URL:-http://127.0.0.1:8000/api/twilio/asterisk/call-ended/}"
SECRET_FILE="${CALL_ENDED_SECRET_FILE:-/etc/asterisk/call_ended.secret}"
ENV_FILE="${ENV_FILE:-/opt/call_platform/.env}"
SPOOL="${CALL_ENDED_SPOOL:-/var/spool/call_ended}"
LOG="${CALL_ENDED_LOG:-/tmp/call_end_debug.log}"
MAX_AGE="${CALL_ENDED_MAX_AGE:-86400}"   # give up on a report after a day
ATTEMPTS="${CALL_ENDED_ATTEMPTS:-2}"     # tries before spooling
CURL_MAX="${CALL_ENDED_CURL_MAX:-10}"    # seconds per try

log() { echo "$(date -Is) $*" >> "$LOG" 2>/dev/null; }

load_secret() {
    SECRET="${ASTERISK_SHARED_SECRET:-}"
    if [ -z "$SECRET" ] && [ -r "$SECRET_FILE" ]; then
        SECRET=$(head -1 "$SECRET_FILE" | tr -d '"'\''' | tr -d '[:space:]')
    fi
    if [ -z "$SECRET" ] && [ -r "$ENV_FILE" ]; then
        SECRET=$(grep -E '^ASTERISK_SHARED_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'\''' | tr -d '[:space:]')
    fi
    [ -n "$SECRET" ]
}

# Prints the HTTP status ("000" when nothing answered). The response body goes
# to the log so a failure can be read afterwards.
post_file() {
    local file="$1" code body
    body=$(mktemp /tmp/call_ended_body.XXXXXX) || { echo "000"; return; }
    code=$(curl -s -o "$body" -w '%{http_code}' \
        --connect-timeout 3 --max-time "$CURL_MAX" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Asterisk-Secret: ${SECRET}" \
        --data-binary "@${file}" "$URL" 2>/dev/null)
    [ -z "$code" ] && code="000"
    if [ -s "$body" ]; then
        { cat "$body"; echo; } >> "$LOG" 2>/dev/null
    fi
    rm -f "$body"
    echo "$code"
}

# 2xx delivered. 400 and 404 can never succeed (bad body, unknown call), so they
# are dropped. Everything else - 5xx, 403 (secret not set up yet), timeouts, no
# connection - is worth another try later.
is_final() {
    case "$1" in
        2??|400|404) return 0 ;;
        *) return 1 ;;
    esac
}

ensure_spool() {
    mkdir -p "$SPOOL" 2>/dev/null && [ -w "$SPOOL" ] && return 0
    SPOOL="/tmp/call_ended_spool"
    mkdir -p "$SPOOL" 2>/dev/null && [ -w "$SPOOL" ]
}

# ---------------------------------------------------------------- replay mode
if [ "${1:-}" = "--replay" ]; then
    ensure_spool || { echo "$(date -Is) no writable spool folder" >&2; exit 1; }
    # One replay at a time; a slow run must not be doubled up by the next minute.
    exec 9>"$SPOOL/.lock"
    flock -n 9 || exit 0
    load_secret || { echo "$(date -Is) replay: no shared secret found" >&2; exit 1; }
    now=$(date +%s)
    for f in "$SPOOL"/*.json; do
        [ -e "$f" ] || continue
        born=$(stat -c %Y "$f" 2>/dev/null || echo "$now")
        if [ $((now - born)) -gt "$MAX_AGE" ]; then
            mkdir -p "$SPOOL/dead" && mv "$f" "$SPOOL/dead/" 2>/dev/null
            log "replay: gave up on $(basename "$f") after $((now - born))s"
            continue
        fi
        code=$(post_file "$f")
        if is_final "$code"; then
            case "$code" in
                2??) log "replay: delivered $(basename "$f") ($code)"; rm -f "$f" ;;
                *)   mkdir -p "$SPOOL/dead" && mv "$f" "$SPOOL/dead/" 2>/dev/null
                     log "replay: dropped $(basename "$f") ($code)" ;;
            esac
        else
            log "replay: $(basename "$f") still undelivered ($code)"
        fi
    done
    exit 0
fi

# ---------------------------------------------------------------- normal mode
CALL_LOG_ID="$1"
DURATION="${2:-0}"
DIALSTATUS="$3"
RECORDING_URL="$4"
CALLER="$5"
# DIALEDTIME - ANSWEREDTIME = the outbound leg's ring, measured by the
# switch. This is the TTC the downstream platform reports for its own leg.
DIALED="${6:-0}"

if [ "$DURATION" -gt 0 ] 2>/dev/null || [ "$DIALSTATUS" = "ANSWERED" ] || [ "$DIALSTATUS" = "ANSWER" ]; then
    ANSWERED="true"
else
    ANSWERED="false"
fi

ENDED_AT=$(date +%s)

# DIALSTATUS rides along raw: BUSY / NOANSWER / CONGESTION / CHANUNAVAIL /
# CANCEL say WHY an unanswered call died. ended_at_epoch is when the call really
# ended, so a report delivered late does not stamp the delivery time on it.
PAYLOAD=$(python3 -c "
import json, sys
def num(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
a = sys.argv
print(json.dumps({
    'call_log_id': a[1],
    'duration': num(a[2]),
    'answered': a[3] == 'true',
    'recording_url': a[4],
    'caller': a[5],
    'dialed_seconds': num(a[6]),
    'dial_status': a[7],
    'ended_at_epoch': num(a[8]),
}))" "$CALL_LOG_ID" "$DURATION" "$ANSWERED" "$RECORDING_URL" "$CALLER" "$DIALED" "$DIALSTATUS" "$ENDED_AT")

if [ -z "$PAYLOAD" ]; then
    log "could not build the report for call '$CALL_LOG_ID' (python3 failed)"
    exit 1
fi

TMP=$(mktemp /tmp/call_ended.XXXXXX) || exit 1
printf '%s' "$PAYLOAD" > "$TMP"

code="000"
if load_secret; then
    n=0
    while [ "$n" -lt "$ATTEMPTS" ]; do
        n=$((n + 1))
        code=$(post_file "$TMP")
        is_final "$code" && break
        [ "$n" -lt "$ATTEMPTS" ] && sleep 1
    done
else
    log "no shared secret found (checked env, $SECRET_FILE, $ENV_FILE); spooling the report"
    code="403"
fi

if is_final "$code"; then
    case "$code" in
        2??) : ;;
        *) log "report for call '$CALL_LOG_ID' rejected with $code and dropped: $PAYLOAD" ;;
    esac
else
    if ensure_spool; then
        OUT="$SPOOL/$(date +%s)-$$-$RANDOM.json"
        if mv "$TMP" "$OUT" 2>/dev/null; then
            chmod 600 "$OUT" 2>/dev/null
            TMP=""
            log "report for call '$CALL_LOG_ID' not delivered ($code); spooled as $(basename "$OUT")"
        fi
    else
        log "report for call '$CALL_LOG_ID' not delivered ($code) and no spool folder is writable: $PAYLOAD"
    fi
fi
[ -n "$TMP" ] && rm -f "$TMP"
exit 0

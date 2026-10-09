#!/bin/bash
# Replaces /usr/local/bin/call_ended.sh.
#
# Now also sends the caller number and the channel's unique id. A transfer at
# the buyer replaces the inbound channel, the replacement carries no channel
# variables, and the h extension fires with CALL_LOG_ID empty - that is how a
# real ten-minute call was written off as no_answer. The caller number
# survives the transfer, so the backend can match the open call by caller
# when the id is missing.
CALL_LOG_ID="$1"
DURATION="${2:-0}"
DIALSTATUS="$3"
RECORDING_URL="$4"
CALLER="$5"

if [ "$DURATION" -gt 0 ] 2>/dev/null || [ "$DIALSTATUS" = "ANSWERED" ] || [ "$DIALSTATUS" = "ANSWER" ]; then
    ANSWERED="true"
else
    ANSWERED="false"
fi

PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'call_log_id': sys.argv[1], 'duration': int(sys.argv[2] or 0), 'answered': sys.argv[3]=='true', 'recording_url': sys.argv[4], 'caller': sys.argv[5]}))" "$CALL_LOG_ID" "$DURATION" "$ANSWERED" "$RECORDING_URL" "$CALLER")

curl -s -X POST -H "Content-Type: application/json" -H "X-Asterisk-Secret: 4DZDdyfcLaIoKekpZhAWnuGGLOZv_tIgBgblX2ObrjA" -d "$PAYLOAD" http://127.0.0.1:8000/api/twilio/asterisk/call-ended/ >> /tmp/call_end_debug.log 2>&1

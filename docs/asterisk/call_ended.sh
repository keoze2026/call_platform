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
# DIALEDTIME - ANSWEREDTIME = the outbound leg's ring, measured by the
# switch. This is the TTC the downstream platform reports for its own leg.
DIALED="${6:-0}"

if [ "$DURATION" -gt 0 ] 2>/dev/null || [ "$DIALSTATUS" = "ANSWERED" ] || [ "$DIALSTATUS" = "ANSWER" ]; then
    ANSWERED="true"
else
    ANSWERED="false"
fi

# DIALSTATUS rides along raw: BUSY / NOANSWER / CONGESTION / CHANUNAVAIL /
# CANCEL say WHY an unanswered call died - a destination paused at the far
# end reports as unavailable, which the backend could never see before.
PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'call_log_id': sys.argv[1], 'duration': int(sys.argv[2] or 0), 'answered': sys.argv[3]=='true', 'recording_url': sys.argv[4], 'caller': sys.argv[5], 'dialed_seconds': int(sys.argv[6] or 0), 'dial_status': sys.argv[7]}))" "$CALL_LOG_ID" "$DURATION" "$ANSWERED" "$RECORDING_URL" "$CALLER" "$DIALED" "$DIALSTATUS")

curl -s -X POST -H "Content-Type: application/json" -H "X-Asterisk-Secret: 4DZDdyfcLaIoKekpZhAWnuGGLOZv_tIgBgblX2ObrjA" -d "$PAYLOAD" http://127.0.0.1:8000/api/twilio/asterisk/call-ended/ >> /tmp/call_end_debug.log 2>&1

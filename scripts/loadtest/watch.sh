#!/bin/bash
# CH-465 load test, Server A: one line every 10 seconds.
#   calls     calls up on Asterisk (each is a caller leg + a buyer leg)
#   LT-A/LT-B calls being put through to each test buyer (limits 400 / 250)
#   held      hang-up reports waiting for Server B (calls A routed alone)
#   load/mem  server load average and memory used
while true; do
    ch=$(asterisk -rx "core show channels concise" 2>/dev/null)
    calls=$(asterisk -rx "core show channels count" 2>/dev/null | grep 'active call' | cut -d' ' -f1)
    a=$(printf '%s\n' "$ch" | grep -c '!Dial!PJSIP/19995550001@')
    b=$(printf '%s\n' "$ch" | grep -c '!Dial!PJSIP/19995550002@')
    held=$(ls /var/lib/avortyx/fallback/pending 2>/dev/null | wc -l)
    echo "$(date +%H:%M:%S) calls=${calls:-?} LT-A=$a LT-B=$b held=$held load=$(cut -d' ' -f1 /proc/loadavg) mem=$(free -m | awk '/Mem/{print $3"/"$2"MB"}')"
    sleep 10
done

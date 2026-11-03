#!/bin/bash
# CH-469 load test, SERVER A: the test calls, kept apart from live calls.
#
#   cd /opt/call_platform && bash scripts/loadtest/a_loadtest.sh <step>
#
# Run "b_loadtest.sh setup" on Server B first: this sends the test number's
# calls to the TEST routing on Server B (10.20.0.2:8011), never to live
# routing (8001). Live calls, the live dial plan, the live fallback state
# and the live hang-up spool are not changed. The test has its own:
#   routing script   /usr/share/asterisk/agi-bin/route_call_loadtest.py
#   hang-up report   /usr/local/bin/call_ended_loadtest.sh
#   fallback state   /var/lib/avortyx/fallback-loadtest
#   hang-up spool    /var/spool/call_ended_loadtest
#   recordings       /var/spool/asterisk/recordings-loadtest
#   sync services    avx-loadtest-sync, avx-loadtest-chansync (temporary)
#   logs             /root/loadtest
#
# Steps:
#   setup                    install the above, add the test dial plan
#   buyers                   start the test buyers (SIPp, answers every call)
#   callers <at-once> <total> start the test callers, e.g. "callers 50 200",
#                            then "callers 600 12500" (about 32 minutes)
#   watch                    one line every 10 s (Ctrl+C to stop watching)
#   snapshot                 save calls, CPU, memory and audio quality now
#   stop                     stop the test callers and buyers
#   cleanup                  remove everything the test added
set -uo pipefail

B=http://10.20.0.2:8011
LIB=/usr/local/lib/avortyx/fallback
STATE=/var/lib/avortyx/fallback-loadtest
SPOOL=/var/spool/call_ended_loadtest
RECDIR=/var/spool/asterisk/recordings-loadtest
AGI=/usr/share/asterisk/agi-bin/route_call_loadtest.py
ENDED=/usr/local/bin/call_ended_loadtest.sh
WORK=/root/loadtest
SIPP=/usr/local/bin/sipp
AU=asterisk
REPO=$(cd "$(dirname "$0")/../.." && pwd)
INC_EXT='#include "extensions_loadtest.conf"   ; CH-469 load test'
INC_SIP='#include "pjsip_loadtest.conf"   ; CH-469 load test'

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root"
ip -4 addr | grep -q '10.20.0.1/' || die "this is not Server A (10.20.0.1)"

b_test_up() {
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$B/api/twilio/asterisk/fallback-plan/")" = "403" ]
}

asterisk_target() {
    local bind
    bind=$(asterisk -rx "pjsip show transport transport-udp" 2>/dev/null \
           | awk -F': *' '/^ *bind /{print $2; exit}' | tr -d '[:space:]')
    [ -n "$bind" ] || return 1
    echo "${bind/0.0.0.0/127.0.0.1}"
}

case "${1:-}" in
setup)
    # sipp -v exits with code 99, so its output is read into a variable first.
    v=$([ -x "$SIPP" ] && "$SIPP" -v 2>&1 || true)
    [[ "$v" == *"SIPp v3.7.7-"*PCAP* ]] \
        || die "SIPp 3.7.7 is not installed: run scripts/loadtest/install_sipp.sh first"
    [ -f /usr/share/asterisk/agi-bin/route_call.py ] || die "route_call.py is not installed"
    grep -q CALL_ENDED_PENDING /usr/local/bin/call_ended.sh || die "call_ended.sh is older than CH-464"
    b_test_up || die "the test routing on Server B does not answer ($B): run b_loadtest.sh setup on Server B"

    echo "== Test folders"
    chmod 711 /var/lib/avortyx
    install -d -o "$AU" -g "$AU" -m 750 "$STATE" "$STATE/pending" "$SPOOL"
    install -d -o "$AU" -g "$AU" -m 755 "$RECDIR"
    [ -f "$STATE/.decide.lock" ] || install -o "$AU" -g "$AU" -m 644 /dev/null "$STATE/.decide.lock"
    mkdir -p "$WORK"

    echo "== Test routing script and hang-up report"
    install -o "$AU" -g "$AU" -m 755 "$REPO/scripts/loadtest/route_call_loadtest.py" "$AGI"
    install -m 755 "$REPO/scripts/loadtest/call_ended_loadtest.sh" "$ENDED"

    echo "== Test dial plan and SIP settings"
    install -m 644 "$REPO/scripts/loadtest/extensions_loadtest.conf" /etc/asterisk/extensions_loadtest.conf
    install -m 644 "$REPO/scripts/loadtest/pjsip_loadtest.conf" /etc/asterisk/pjsip_loadtest.conf
    grep -qF "$INC_EXT" /etc/asterisk/extensions.conf || { cp -a /etc/asterisk/extensions.conf /root/extensions.conf.before-loadtest; echo "$INC_EXT" >> /etc/asterisk/extensions.conf; }
    grep -qF "$INC_SIP" /etc/asterisk/pjsip.conf || { cp -a /etc/asterisk/pjsip.conf /root/pjsip.conf.before-loadtest; echo "$INC_SIP" >> /etc/asterisk/pjsip.conf; }
    asterisk -rx "dialplan reload" >/dev/null
    asterisk -rx "module reload res_pjsip.so" >/dev/null
    sleep 1
    asterisk -rx "dialplan show loadtest-in" | grep -q route_call_loadtest || die "test dial plan not loaded"
    asterisk -rx "pjsip show endpoint loadtest-in" | grep -q 'loadtest-in' || die "test SIP settings not loaded"

    echo "== Test caller and its audio"
    cp "$REPO/scripts/loadtest/caller.xml" "$WORK/caller.xml"
    [ -s "$WORK/tone-ulaw.pcap" ] || python3 "$REPO/scripts/loadtest/make_pcap.py" "$WORK/tone-ulaw.pcap" 90

    echo "== Test sync services"
    systemctl stop avx-loadtest-sync avx-loadtest-chansync 2>/dev/null
    systemctl reset-failed avx-loadtest-sync avx-loadtest-chansync 2>/dev/null
    systemd-run --quiet --unit=avx-loadtest-sync -p Restart=always -p RestartSec=5 \
        -p StandardOutput=append:$WORK/sync.log -p StandardError=append:$WORK/sync.log \
        --setenv=AVX_FALLBACK_DIR=$STATE --setenv=FALLBACK_B_URL=$B --setenv=CALL_ENDED_BIN=$ENDED \
        /usr/bin/python3 $LIB/fallback_sync.py --loop
    systemd-run --quiet --unit=avx-loadtest-chansync \
        -p StandardOutput=append:$WORK/chansync.log -p StandardError=append:$WORK/chansync.log \
        /bin/bash -c "while true; do SYNC_URL=$B/api/twilio/asterisk/active-channels/ $REPO/scripts/asterisk_channel_sync.sh; runuser -u $AU -- $ENDED --replay; sleep 60; done"
    sleep 8
    AVX_FALLBACK_DIR=$STATE python3 $LIB/fallback_sync.py --status
    echo "Ready. Asterisk for the test callers: $(asterisk_target || echo '?')"
    ;;
buyers)
    pkill -f "sipp -sn uas -i 127.0.0.3" 2>/dev/null; sleep 1
    "$SIPP" -sn uas -i 127.0.0.3 -p 5090 -bg -trace_err -error_file "$WORK/buyers_errors.log" >/dev/null 2>&1
    sleep 1
    pgrep -f "sipp -sn uas -i 127.0.0.3" >/dev/null && echo "test buyers answering on 127.0.0.3:5090" || die "test buyers did not start"
    ;;
callers)
    AT_ONCE=${2:-}; TOTAL=${3:-}
    [[ "$AT_ONCE" =~ ^[0-9]+$ && "$TOTAL" =~ ^[0-9]+$ ]] || die 'use: callers <at-once> <total>, e.g. callers 50 200'
    pgrep -f "sipp -sn uas -i 127.0.0.3" >/dev/null || die "start the test buyers first: a_loadtest.sh buyers"
    b_test_up || echo "NOTE: test routing on Server B is off: Server A will route the test calls alone"
    AST=${ASTERISK_SIP:-$(asterisk_target)} || die "could not read Asterisk's SIP address; set ASTERISK_SIP=ip:port"
    tag=$(date +%H%M)
    "$SIPP" "$AST" -sf "$WORK/caller.xml" -s 19995550100 -i 127.0.0.2 -p 5061 -mi 127.0.0.2 \
        -l "$AT_ONCE" -r 20 -m "$TOTAL" -d 90000 -bg \
        -trace_err -error_file "$WORK/callers_errors_$tag.log" \
        -trace_stat -stf "$WORK/callers_stats_$tag.csv" -fd 30 \
        -trace_screen -screen_file "$WORK/callers_result_$tag.log" >/dev/null 2>&1
    sleep 2
    pgrep -f "sipp .*caller.xml" >/dev/null || die "test callers did not start (see $WORK/callers_errors_$tag.log)"
    echo "$(date +%T) test callers started: $AT_ONCE at once, $TOTAL in total, to $AST"
    echo "Result at the end: $WORK/callers_result_$tag.log"
    ;;
watch)
    bash "$REPO/scripts/loadtest/watch.sh" | tee -a "$WORK/watch.log"
    ;;
snapshot)
    f="$WORK/snapshot-$(date +%H%M%S).txt"
    {
        echo "== $(date)"; asterisk -rx "core show channels count"; uptime; free -m
        echo "== top"; top -b -n1 | head -20
        echo "== audio (jitter, loss) per call"; asterisk -rx "pjsip show channelstats"
    } > "$f" 2>&1
    head -30 "$f"; echo "... saved in $f"
    ;;
stop)
    pkill -f "sipp .*caller.xml" 2>/dev/null && echo "test callers stopped"
    pkill -f "sipp -sn uas -i 127.0.0.3" 2>/dev/null && echo "test buyers stopped"
    true
    ;;
cleanup)
    pkill -f "sipp .*caller.xml" 2>/dev/null; pkill -f "sipp -sn uas -i 127.0.0.3" 2>/dev/null
    systemctl stop avx-loadtest-sync avx-loadtest-chansync 2>/dev/null
    systemctl reset-failed avx-loadtest-sync avx-loadtest-chansync 2>/dev/null
    drop_line() {   # remove exactly this line, keeping the file's owner and mode
        local tmp; tmp=$(mktemp) && grep -vxF "$1" "$2" > "$tmp"; cat "$tmp" > "$2"; rm -f "$tmp"
    }
    drop_line "$INC_EXT" /etc/asterisk/extensions.conf
    drop_line "$INC_SIP" /etc/asterisk/pjsip.conf
    rm -f /etc/asterisk/extensions_loadtest.conf /etc/asterisk/pjsip_loadtest.conf
    asterisk -rx "dialplan reload" >/dev/null
    asterisk -rx "module reload res_pjsip.so" >/dev/null
    rm -f "$AGI" "$ENDED" "$WORK/tone-ulaw.pcap"
    rm -rf "$STATE" "$SPOOL" "$RECDIR"
    grep -q 'CH-469 load test' /etc/asterisk/extensions.conf /etc/asterisk/pjsip.conf \
        && echo "WARNING: a test include line is still there" || echo "test dial plan removed"
    echo "test removed; logs kept in $WORK"
    echo "live services: $(systemctl is-active asterisk kamailio avx-fallback-sync | tr '\n' ' ')"
    ;;
*)
    sed -n '2,30p' "$0"
    exit 1
    ;;
esac

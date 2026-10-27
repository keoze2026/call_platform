#!/usr/bin/env bash
# CH-301: switch the cancel hold on the live server, step by step.
#
#   bash docs/kamailio/cancel_hold.sh install            # relay + carrier-out + dialplan switch (OFF for all calls)
#   bash docs/kamailio/cancel_hold.sh test-caller 1XXXXXXXXXX   # ON for one caller number only
#   bash docs/kamailio/cancel_hold.sh on                 # ON for all calls
#   bash docs/kamailio/cancel_hold.sh off                # OFF for all calls (instant, no reload)
#   bash docs/kamailio/cancel_hold.sh status
#   bash docs/kamailio/cancel_hold.sh log                # the relay's hold lines
#   bash docs/kamailio/cancel_hold.sh hold 3000          # set the hold (ms); restarts the relay, only with 0 calls live
#   bash docs/kamailio/cancel_hold.sh check-backend      # the held-answer endpoint answers (after web restart)
#   bash docs/kamailio/cancel_hold.sh uninstall          # put pjsip.conf / extensions.conf back, stop relay
#
# "install" changes nothing for live calls: the dialplan uses the relay only
# when avortyx/cancel_hold (all) or avortyx/cancel_hold_caller (one number)
# is set. Reloads used: pjsip and dialplan reloads, which do not drop calls.
set -u
APP=/opt/call_platform
CFG=$APP/docs/kamailio/kamailio.cfg
PJ=/etc/asterisk/pjsip.conf
EXT=/etc/asterisk/extensions.conf
XOLO_IPS="45.79.4.41 100.53.112.140 216.152.152.100"   # [carrier] identify match
STAMP=$(date +%F-%H%M%S)
ast() { asterisk -rx "$1"; }

install() {
    echo "== 1) relay config (shared secret from .env, never printed)"
    python3 - "$CFG" "$APP/.env" <<'PY' || exit 1
import sys
cfg, env = open(sys.argv[1]).read(), open(sys.argv[2]).read().splitlines()
secret = next((l.split('=', 1)[1].strip().strip("'\"") for l in env if l.startswith('ASTERISK_SHARED_SECRET=')), '')
if not secret:
    sys.exit('ASTERISK_SHARED_SECRET not found in .env')
open('/etc/kamailio/kamailio.cfg', 'w').write(cfg.replace('__ASTERISK_SHARED_SECRET__', secret))
PY
    chown root:kamailio /etc/kamailio/kamailio.cfg 2>/dev/null; chmod 640 /etc/kamailio/kamailio.cfg
    kamailio -c -f /etc/kamailio/kamailio.cfg 2>&1 | tail -2

    echo "== 2) firewall: 5080/udp from the carrier IPs only"
    for ip in $XOLO_IPS; do ufw allow from "$ip" to any port 5080 proto udp comment 'cancel-hold relay' >/dev/null; done
    ufw status | grep 5080

    echo "== 3) start the relay"
    D=/etc/default/kamailio; touch "$D"
    grep -q '^CFGFILE=' "$D" || echo 'CFGFILE=/etc/kamailio/kamailio.cfg' >> "$D"
    grep -q '^SHM_MEMORY=' "$D" || echo 'SHM_MEMORY=128' >> "$D"
    grep -q '^PKG_MEMORY=' "$D" || echo 'PKG_MEMORY=16' >> "$D"
    grep -q '^RUN_KAMAILIO=' "$D" || echo 'RUN_KAMAILIO=yes' >> "$D"
    systemctl unmask kamailio >/dev/null 2>&1
    systemctl enable --now kamailio
    sleep 2
    ss -lunp | grep -E ":(5070|5080) " || { echo "relay not listening - stopping here"; exit 1; }

    echo "== 4) Asterisk endpoint carrier-out (copy of carrier, through the relay)"
    if ! grep -q '^\[carrier-out\]' "$PJ"; then
        cp -a "$PJ" "$PJ.bak-ch301-$STAMP"
        cat >> "$PJ" <<'PJS'

; CH-301 cancel hold: outbound calls through the relay (docs/kamailio)
[carrier-out]
type=endpoint
context=from-carrier
disallow=all
allow=ulaw
allow=alaw
direct_media=no
trust_id_inbound=yes
aors=carrier
outbound_proxy=sip:127.0.0.1:5070\;lr
PJS
        ast "module reload res_pjsip.so" >/dev/null
    fi
    ast "pjsip show endpoint carrier-out" | grep -E "Endpoint:|outbound_proxy" | head -3

    echo "== 5) dialplan switch (off by default)"
    if ! grep -q 'OUT_EP' "$EXT"; then
        cp -a "$EXT" "$EXT.bak-ch301-$STAMP"
        python3 - "$EXT" <<'PY' || exit 1
import sys
p = sys.argv[1]; s = open(p).read()
old = 'same => n,Dial(PJSIP/${DIAL_NUM}@carrier,${MAX_DURATION},gr)'
if s.count(old) != 1:
    sys.exit('Dial line not found exactly once - extensions.conf left unchanged')
line = next(l for l in s.splitlines() if old in l)
indent = line[:len(line) - len(line.lstrip())]
new = '\n'.join(indent + x for x in [
    'same => n,Set(OUT_EP=carrier)',
    'same => n,ExecIf($["${DB(avortyx/cancel_hold)}" = "1"]?Set(OUT_EP=carrier-out))',
    'same => n,ExecIf($["${DB(avortyx/cancel_hold_caller)}" = "${CALLERID(num):-11}"]?Set(OUT_EP=carrier-out))',
    'same => n,Dial(PJSIP/${DIAL_NUM}@${OUT_EP},${MAX_DURATION},grb(avx-out-hdr^s^1(${CALL_LOG_ID})))',
])
s = s.replace(line, new)
s += '\n\n; CH-301: our call id on the outbound INVITE, for the cancel-hold relay\n[avx-out-hdr]\nexten => s,1,Set(PJSIP_HEADER(add,X-Avortyx-Call)=${ARG1})\n same => n,Return()\n'
open(p, 'w').write(s)
PY
        ast "dialplan reload" >/dev/null
    fi
    ast "dialplan show from-carrier" | grep -E "OUT_EP|Dial\(" 
    echo "== installed. Still OFF for every call. Next: test-caller <number>"
}

case "${1:-}" in
    install) install ;;
    test-caller)
        n=$(printf '%s' "${2:-}" | tr -cd '0-9' | tail -c 11)
        [ ${#n} -eq 11 ] || { echo "give the test phone as 11 digits, e.g. 1XXXXXXXXXX"; exit 1; }
        ast "database put avortyx cancel_hold_caller $n"; echo "ON for caller $n only" ;;
    on)  ast "database put avortyx cancel_hold 1"; echo "ON for all calls" ;;
    off) ast "database del avortyx cancel_hold"; ast "database del avortyx cancel_hold_caller"; echo "OFF for all calls" ;;
    status)
        ast "database show avortyx"; systemctl is-active kamailio; ss -lunp | grep -E ":(5070|5080) "
        grep -E '^#!substdef "!HOLD_MS' /etc/kamailio/kamailio.cfg | sed -E 's/.*HOLD_MS!([0-9]+)!.*/hold: \1 ms/'
        ast "pjsip show endpoint carrier-out" | grep -E "Endpoint:" | head -1 ;;
    check-backend)
        S=$(grep '^ASTERISK_SHARED_SECRET=' "$APP/.env" | cut -d= -f2- | tr -d "'\"")
        printf 'header = "X-Asterisk-Secret: %s"\nheader = "Content-Type: application/json"\n' "$S" \
          | curl -s -K - -X POST -d '{"call_log_id": "00000000-0000-0000-0000-000000000000"}' \
            -w '  HTTP %{http_code}\n' http://127.0.0.1:8000/api/twilio/asterisk/held-answer/
        echo '(expected: {"error": "Call not found"}  HTTP 404 - the endpoint is live)' ;;
    hold)
        # CH-315: the hold time. Measured 27 Oct on the 33 calls Revo had to
        # correct (26-27 Oct): every buyer answer came 0 to 2 s after the
        # caller hung up (Revo counts whole seconds); a 1000 ms hold misses
        # every answer that comes later than 1 s.
        ms=$(printf '%s' "${2:-}" | tr -cd '0-9')
        { [ -n "$ms" ] && [ "$ms" -ge 500 ] && [ "$ms" -le 5000 ]; } || { echo "give the hold in ms, 500 to 5000 (e.g. 3000)"; exit 1; }
        live=$(ast "core show channels count" | awk '/active call/{print $1}')
        if [ "${live:-1}" != "0" ] && [ "${3:-}" != "--now" ]; then
            echo "$live call(s) live: the relay restarts to load the new time. Run again when 0 calls are live."; exit 1
        fi
        C=/etc/kamailio/kamailio.cfg
        cp -a "$C" "$C.bak-hold-$STAMP"
        sed -i -E "s/^#!substdef \"!HOLD_MS![0-9]+!g\"/#!substdef \"!HOLD_MS!$ms!g\"/; s/^#!substdef \"!HOLD_US![0-9]+!g\"/#!substdef \"!HOLD_US!${ms}000!g\"/; s/^children=([0-9]|[12][0-9]|3[01])\b.*/children=32/" "$C"
        grep -E '^#!substdef "!HOLD_(MS|US)|^children=' "$C"
        if ! kamailio -c -f "$C" >/dev/null 2>&1; then
            cp -a "$C.bak-hold-$STAMP" "$C"; echo "config check failed: old config kept, relay not touched"; exit 1
        fi
        systemctl restart kamailio && sleep 2
        ss -lunp | grep -qE ":(5070|5080) " && echo "relay running with a ${ms} ms hold" || {
            cp -a "$C.bak-hold-$STAMP" "$C"; systemctl restart kamailio
            echo "relay did not come back: old config restored"; exit 1; } ;;
    log) journalctl -u kamailio --since "-60 min" --no-pager | grep "hold:" | sed 's/^.*hold:/hold:/' | tail -40 ;;
    uninstall)
        ast "database del avortyx cancel_hold"; ast "database del avortyx cancel_hold_caller"
        e=$(ls -t "$EXT".bak-ch301-* 2>/dev/null | tail -1); p=$(ls -t "$PJ".bak-ch301-* 2>/dev/null | tail -1)
        [ -n "$e" ] && cp -a "$e" "$EXT" && ast "dialplan reload" >/dev/null && echo "extensions.conf restored from $e"
        [ -n "$p" ] && cp -a "$p" "$PJ" && ast "module reload res_pjsip.so" >/dev/null && echo "pjsip.conf restored from $p"
        systemctl disable --now kamailio; echo "relay stopped" ;;
    *) sed -n '2,15p' "$0" ;;
esac

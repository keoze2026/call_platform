#!/usr/bin/env bash
# CH-297: lab test of the cancel-hold relay. Everything on 127.0.0.1 - no
# real call, no XOLO, Asterisk untouched. Needs: kamailio, sipp (see
# docs/kamailio/LAB-PLAN.md). Prints PASS/FAIL per scenario.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
L=/root/hold-lab; rm -rf "$L"; mkdir -p "$L"; cd "$L" || exit 1
command -v kamailio >/dev/null || { echo "kamailio not installed"; exit 1; }
command -v sipp >/dev/null || { echo "sipp not installed"; exit 1; }
sed -e 's|!PUBLIC_IP!156.67.25.167!|!PUBLIC_IP!127.0.0.1!|' \
    -e 's|!HELD_URL![^!]*!|!HELD_URL!http://127.0.0.1:8099/held!|' \
    "$HERE/../kamailio.cfg" > kamailio-lab.cfg
echo "== config check"
kamailio -c -f kamailio-lab.cfg 2>&1 | tail -3 || exit 1
python3 -m http.server 8099 --bind 127.0.0.1 > http.log 2>&1 &
HP=$!
kamailio -f kamailio-lab.cfg -DD -E -w "$L" > kamailio.log 2>&1 &
KP=$!
sleep 2
kill -0 $KP 2>/dev/null || { echo "kamailio did not start:"; tail -20 kamailio.log; kill $HP; exit 1; }

run() {  # $1 scenario letter
    sipp -sf "$HERE/uas_$1.xml" -i 127.0.0.1 -p 5062 -m 1 -timeout 15s -timeout_error \
         -trace_msg -message_file "uas_$1.msg" -trace_err -error_file "uas_$1.err" > "uas_$1.out" 2>&1 &
    local SP=$!
    sleep 0.5
    sipp 127.0.0.1:5062 -sf "$HERE/uac_$1.xml" -i 127.0.0.1 -p 5061 -rsa 127.0.0.1:5070 -m 1 \
         -timeout 15s -timeout_error -trace_msg -message_file "uac_$1.msg" \
         -trace_err -error_file "uac_$1.err" > "uac_$1.out" 2>&1
    local UC=$?
    wait $SP; local SC=$?
    if [ $UC -eq 0 ] && [ $SC -eq 0 ]; then echo "PASS $1"; else echo "FAIL $1 (caller exit $UC, buyer exit $SC - see $L/uac_$1.err $L/uas_$1.err)"; fi
}

echo "== a) normal answered call passes through"; run a
echo "== b) caller cancels, buyer never answers"; run b
echo "== c) caller cancels, buyer answers during the hold"; run c
sleep 1
echo "== relay log (hold lines)"
grep "hold:" kamailio.log | sed 's/^.*hold:/hold:/'
echo "== CANCELs actually sent to the buyer (should be 1, from b only):"
grep -c "CANCEL sent" kamailio.log
echo "== held-answer report to the backend (should be 1 POST, from c):"
grep -c "POST /held" http.log
kill $KP $HP 2>/dev/null; sleep 1; pkill -f "kamailio -f $L/kamailio-lab.cfg" 2>/dev/null
echo "lab files: $L"

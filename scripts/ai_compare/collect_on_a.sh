#!/usr/bin/env bash
# CH-549: hand 200 recent calls' caller / agent recordings to Server B for the
# speech-to-text test (Sir, 4 Nov: "run Deepgram (Nova-3) and AssemblyAI
# (Universal-Streaming) on the same 200 of our recordings, both channels").
#
# Run on Server A as root:   bash collect_on_a.sh [count]
#
# What it does - and nothing else:
#   1. picks the newest <count> (default 200) calls that have BOTH split files
#      (recordings/split/<id>-caller.wav and <id>-agent.wav), finished at least
#      2 minutes ago, agent side at least 3 s long;
#   2. packs copies of them into /tmp/ai-compare/split.tar (at the lowest CPU
#      and disk priority, so calls never wait);
#   3. offers that one file to Server B over the private link only
#      (10.20.0.1:8099, firewall opened for 10.20.0.2 only) for 30 minutes,
#      then closes the port again and deletes the copy.
# Asterisk, the recordings and the dialplan are not touched.
set -euo pipefail
COUNT="${1:-200}"
SPLIT=/var/spool/asterisk/recordings/split
WORK=/tmp/ai-compare
PORT=8099
A_IP=10.20.0.1
B_IP=10.20.0.2
MIN_AGENT_BYTES=48000          # 3 s of 8 kHz 16-bit audio

[ -d "$SPLIT" ] || { echo "no $SPLIT - split recording is not on (CH-543)"; exit 1; }
rm -rf "$WORK"; mkdir -p "$WORK"

# newest first; both sides present; finished (not written to) for 2+ minutes
find "$SPLIT" -maxdepth 1 -name '*-agent.wav' -mmin +2 -size +"${MIN_AGENT_BYTES}c" -printf '%T@ %f\n' \
  | sort -rn | awk '{print $2}' | while read -r agent; do
      id="${agent%-agent.wav}"
      [ -s "$SPLIT/$id-caller.wav" ] && echo "$id"
    done | head -n "$COUNT" > "$WORK/ids.txt" || true     # head stops the list early: not an error

N=$(wc -l < "$WORK/ids.txt")
[ "$N" -gt 0 ] || { echo "no calls with both split files found"; exit 1; }
echo "calls picked: $N (newest first)"

sed 's/$/-caller.wav/' "$WORK/ids.txt" > "$WORK/files.txt"
sed 's/$/-agent.wav/' "$WORK/ids.txt" >> "$WORK/files.txt"
nice -n 19 ionice -c3 tar -C "$SPLIT" -cf "$WORK/split.tar" -T "$WORK/files.txt"
rm -f "$WORK/files.txt"
ls -lh "$WORK/split.tar" | awk '{print "packed:", $5}'
sha256sum "$WORK/split.tar" | awk '{print $1}' > "$WORK/split.tar.sha256"
echo "checksum: $(cat "$WORK/split.tar.sha256")"

OPENED=0
if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
  ufw allow in on wg0 from "$B_IP" to any port "$PORT" proto tcp >/dev/null
  OPENED=1
  echo "firewall: port $PORT open for $B_IP on the private link only"
fi
cleanup() {
  [ "$OPENED" = 1 ] && ufw delete allow in on wg0 from "$B_IP" to any port "$PORT" proto tcp >/dev/null && echo "firewall: port $PORT closed again"
  rm -rf "$WORK" && echo "copy deleted"
}
trap cleanup EXIT

echo
echo "Now on Server B run:   bash /opt/call_platform/scripts/ai_compare/fetch_on_b.sh"
echo "Waiting up to 30 minutes (Ctrl+C here once Server B says 'unpacked')..."
cd "$WORK"
timeout 1800 nice -n 19 python3 -m http.server "$PORT" --bind "$A_IP" >/dev/null 2>&1 || true

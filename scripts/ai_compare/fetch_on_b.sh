#!/usr/bin/env bash
# CH-549: Server B side of collect_on_a.sh - fetch the 200 calls' recordings
# over the private link and unpack them into /opt/ai-compare/split.
# Run on Server B as root:   bash fetch_on_b.sh
set -euo pipefail
DEST=/opt/ai-compare
URL=http://10.20.0.1:8099
mkdir -p "$DEST/split"
curl -fsS -o "$DEST/split.tar" "$URL/split.tar"
want=$(curl -fsS "$URL/split.tar.sha256")
have=$(sha256sum "$DEST/split.tar" | awk '{print $1}')
[ "$want" = "$have" ] || { echo "checksum does not match - try again"; rm -f "$DEST/split.tar"; exit 1; }
tar -C "$DEST/split" -xf "$DEST/split.tar"
rm -f "$DEST/split.tar"
chmod 700 "$DEST"
echo "unpacked: $(ls "$DEST/split" | grep -c -- '-caller.wav') calls in $DEST/split"
echo "Server A can now be stopped with Ctrl+C."

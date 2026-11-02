#!/bin/bash
# CH-468 load test: install SIPp 3.7.7 on Server A as /usr/local/bin/sipp.
#
#   sudo bash scripts/loadtest/install_sipp.sh
#
# Ubuntu's SIPp (3.7.2, package sip-tester) has a bug when it plays audio
# (play_pcap_audio) on many calls. On every call it reads the audio file
# again, never frees the previous copy, and frees a record that the calls
# already playing still use. Memory grows by the size of the audio file on
# every call (about 1 MB for 90 s), and the test callers freeze or crash
# ("Segmentation fault", "buffer overflow detected"). SIPp fixed this in
# 3.7.3 ("Fix crash when using PCAP play with more than one call"), which
# also restored the -mp option. 3.7.7 includes that fix and later
# memory-leak fixes.
#
# Measured with the same test caller, 600 calls at once, 3,000 calls:
#   3.7.2  memory 0.8 GB -> 2.0 GB, growing with every call
#   3.7.7  memory 24 MB, flat
#
# Only the test program changes. Asterisk, Kamailio and the routing are not
# touched, and Ubuntu's sipp stays at /usr/bin/sipp. The build runs at the
# lowest CPU priority.
set -euo pipefail

VERSION=v3.7.7
DEST=/usr/local/bin/sipp

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }

# sipp -v exits with code 99, so its output is read into a variable first.
sipp_version() { "$DEST" -v 2>&1 | grep -o 'SIPp v[^ ]*' || true; }

if [ -x "$DEST" ] && [[ "$(sipp_version)" == "SIPp ${VERSION}-"* ]]; then
    echo "SIPp ${VERSION} is already installed: $DEST"
    exit 0
fi

echo "== Build tools (does not touch Asterisk or Kamailio)"
apt-get update -qq && apt-get install -y --no-install-recommends git cmake make g++ pkg-config libpcap-dev libncurses-dev

SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT

echo "== SIPp ${VERSION} source"
git -c advice.detachedHead=false clone --quiet --depth 1 -b "$VERSION" https://github.com/SIPp/sipp.git "$SRC"

echo "== Build"
cmake -S "$SRC" -B "$SRC/build" -DUSE_PCAP=1 -DUSE_GSL=0 -DUSE_SSL=0 -DUSE_SCTP=0 \
      -DCMAKE_BUILD_TYPE=Release > "$SRC/cmake.log"
nice -n 19 ionice -c3 make -C "$SRC/build" -j2 sipp > "$SRC/make.log" 2>&1 \
    || { tail -20 "$SRC/make.log"; exit 1; }

install -m 755 "$SRC/build/sipp" "$DEST"
hash -r 2>/dev/null || true

echo "== Installed"
INSTALLED=$(sipp_version)
echo "$INSTALLED"
[[ "$INSTALLED" == "SIPp ${VERSION}-"*PCAP* ]] || { echo "ERROR: not the expected SIPp ${VERSION} with audio (PCAP)"; exit 1; }
echo "Use $DEST for the load test (Ubuntu's /usr/bin/sipp is left as it is)."

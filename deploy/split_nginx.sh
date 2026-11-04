#!/usr/bin/env bash
#
# CH-553: Server A - let Server B fetch the caller / agent recordings apart.
#
# Since CH-543 Asterisk writes recordings/split/<id>-caller.wav and
# <id>-agent.wav, and the AI audit on Server B fetches them from
# https://rec.v0l1.com/recordings/split/... - but rec.v0l1.com (CH-305,
# scripts/b2_nginx.sh) only knew /recordings/<id>.mp3|wav: anything with a
# second "/" fell to "location / { return 404; }". Every split file was a
# 404 (4 Nov), so every call was checked as one mixed recording.
#
# This adds ONE location to the rec.v0l1.com site, open to Server B only:
#
#   location ~ ^/recordings/split/<id>-(caller|agent).wav  -> the file, or 404
#   allowed: Server B (13.140.236.153 and its IPv6 /64), this server; others 403
#
#   bash deploy/split_nginx.sh install    # back up, add, nginx -t, reload, test; undo on failure
#   bash deploy/split_nginx.sh test       # the tests only, changes nothing
#   bash deploy/split_nginx.sh rollback   # put the backup back
#
# nginx is reloaded, not restarted: open downloads finish; calls never go
# through nginx. Run as root on Server A.
set -uo pipefail

DOMAIN=rec.v0l1.com
LINK=/etc/nginx/sites-enabled/$DOMAIN
BACKUPS=/root/nginx-backup
SPLIT=/var/spool/asterisk/recordings/split
B_V4="${B_V4:-13.140.236.153}"
B_V6="${B_V6:-2605:a144:2371:9898::/64}"
MARK="# split-recordings (CH-553)"

SITE=$(readlink -f "$LINK")
[ -f "$SITE" ] || { echo "$LINK not found" >&2; exit 1; }

code() { curl -s -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "$@"; }

run_tests() {
    local ok=1 c name
    name=$(find "$SPLIT" -maxdepth 1 -name '*-caller.wav' -mmin +2 -printf '%f\n' 2>/dev/null | head -1)
    if [ -n "$name" ]; then
        c=$(code "https://$DOMAIN/recordings/split/$name");      echo "  split file (from this server)  -> $c (200 expected)"; [ "$c" = 200 ] || ok=0
    else
        echo "  no split file on disk to test with"; ok=0
    fi
    c=$(code "https://$DOMAIN/recordings/split/00000000-0000-0000-0000-000000000000-caller.wav")
    echo "  no such split file             -> $c (404 expected)"; [ "$c" = 404 ] || ok=0
    c=$(code "https://$DOMAIN/recordings/split/");                 echo "  split folder listing           -> $c (404 expected)"; [ "$c" = 404 ] || ok=0
    name=$(find /var/spool/asterisk/recordings -maxdepth 1 -name '*.mp3' -mmin +60 -printf '%f\n' | head -1)
    if [ -n "$name" ]; then
        c=$(code "https://$DOMAIN/recordings/$name");            echo "  normal recording (unchanged)   -> $c (200 expected)"; [ "$c" = 200 ] || ok=0
    fi
    [ $ok = 1 ]
}

restore() {
    local last
    last=$(ls -1t "$BACKUPS"/$DOMAIN.split.* 2>/dev/null | head -1)
    [ -n "$last" ] || { echo "no backup in $BACKUPS" >&2; return 1; }
    cp -a "$last" "$SITE" && nginx -t 2>&1 | tail -1 && systemctl reload nginx
    echo "restored $last"
}

case "${1:-}" in
test)
    run_tests && echo "ALL OK" || { echo "SOME TESTS FAILED"; exit 1; }
    ;;
rollback)
    restore
    ;;
install)
    if grep -qF "$MARK" "$SITE"; then
        echo "already added - testing:"
        run_tests && echo "ALL OK" || { echo "SOME TESTS FAILED"; exit 1; }
        exit 0
    fi
    grep -q 'location @b2 {' "$SITE" || { echo "rec.v0l1.com site not as expected (no 'location @b2'); nothing changed"; exit 1; }
    mkdir -p "$BACKUPS"
    cp -a "$SITE" "$BACKUPS/$DOMAIN.split.$(date +%Y%m%d%H%M%S)" || exit 1
    python3 - "$SITE" "$MARK" "$B_V4" "$B_V6" <<'PY' || { echo "edit failed; nothing changed"; exit 1; }
import sys
path, mark, v4, v6 = sys.argv[1:5]
s = open(path).read()
block = f"""    {mark}: caller and agent apart, for the AI audit on Server B only.
    location ~ ^/recordings/split/([A-Za-z0-9_.-]+)-(caller|agent)\\.wav$ {{
        allow {v4};
        allow {v6};
        allow 127.0.0.1;
        deny all;
        root /var/spool/asterisk;
        try_files /recordings/split/$1-$2.wav =404;
        types {{ audio/wav wav; }}
        default_type audio/wav;
        add_header Cache-Control "no-store" always;
    }}

"""
i = s.index('    location @b2 {')
open(path, 'w').write(s[:i] + block + s[i:])
PY
    if ! nginx -t 2>/tmp/split_nginx.err; then
        cat /tmp/split_nginx.err; echo "nginx -t failed - putting the backup back"; restore; exit 1
    fi
    systemctl reload nginx
    sleep 1
    echo "added; testing:"
    if run_tests; then
        echo "ALL OK"
    else
        echo "TESTS FAILED - putting the backup back"; restore; exit 1
    fi
    ;;
*)
    echo "usage: bash deploy/split_nginx.sh install | test | rollback"; exit 1
    ;;
esac

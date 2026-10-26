#!/usr/bin/env bash
#
# CH-305: rec.v0l1.com plays a recording from Backblaze B2 when it is no
# longer on the server's disk, so every link ever sent out keeps playing after
# old local copies are removed (scripts/recordings_to_b2.py --delete-local-days).
#
#   scripts/b2_nginx.sh install    # back up, write, check, reload, test; undo on any failure
#   scripts/b2_nginx.sh test       # the same tests, changes nothing
#   scripts/b2_nginx.sh rollback   # put the last backup back
#
# A recording on disk is served from disk exactly as before. Only a missing
# one is fetched from the bucket, with a read-only token for recordings/ that
# recordings_to_b2.py --nginx renews daily (/etc/nginx/avortyx-b2.conf).
# nginx is reloaded, not restarted: open downloads finish, calls are not
# involved (Asterisk does not go through nginx).
set -uo pipefail

APP="${APP_DIR:-/opt/call_platform}"
DOMAIN=rec.v0l1.com
LINK=/etc/nginx/sites-enabled/$DOMAIN
SNIPPET=/etc/nginx/avortyx-b2.conf
BACKUPS=/root/nginx-backup        # outside sites-enabled: nginx must not load the copies
DIR=/var/spool/asterisk/recordings
CANARY_TEXT="AVORTYX B2 FALLBACK TEST - CH-305"

SITE=$(readlink -f "$LINK")
[ -f "$SITE" ] || { echo "$LINK not found" >&2; exit 1; }

code() { curl -s -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "$@"; }

run_tests() {
    local ok=1 c name
    name=$(find "$DIR" -maxdepth 1 -name '*.mp3' -mmin +60 -printf '%f\n' | head -1)
    name=${name%.mp3}
    if [ -n "$name" ]; then
        c=$(code "https://$DOMAIN/recordings/$name.mp3");  echo "  recording on disk (.mp3)    -> $c"; [ "$c" = 200 ] || ok=0
        c=$(code "https://$DOMAIN/recordings/$name.wav");  echo "  same, old .wav link         -> $c"; [ "$c" = 200 ] || ok=0
    fi
    c=$(curl -s --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/recordings/b2-canary.mp3")
    if [ "$c" = "$CANARY_TEXT" ]; then echo "  only in B2 (test file)      -> 200, content matches"; else echo "  only in B2 (test file)      -> FAILED"; ok=0; fi
    c=$(code -H 'Range: bytes=0-9' "https://$DOMAIN/recordings/b2-canary.mp3"); echo "  B2 seek (range request)     -> $c"; [ "$c" = 206 ] || ok=0
    c=$(curl -s -I --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/recordings/b2-canary.mp3" | grep -ci '^access-control-allow-origin'); echo "  B2 CORS header (portal)     -> $c"; [ "$c" = 1 ] || ok=0
    c=$(code "https://$DOMAIN/recordings/00000000-0000-0000-0000-000000000000.mp3"); echo "  no such recording           -> $c (404 expected)"; [ "$c" = 404 ] || ok=0
    c=$(code "https://$DOMAIN/recordings/"); echo "  folder listing              -> $c (404 expected)"; [ "$c" = 404 ] || ok=0
    [ $ok = 1 ]
}

restore() {
    local last
    last=$(ls -1t "$BACKUPS"/$DOMAIN.* 2>/dev/null | head -1)
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
    mkdir -p "$BACKUPS"
    cp -a "$SITE" "$BACKUPS/$DOMAIN.$(date +%Y%m%d%H%M%S)" || exit 1
    python3 "$APP/scripts/recordings_to_b2.py" --nginx --no-reload || { echo "B2 settings not written; nothing changed"; exit 1; }
    cat > "$SITE" <<'CONF'
# Call recordings on the masked domain (CH-305; written by scripts/b2_nginx.sh).
#
# A recording on the server's disk is served from disk. One that is not -
# removed after it was copied to Backblaze B2 (scripts/recordings_to_b2.py) -
# is fetched from the bucket, so every link ever sent out keeps playing.
# Old links end in .wav; after MP3 conversion (CH-281) only <id>.mp3 exists.

server {
    server_name rec.v0l1.com;

    location ~ ^/recordings/([A-Za-z0-9_.-]+)\.(wav|mp3)$ {
        root /var/spool/asterisk;
        try_files /recordings/$1.$2 /recordings/$1.mp3 @b2;
        # The portal plays these from app.avortyx.com: CORS for duration and seek.
        add_header Access-Control-Allow-Origin "*" always;
        add_header Cache-Control "private, max-age=3600";
        types { audio/wav wav; audio/mpeg mp3; }
        default_type audio/mpeg;
    }

    location @b2 {
        # Bucket path, host and the read-only token (renewed daily).
        include /etc/nginx/avortyx-b2.conf;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_ssl_server_name on;
        proxy_intercept_errors on;
        proxy_hide_header Cache-Control;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header x-bz-file-name;
        proxy_hide_header x-bz-file-id;
        proxy_hide_header x-bz-content-sha1;
        proxy_hide_header x-bz-upload-timestamp;
        proxy_hide_header x-bz-info-src_last_modified_millis;
        add_header Access-Control-Allow-Origin "*" always;
        add_header Cache-Control "private, max-age=3600";
    }

    # Nothing else exists here: no listing, no other files.
    location / {
        return 404;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/rec.v0l1.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/rec.v0l1.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot
}

server {
    if ($host = rec.v0l1.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot

    listen 80;
    server_name rec.v0l1.com;
    return 404; # managed by Certbot
}
CONF
    if ! nginx -t 2>/tmp/b2_nginx_test.log; then
        cat /tmp/b2_nginx_test.log
        echo "nginx check failed: putting the old settings back"; restore; exit 1
    fi
    systemctl reload nginx
    sleep 2
    echo "Tests:"
    if run_tests; then
        echo "INSTALLED: links fall back to B2. Nothing has been deleted yet."
    else
        echo "A test failed: putting the old settings back"; restore; exit 1
    fi
    ;;
*)
    echo "usage: $0 install | test | rollback" >&2; exit 2
    ;;
esac

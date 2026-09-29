#!/usr/bin/env bash
# Apply the rate-limit changes to the live Nginx config.
#
# Two changes requested by the frontend:
#
#   a throttled request now reaches the browser as a clean 429 with CORS headers,
#   instead of a misleading CORS error. Nginx rejects the request before Django
#   runs, so nothing adds Access-Control-Allow-Origin and the browser reports the
#   missing header rather than the rate limit.
#
#   the per-address connection limit goes from 20 to 50. Twenty is a whole office
#   rather than a whole person - colleagues behind one address share it, and a
#   browser opens several connections per tab.
#
# Backs up first, tests before reloading, and reverts if the test fails.
set -uo pipefail

ZONES=/etc/nginx/conf.d/ratelimit-zones.conf
SITE=/etc/nginx/sites-enabled/callplatform
STAMP=$(date +%F-%H%M%S)

[ -f "$SITE" ] || { echo "No $SITE - nothing to change."; exit 1; }
cp "$SITE" "/root/callplatform.nginx.backup-$STAMP"
[ -f "$ZONES" ] && cp "$ZONES" "/root/ratelimit-zones.backup-$STAMP"
echo "1/4  backed up to /root/*.backup-$STAMP"

# The origin map lives beside the zones, in http{}.
cat > "$ZONES" <<'EOF'
limit_req_zone  $binary_remote_addr  zone=api_general:10m  rate=30r/s;
limit_req_zone  $binary_remote_addr  zone=api_login:10m    rate=10r/m;
limit_conn_zone $binary_remote_addr  zone=api_conn:10m;

limit_req_status  429;
limit_conn_status 429;

# Echoed back on the 429 response only, so a throttled request arrives at the
# browser as a rate limit rather than as a CORS failure.
map $http_origin $cors_origin {
    default                     "";
    "https://www.avortyx.com"   $http_origin;
    "https://avortyx.com"       $http_origin;
    "https://avortyx.io"        $http_origin;
    "http://localhost:3000"     $http_origin;
    "http://localhost:3001"     $http_origin;
}
EOF
echo "2/4  zones and origin map written"

python3 - "$SITE" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()

# 20 concurrent is a whole office, not a whole person.
s = s.replace('limit_conn api_conn 20;', 'limit_conn api_conn 50;')

# The 429 handler, added once, just inside the server block that proxies the API.
if '@rate_limited' not in s:
    handler = '''
    # Adding the CORS headers only here: on every response they would duplicate
    # Django's own, and a browser rejects a response carrying two.
    error_page 429 = @rate_limited;
    location @rate_limited {
        default_type application/json;
        add_header Access-Control-Allow-Origin      $cors_origin always;
        add_header Access-Control-Allow-Credentials true         always;
        add_header Access-Control-Allow-Headers     "Authorization,Content-Type,X-Request-ID" always;
        add_header Retry-After                      1            always;
        return 429 '{"detail":"Too many requests. Please retry shortly.","code":"rate_limited"}';
    }
'''
    anchor = '\n    location ^~ /api/twilio/asterisk/ {'
    if anchor in s:
        s = s.replace(anchor, handler + anchor, 1)
    else:
        print('   could not find the Asterisk location to anchor to - stopping')
        raise SystemExit(1)

open(p, 'w').write(s)
print('3/4  connection limit raised to 50, 429 handler added')
PY

if nginx -t 2>&1 | grep -q successful; then
    systemctl reload nginx
    echo "4/4  nginx reloaded"
    echo
    grep -n "limit_conn api_conn\|@rate_limited" "$SITE"
else
    echo "nginx test FAILED - reverting"
    cp "/root/callplatform.nginx.backup-$STAMP" "$SITE"
    [ -f "/root/ratelimit-zones.backup-$STAMP" ] && cp "/root/ratelimit-zones.backup-$STAMP" "$ZONES"
    nginx -t
    exit 1
fi

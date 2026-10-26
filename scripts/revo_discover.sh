#!/usr/bin/env bash
# Read-only: finds the RevoCalls API address from its own website, then tests the key.
# CH-289 (Sir, 26 Oct): first step of matching Revo with Avortyx. RevoCalls has no
# public API docs, so this reads the address its own website uses. Needs
# REVO_API_KEY and REVO_WORKSPACE in .env. Run on the host: bash scripts/revo_discover.sh
cd /opt/call_platform || exit 1
W=/tmp/revo-disc; rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
SITE=https://console.revocalls.com
curl -s -m 20 -L "$SITE/auth/login" -o page.html
echo "== page: $(wc -c < page.html) bytes"
grep -oE '(src|href)="[^"]+\.js[^"]*"' page.html | sed -E 's/^(src|href)="//; s/"$//' | sort -u > js.txt
echo "== scripts: $(wc -l < js.txt)"
while read -r js; do
  case "$js" in http*) u="$js";; //*) u="https:$js";; /*) u="$SITE$js";; *) u="$SITE/$js";; esac
  curl -s -m 30 "$u"; echo
done < js.txt > all.js
# chunks loaded later by the main script
grep -oE '"[^"]*(assets|_next|static|js)/[^"]+\.js"' all.js | tr -d '"' | sort -u | head -150 > more.txt
while read -r js; do
  case "$js" in http*) u="$js";; /*) u="$SITE$js";; *) u="$SITE/$js";; esac
  curl -s -m 30 "$u"; echo
done < more.txt >> all.js
echo "== javascript read: $(wc -c < all.js) bytes"
echo "== addresses in the site's code:"
grep -oE 'https?://[A-Za-z0-9.-]+(\.[A-Za-z]{2,})(/[A-Za-z0-9._/-]*)?' all.js | grep -viE 'w3\.org|reactjs|github|mozilla|google|gstatic|jsdelivr|unpkg|sentry|cloudflare|schema\.org|apple\.com|microsoft' | sort | uniq -c | sort -rn | head -25
echo "== API paths about calls/logs/reports:"
grep -oE '["`](/?api)?/[A-Za-z0-9_/{}:$.-]*(call|log|report|cdr|stat|workspace)[A-Za-z0-9_/{}:$.-]*["`]' all.js | tr -d '"`' | sort -u | head -60
echo "== key test:"
KEY=$(grep '^REVO_API_KEY=' /opt/call_platform/.env | cut -d= -f2- | tr -d "'")
WS=$(grep '^REVO_WORKSPACE=' /opt/call_platform/.env | cut -d= -f2- | tr -d "'")
for base in $(grep -oE 'https?://[A-Za-z0-9.-]*revocalls\.com(/api)?' all.js | sort -u) https://console.revocalls.com/api https://api.revocalls.com/api https://api.revocalls.com; do
  for p in /user /me /workspaces "/workspaces/$WS" "/workspaces/$WS/calls" "/workspaces/$WS/call-logs" "/$WS/calls"; do
    r=$(printf 'header = "Authorization: Bearer %s"\nheader = "Accept: application/json"\n' "$KEY" \
        | curl -s -m 15 -K - -o resp.txt -w '%{http_code} %{content_type}' "$base$p")
    echo "${base}${p//$WS/<ws>} -> $r :: $(head -c 100 resp.txt | tr '\n\r' '  ')"
  done
done | sort -u
unset KEY

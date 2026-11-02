#!/bin/bash
# CH-466: install or update Server A's fallback routing so that the asterisk
# user - who runs the routing script (AGI) - can read it. Server A keeps
# /opt/call_platform and /var/lib/avortyx readable by root only, so the code
# the AGI loads is copied to /usr/local/lib/avortyx/fallback.
#
# Run as root on Server A after every `git pull` that changes scripts/fallback:
#     bash /opt/call_platform/scripts/fallback/install_a.sh
# It ends with a check run as the asterisk user.
set -euo pipefail
SRC=/opt/call_platform/scripts/fallback
LIB=/usr/local/lib/avortyx/fallback
STATE=/var/lib/avortyx/fallback
AU=asterisk

install -d -m 755 /usr/local/lib/avortyx "$LIB"
install -m 644 "$SRC/avx_fallback.py" "$SRC/fallback_agi.py" "$SRC/fallback_sync.py" "$LIB/"
install -o "$AU" -g "$AU" -m 755 "$SRC/route_call.py" /usr/share/asterisk/agi-bin/route_call.py
# The asterisk user may pass through /var/lib/avortyx (not list it) to its own folder.
chmod 711 /var/lib/avortyx
install -d -o "$AU" -g "$AU" -m 750 "$STATE" "$STATE/pending"
if [ -f "$STATE/journal.sqlite3" ]; then chown "$AU:$AU" "$STATE/journal.sqlite3"; fi
if [ -f "$STATE/plan.sqlite3" ]; then chmod 644 "$STATE/plan.sqlite3"; fi
install -m 644 /opt/call_platform/deploy/avx-fallback-sync.service /etc/systemd/system/avx-fallback-sync.service
systemctl daemon-reload
systemctl restart avx-fallback-sync
sleep 3

sudo -u "$AU" python3 - "$LIB" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import fallback_agi  # noqa: F401  (the AGI imports this)
import avx_fallback as f
plan = f.Plan.open()
rows = f.Journal(timeout=1).db.execute('select count(*) from calls').fetchone()[0]
print('fallback ready for the asterisk user: plan', 'yes' if plan else 'NOT YET (give it a minute)',
      '- journal opens,', rows, 'calls in it')
PY

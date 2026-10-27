#!/usr/bin/env bash
# CH-331: check the server's instant alerts (CH-327) end to end.
#
#   bash scripts/host_watch_selftest.sh
#
# Makes one harmless test change of each kind the watcher reports, waits for
# its next run (cron, every minute), undoes every change, waits again and then
# says which kinds it saw. Each change also arrives in the status chat on
# Telegram, so you can compare the two.
#
#   config    a comment line in /etc/hosts
#   env       WATCH_SELFTEST=1 in .env (no value is ever sent, only the name)
#   firewall  ufw deny from 192.0.2.123 (a documentation-only address)
#   cron      a comment line in root's crontab
#   users     user "watchselftest" (no password, removed again)
#   software  the package "cowsay" (removed again)
#   sudo      sudo true
#   restart   systemctl restart cron (calls are not affected)
#   port      a listener on 0.0.0.0:47999 for about two minutes
#
# Everything is undone even if the script is stopped with Ctrl+C. It never
# touches Asterisk, the relay, the database or the web containers.
# Takes about 3 minutes. Run as root on the server.
set -u

APP="${APP_DIR:-/opt/call_platform}"
ENV_FILE="${ENV_FILE:-$APP/.env}"
TEST_IP=192.0.2.123
TEST_PORT=47999
TEST_USER=watchselftest
MARK='# host_watch selftest'
WAIT=80

[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
cd "$APP" || { echo "No $APP"; exit 1; }

look() {
    # The watcher's report without sending or saving; retried while the
    # minute's real run holds the lock.
    for _ in 1 2 3 4 5 6; do
        out=$(python3 scripts/host_watch.py --look 2>/dev/null)
        if [ -n "$out" ] && ! printf '%s' "$out" | grep -q '"skipped"'; then
            printf '%s' "$out"
            return
        fi
        sleep 5
    done
    echo '{}'
}

counts() {
    look | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(json.dumps({"events": d.get("events_24h") or {}, "new_ports": (d.get("ports") or {}).get("new") or [],
                  "open": d.get("open_critical") or []}))'
}

LISTENER=""
CRON_BACKUP=""
undo() {
    sed -i "/^${MARK}\$/d" /etc/hosts
    [ -f "$ENV_FILE" ] && sed -i '/^WATCH_SELFTEST=/d' "$ENV_FILE"
    ufw delete deny from "$TEST_IP" >/dev/null 2>&1
    # root's crontab goes back exactly as it was (never rebuilt from a failed read)
    if [ -n "$CRON_BACKUP" ] && [ -s "$CRON_BACKUP" ]; then
        crontab "$CRON_BACKUP" && rm -f "$CRON_BACKUP"
        CRON_BACKUP=""
    fi
    id "$TEST_USER" >/dev/null 2>&1 && userdel -r "$TEST_USER" >/dev/null 2>&1
    if dpkg -s cowsay >/dev/null 2>&1 && [ "${COWSAY_WAS_THERE:-no}" = no ]; then
        DEBIAN_FRONTEND=noninteractive timeout 180 apt-get remove -y -qq cowsay >/dev/null 2>&1
    fi
    [ -n "$LISTENER" ] && kill "$LISTENER" >/dev/null 2>&1
    LISTENER=""
}
trap 'echo; echo "Stopped: undoing the test changes."; undo; exit 1' INT TERM

echo "== Before"
BEFORE=$(counts)

echo "== 1/3 Making the test changes"
echo "$MARK" >> /etc/hosts
[ -f "$ENV_FILE" ] && echo "WATCH_SELFTEST=1" >> "$ENV_FILE"
ufw deny from "$TEST_IP" comment 'host_watch selftest' >/dev/null
CRON_BACKUP=$(mktemp /root/crontab-before-selftest.XXXXXX)
if crontab -l > "$CRON_BACKUP" 2>/dev/null && [ -s "$CRON_BACKUP" ]; then
    (cat "$CRON_BACKUP"; echo "$MARK") | crontab -
else
    rm -f "$CRON_BACKUP"; CRON_BACKUP=""
    echo "   (could not read root's crontab: the cron test is skipped)"
fi
useradd -m -s /bin/bash "$TEST_USER"
if dpkg -s cowsay >/dev/null 2>&1; then
    COWSAY_WAS_THERE=yes
    echo "   (cowsay is already installed: the software test is skipped)"
else
    COWSAY_WAS_THERE=no
    DEBIAN_FRONTEND=noninteractive timeout 180 apt-get install -y -qq cowsay >/dev/null 2>&1 \
        || echo "   (apt-get install cowsay failed: the software test will show as not seen)"
fi
sudo -n true
systemctl restart cron
python3 -c "import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', $TEST_PORT)); s.listen(); time.sleep(240)" &
LISTENER=$!

echo "== Waiting ${WAIT}s for the watcher's next run"
sleep "$WAIT"
DURING=$(counts)

echo "== 2/3 Undoing the test changes"
undo
echo "== Waiting ${WAIT}s for the watcher to see them undone"
sleep "$WAIT"
AFTER=$(counts)

echo "== 3/3 Result"
python3 - "$BEFORE" "$DURING" "$AFTER" "$TEST_PORT" "${COWSAY_WAS_THERE:-no}" <<'EOF'
import json, sys
before, during, after = (json.loads(x) for x in sys.argv[1:4])
port, cowsay_there = sys.argv[4], sys.argv[5] == 'yes'
b, a = before['events'], after['events']
names = {'config': 'Config file changed (/etc/hosts)', 'env': '.env / API key changed (WATCH_SELFTEST)',
         'firewall': 'Firewall rule changed (ufw)', 'cron': 'Cron job changed', 'users': 'New user added / removed',
         'software': 'Software installed / removed (cowsay)', 'sudo': 'sudo command', 'restart': 'Service restarted (cron)'}
ok = 0
for kind, label in names.items():
    if kind == 'software' and cowsay_there:
        print(f'  ⚪ {label}: skipped')
        continue
    seen = a.get(kind, 0) - b.get(kind, 0)
    print(f'  {"✅" if seen > 0 else "❌"} {label}: {seen} alert(s)')
    ok += seen > 0
port_seen = any(port in p for p in during['new_ports']) or any(port in t for t in during['open'])
port_gone = not any(port in t for t in after['open'])
print(f'  {"✅" if port_seen else "❌"} New port open (tcp/{port}): {"seen" if port_seen else "not seen"}'
      f'{", closed again" if port_seen and port_gone else ""}')
ok += port_seen
total = len(names) + 1 - (1 if cowsay_there else 0)
print(f'\n{ok} of {total} alert kinds seen. Each one was also sent to the status chat on Telegram.')
if ok < total:
    print('A ❌ means the watcher did not see that change: send the output of\n'
          '  python3 scripts/host_watch.py --look')
EOF

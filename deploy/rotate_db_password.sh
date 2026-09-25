#!/usr/bin/env bash
# Rotate the Postgres password.
#
# Two things broke the earlier attempts, both the same mistake:
#
#   1. Checking that the old password appeared SOMEWHERE in .env, rather than
#      inside DATABASE_URL. It was present in another variable, so the guard
#      passed and the URL was never rewritten.
#
#   2. Using `docker compose restart`, which does NOT re-read env_file. The
#      environment is baked in when a container is created. .env was correct
#      both times and the container never saw it: the database had the new
#      password while the app kept sending the old one.
#
# This script rewrites every place the password appears, recreates the
# containers with `up -d`, verifies a real query, and rolls back on its own if
# the verification fails.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

STAMP=$(date +%F-%H%M%S)
BACKUP=".env.backup-$STAMP"

fail() { echo; echo "FAILED: $*"; echo "Rolling back."; rollback; exit 1; }

rollback() {
    [ -f "$BACKUP" ] && cp "$BACKUP" .env
    docker compose exec -T postgres psql -U call_platform -d call_platform \
        -c "ALTER USER call_platform WITH PASSWORD '$OLDPW';" >/dev/null 2>&1
    docker compose up -d >/dev/null 2>&1
    sleep 10
    echo "Rolled back to the previous password."
}

[ -f .env ] || { echo "No .env here."; exit 1; }
cp .env "$BACKUP" || exit 1
echo "1/6  backed up .env -> $BACKUP"

# The password the app is currently using, read out of DATABASE_URL itself
# rather than assumed.
OLDPW=$(grep -m1 '^DATABASE_URL=' .env | sed -E 's|^DATABASE_URL=.*://[^:]+:([^@]*)@.*|\1|')
if [ -z "$OLDPW" ] || [ "$OLDPW" = "$(grep -m1 '^DATABASE_URL=' .env)" ]; then
    echo "Could not read the current password out of DATABASE_URL."
    echo "DATABASE_URL line shape:"
    grep -m1 '^DATABASE_URL=' .env | sed -E 's|(://[^:]+:)[^@]*(@)|\1********\2|'
    exit 1
fi
echo "2/6  read current password from DATABASE_URL (${#OLDPW} chars)"

# Hex only, so nothing needs escaping inside a URL.
NEWPW=$(openssl rand -hex 24)

docker compose exec -T postgres psql -U call_platform -d call_platform \
    -c "ALTER USER call_platform WITH PASSWORD '$NEWPW';" >/dev/null \
    || { echo "ALTER USER failed - nothing changed."; exit 1; }
echo "3/6  database password changed"

# Replace the old password everywhere it appears, so no variable is left behind
# holding the previous value.
python3 - "$OLDPW" "$NEWPW" <<'PY'
import sys
old, new = sys.argv[1], sys.argv[2]
text = open('.env').read()
n = text.count(old)
text = text.replace(old, new)
if 'POSTGRES_PASSWORD=' not in text:
    text = text.rstrip('\n') + f'\nPOSTGRES_PASSWORD={new}\n'
open('.env', 'w').write(text)
print(f"4/6  rewrote {n} occurrence(s) in .env")
PY

# up -d, not restart: restart keeps the old baked-in environment.
docker compose up -d >/dev/null 2>&1 || fail "docker compose up -d"
echo "5/6  containers recreated (up -d, so .env is re-read)"

sleep 12
if docker compose exec -T web python manage.py shell \
     -c "from routing.models import CallLog; print('calls:', CallLog.objects.count())" 2>/dev/null | grep -q calls; then
    echo "6/6  verified: the app can query the database"
    echo
    echo "ROTATED OK. Backup kept at $BACKUP"
else
    fail "the app still cannot reach the database"
fi

#!/usr/bin/env bash
#
# Rotate SECRET_KEY, which is what actually ends everybody's session.
#
# This platform does not have `token_blacklist` installed, so an issued JWT
# cannot be revoked one at a time - the Active Sessions page and the revoke
# button have never worked, they fail and return an empty list. SIMPLE_JWT
# signs with SECRET_KEY, so changing it invalidates every token that exists,
# everywhere, immediately.
#
# That is the point. After an ex-employee leaves, their open browser tab keeps
# working until this runs.
#
# Everyone has to sign in again afterwards, including you. Have the new
# passwords to hand before you start.
#
#   sudo /opt/call_platform/deploy/rotate_secret_key.sh
#
set -euo pipefail

APP_DIR=/opt/call_platform
cd "$APP_DIR"

STAMP=$(date -u +%F_%H%M%S)
BACKUP="/root/env-backup-${STAMP}"

echo "== backing up .env to ${BACKUP} =="
cp .env "$BACKUP"
chmod 600 "$BACKUP"

# No $ and no backtick in the alphabet. docker compose interpolates $NAME out
# of an env_file value, so a key containing $K2xX was silently replaced with a
# blank string and the container ran on a shorter key than the one in .env -
# with a warning that is easy to scroll past. Also no quotes or backslashes,
# which the shell and the file format both argue about.
NEW_KEY=$(python3 -c "import secrets,string; a=string.ascii_letters+string.digits+'!@#%^&*()-_=+[]{}<>:?,./'; print(''.join(secrets.choice(a) for _ in range(72)))")

echo "== writing the new SECRET_KEY =="
if grep -q '^SECRET_KEY=' .env; then
    # A literal replacement, not sed's pattern substitution: a generated key
    # contains &, / and \ often enough that sed would mangle it silently.
    python3 - "$NEW_KEY" <<'PY'
import sys
key = sys.argv[1]
lines = open('/opt/call_platform/.env').read().splitlines(keepends=True)
out = []
for line in lines:
    if line.startswith('SECRET_KEY='):
        out.append(f'SECRET_KEY={key}\n')
    else:
        out.append(line)
open('/opt/call_platform/.env', 'w').writelines(out)
PY
else
    printf 'SECRET_KEY=%s\n' "$NEW_KEY" >> .env
fi

echo "== restarting so the new key is loaded =="
# `up -d --force-recreate`, never `restart`: a plain restart does not reload
# env_file, which is what made two password rotations look like they had failed.
docker compose up -d --force-recreate web celery_worker celery_beat

echo "== waiting for the containers =="
sleep 14

echo "== checking the platform still answers =="
ROUTING=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
          -X POST http://127.0.0.1:8000/api/twilio/asterisk/route/ || echo none)
PORTAL=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
         https://avortyx.io/api/docs || echo none)

echo "   routing: ${ROUTING}   (403 is correct - it wants the shared secret)"
echo "   portal:  ${PORTAL}   (expect 200)"

echo "== checking the container got the key that is in the file =="
FILE_KEY=$(grep -m1 '^SECRET_KEY=' .env | cut -d= -f2-)
RUNNING_KEY=$(docker compose exec -T web python -c \
    "from django.conf import settings; print(settings.SECRET_KEY)" 2>/dev/null | tr -d '\r\n')
if [ "$FILE_KEY" = "$RUNNING_KEY" ]; then
    echo "   key matches .env"
else
    echo "   !! THE RUNNING KEY DOES NOT MATCH .env"
    echo "   !! docker compose has interpolated something out of the value."
    echo "   !! file length ${#FILE_KEY}, running length ${#RUNNING_KEY}"
    echo "   Put the backup back and tell somebody:"
    echo "     cp ${BACKUP} ${APP_DIR}/.env"
    exit 1
fi

if [ "$ROUTING" != "403" ] || [ "$PORTAL" != "200" ]; then
    echo
    echo "!! SOMETHING IS WRONG. Put the old file back and restart:"
    echo "     cp ${BACKUP} ${APP_DIR}/.env"
    echo "     cd ${APP_DIR} && docker compose up -d --force-recreate web celery_worker celery_beat"
    exit 1
fi

echo
echo "Done. Every login token issued before now is dead."
echo "Everyone signs in again, including you."
echo
echo "The old .env is at ${BACKUP} if you need to go back."
echo "Delete it once you are sure: shred -u ${BACKUP}"

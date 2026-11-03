#!/bin/bash
# CH-469 load test, SERVER B: a separate TEST copy of call routing.
#
#   cd /opt/call_platform && bash scripts/loadtest/b_loadtest.sh <step>
#
# Since the move Server B holds the LIVE database, so the test no longer runs
# on it. It runs on its own copy:
#   - database avortyx_loadtest, copied from live at "setup";
#   - container avx-loadtest-routing, the same code as web_calls, listening
#     on 10.20.0.2:8011 (live routing is 8001);
#   - Redis database 9 (live uses 0), and every outside service switched off
#     (scripts/loadtest/make_env_b.py).
# Only the test number's calls on Server A are sent to port 8011. Live
# containers, the live database and live Redis are never changed.
#
# Steps:
#   setup    copy the live database to avortyx_loadtest, start the test
#            routing, create the test campaign in the copy
#   down     switch the test routing off (Server A must route alone)
#   up       switch it back on
#   during   Sir's extras, while calls run: add 1,000 TFNs, pause and play
#            the test campaign, run a 30-day report. One at a time:
#              during tfns | during pause | during report
#   report   the result: calls, buyers, limits, billing
#   cleanup  remove the test routing, the test database and Redis database 9
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1
C=avx-loadtest-routing
TEST_DB=avortyx_loadtest
PORT=8011
LIVE_IP=10.20.0.2

die() { echo "ERROR: $*" >&2; exit 1; }

[ -r .env ] || die "no .env here: run from /opt/call_platform on Server B"
grep -E '^ALLOWED_HOSTS=' .env | grep -q "$LIVE_IP" \
    || die "this is not Server B ($LIVE_IP is not in ALLOWED_HOSTS)"

# The live database's user and name, from the live DATABASE_URL.
read -r DB_USER LIVE_DB < <(python3 - <<'EOF'
import sys
sys.path.insert(0, 'scripts/loadtest')
from make_env_b import parse
from urllib.parse import urlsplit
u = urlsplit(parse(open('.env').read()).get('DATABASE_URL', ''))
print(u.username or 'call_platform', u.path.lstrip('/') or 'call_platform')
EOF
)
[ -n "${LIVE_DB:-}" ] && [ "$LIVE_DB" != "$TEST_DB" ] || die "could not read the live database name"

pg() { docker compose exec -T postgres "$@"; }
redis9() {
    docker compose exec -T redis sh -c \
        'redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --no-auth-warning -n 9 '"$*"
}
in_test() { docker exec -i "$@"; }
calls_in() { pg psql -U "$DB_USER" -d "$1" -tAc "SELECT count(*) FROM call_logs" | tr -d '[:space:]'; }

wait_up() {
    for _ in $(seq 1 60); do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
               "http://127.0.0.1:$PORT/api/twilio/asterisk/fallback-plan/")
        # 403: the app answered and refused a request without the shared secret.
        [ "$code" = "403" ] && return 0
        sleep 2
    done
    return 1
}

case "${1:-}" in
setup)
    LIVE=$(docker compose ps -q web_calls)
    [ -n "$LIVE" ] || die "live web_calls is not running"
    IMG=$(docker inspect -f '{{.Image}}' "$LIVE")
    NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$LIVE" | awk '{print $1}')
    WORKERS=$(grep -E '^CALL_WORKERS=' .env | tail -1 | cut -d= -f2 | tr -dc 0-9)

    echo "== Test settings (live database $LIVE_DB -> test copy $TEST_DB)"
    python3 scripts/loadtest/make_env_b.py .env .env.loadtest || exit 1

    echo "== Copy of the live database (reads only from live)"
    docker rm -f "$C" >/dev/null 2>&1
    pg dropdb -U "$DB_USER" --if-exists "$TEST_DB" || die "could not remove an old $TEST_DB"
    pg createdb -U "$DB_USER" "$TEST_DB" || die "could not create $TEST_DB"
    pg pg_dump -U "$DB_USER" -Fc "$LIVE_DB" \
        | pg pg_restore -U "$DB_USER" -d "$TEST_DB" --no-owner --no-privileges
    live_n=$(calls_in "$LIVE_DB"); test_n=$(calls_in "$TEST_DB")
    echo "calls: live $live_n, test copy $test_n"
    [ -n "$test_n" ] && [ "$test_n" -gt 0 ] || die "the test copy has no calls"
    redis9 FLUSHDB >/dev/null

    echo "== Test routing on $LIVE_IP:$PORT"
    docker run -d --name "$C" --restart no --network "$NET" \
        --env-file .env.loadtest -v "$PWD:/app" -w /app \
        -p "$LIVE_IP:$PORT:8000" -p "127.0.0.1:$PORT:8000" \
        "$IMG" uvicorn config.asgi:application --host 0.0.0.0 --port 8000 \
        --workers "${WORKERS:-12}" --lifespan off --timeout-keep-alive 5 >/dev/null \
        || die "test routing did not start"
    wait_up || { docker logs --tail 30 "$C"; die "test routing does not answer on port $PORT"; }
    db=$(in_test "$C" python manage.py shell -c "from django.conf import settings; print(settings.DATABASES['default']['NAME'])" 2>/dev/null | tail -1)
    [ "$db" = "$TEST_DB" ] || { docker rm -f "$C" >/dev/null; die "test routing is on database '$db', not $TEST_DB: stopped it"; }
    echo "test routing is up, on database $db"

    echo "== Test campaign in the copy"
    in_test "$C" python manage.py shell < scripts/loadtest/setup_b.py
    ;;
down)
    docker stop -t 2 "$C" >/dev/null && echo "test routing OFF at $(date +%T): Server A routes the test calls alone"
    ;;
up)
    docker start "$C" >/dev/null && wait_up && echo "test routing ON at $(date +%T)"
    ;;
during)
    case "${2:-}" in tfns|pause|report) ;; *) die "use: during tfns | during pause | during report" ;; esac
    in_test -e LOADTEST_STEP="$2" "$C" python manage.py shell < scripts/loadtest/during_b.py
    ;;
report)
    in_test "$C" python manage.py shell < scripts/loadtest/report_b.py
    ;;
status)
    docker ps -a --filter "name=^${C}$" --format '{{.Names}}: {{.Status}}'
    ;;
cleanup)
    docker rm -f "$C" >/dev/null 2>&1 && echo "test routing removed"
    pg dropdb -U "$DB_USER" --if-exists "$TEST_DB" && echo "test database $TEST_DB removed"
    redis9 FLUSHDB >/dev/null && echo "Redis database 9 emptied"
    rm -f .env.loadtest
    echo "live: $(calls_in "$LIVE_DB") calls in $LIVE_DB (unchanged by the test)"
    ;;
*)
    sed -n '2,25p' "$0"
    exit 1
    ;;
esac

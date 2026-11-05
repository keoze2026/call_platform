#!/usr/bin/env bash
# Customer accounts 1c (CH-580): the portal's restricted database login (Server B).
#
#   bash scripts/portal_db_role.sh [database] [--no-env]
#
#   database   the database to grant on (default: call_platform)
#   --no-env   rehearsal: role and grants only, .env untouched
#
# The role avx_portal: a login, NOT a superuser, no BYPASSRLS, cannot create
# databases or roles. It gets read/write on the tables (row-level security
# decides which rows) and the sequences, also on tables created later by
# migrations. Without --no-env, if .env has no PORTAL_DATABASE_URL yet, a new
# random password is set and PORTAL_DATABASE_URL is written into .env; the
# password is never printed and never on a command line. Nothing else in .env
# changes; no container is restarted. Safe to run again.
#
# Works on the live checkout (APP_DIR, default /opt/call_platform): its .env and
# its running postgres container, wherever this copy of the script is.
set -euo pipefail
DB=call_platform; WRITE_ENV=1
for a in "$@"; do
  case "$a" in --no-env) WRITE_ENV=0 ;; *) DB="$a" ;; esac
done
cd "${APP_DIR:-/opt/call_platform}"
psql() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U call_platform -d "$DB" -qAt "$@"; }

if [ -z "$(psql -c "select 1 from pg_roles where rolname='avx_portal'")" ]; then
  psql -c "create role avx_portal login nosuperuser nobypassrls nocreatedb nocreaterole noinherit"
  echo "role avx_portal created"
else
  echo "role avx_portal already exists"
fi

if [ "$WRITE_ENV" = 1 ] && ! grep -q '^PORTAL_DATABASE_URL=' .env; then
  PW=$(openssl rand -hex 24)
  # Through stdin, not the command line: the password never shows in `ps`.
  psql <<< "alter role avx_portal password '$PW'"
  AVX_PW="$PW" python3 - <<'PY'
import os, re
pw = os.environ['AVX_PW']
env = open('.env').read()
url = re.search(r'^DATABASE_URL=(.*)$', env, re.M).group(1).strip().strip('"\'')
scheme, rest = url.split('://', 1)
host = rest.rsplit('@', 1)[1]            # everything after the owner's user:password
with open('.env', 'a') as f:
    f.write(('' if env.endswith('\n') else '\n') + f'PORTAL_DATABASE_URL={scheme}://avx_portal:{pw}@{host}\n')
print('PORTAL_DATABASE_URL written to .env (password not shown)')
PY
  unset PW
fi

psql <<SQL
grant connect on database "$DB" to avx_portal;
grant usage on schema public to avx_portal;
grant select, insert, update, delete on all tables in schema public to avx_portal;
grant usage, select on all sequences in schema public to avx_portal;
alter default privileges for role call_platform in schema public grant select, insert, update, delete on tables to avx_portal;
alter default privileges for role call_platform in schema public grant usage, select on sequences to avx_portal;
SQL
echo "grants on $DB: done"
psql -c "select rolname, rolsuper, rolbypassrls, rolcanlogin from pg_roles where rolname='avx_portal'"

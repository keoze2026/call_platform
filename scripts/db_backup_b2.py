#!/usr/bin/env python3
"""Nightly database backup, copied to Backblaze B2 (CH-314, issue 2).

Call logs, billing, buyers and settings live only in the Postgres container on
this server's disk. Recordings are safe in B2 (CH-304/305); the database was
not. Every night this:

  1. dumps the database from the postgres container (pg_dump, a consistent
     snapshot; calls keep running, it only reads)
  2. compresses it to /var/backups/avortyx/db-<UTC time>.sql.gz
  3. checks the file is a complete dump (gzip reads to the end, and the dump's
     own "dump complete" line is there)
  4. uploads it to B2 as backups/db/<name>, with B2 checking the SHA-1
  5. keeps the last 7 dumps on the server; B2 keeps every dump (the bucket's
     lifecycle keeps only the last version of a name, and every name differs)
  6. writes /var/lib/avortyx/backup_last.json - the status bot reads it

Runs on the HOST. Python standard library only, uses scripts/recordings_to_b2.py
for B2. Restarts nothing.

    python3 scripts/db_backup_b2.py            # back up now
    python3 scripts/db_backup_b2.py --status   # last backup
    cron: 30 3 * * * python3 /opt/call_platform/scripts/db_backup_b2.py >> /var/log/db_backup.log 2>&1

Restore (into a NEW, empty database - never over the live one without a plan):
    gunzip -c /var/backups/avortyx/db-....sql.gz | docker compose exec -T postgres psql -U call_platform -d <new db>
"""
import gzip
import hashlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recordings_to_b2 as R  # noqa: E402

APP = os.environ.get('APP_DIR', '/opt/call_platform')
OUT = os.environ.get('BACKUP_DIR', '/var/backups/avortyx')
STATE = os.environ.get('BACKUP_STATE', '/var/lib/avortyx/backup_last.json')
KEEP = int(os.environ.get('BACKUP_KEEP', '7'))
PREFIX = 'backups/db/'
DUMP = ['docker', 'compose', 'exec', '-T', 'postgres', 'sh', '-c',
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner']


def log(msg):
    print(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {msg}', flush=True)


def save_state(**kw):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    kw['at'] = int(time.time())
    old = {}
    if os.path.exists(STATE):
        try:
            old = json.load(open(STATE))
        except ValueError:
            old = {}
    if kw.get('ok'):
        old['last_ok'] = kw
    old['last_run'] = kw
    tmp = STATE + '.tmp'
    json.dump(old, open(tmp, 'w'), indent=1)
    os.replace(tmp, STATE)


def dump(path):
    """pg_dump into a gzip file. Returns the raw dump size."""
    raw = 0
    tail = b''
    with gzip.open(path + '.part', 'wb', compresslevel=6) as gz:
        p = subprocess.Popen(DUMP, cwd=APP, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for chunk in iter(lambda: p.stdout.read(1 << 20), b''):
            gz.write(chunk)
            raw += len(chunk)
            tail = (tail + chunk)[-4096:]
        err = p.stderr.read().decode('utf-8', 'replace').strip()
        if p.wait() != 0:
            raise RuntimeError(f'pg_dump failed: {err[-300:]}')
    if b'PostgreSQL database dump complete' not in tail:
        raise RuntimeError('the dump is incomplete (no "dump complete" line)')
    # Read the whole file back: a damaged gzip fails here, not on restore day.
    with gzip.open(path + '.part', 'rb') as gz:
        for _ in iter(lambda: gz.read(1 << 20), b''):
            pass
    os.replace(path + '.part', path)
    return raw


def prune():
    files = sorted(f for f in os.listdir(OUT) if f.startswith('db-') and f.endswith('.sql.gz'))
    for f in files[:-KEEP]:
        os.remove(os.path.join(OUT, f))


def main():
    if '--status' in sys.argv:
        print(open(STATE).read() if os.path.exists(STATE) else 'no backup yet')
        return 0
    os.makedirs(OUT, exist_ok=True)
    name = f'db-{time.strftime("%Y%m%d-%H%M", time.gmtime())}.sql.gz'
    path = os.path.join(OUT, name)
    try:
        raw = dump(path)
        size = os.path.getsize(path)
        log(f'dumped {raw / 1e6:.1f} MB -> {name} ({size / 1e6:.1f} MB)')
        env = R.read_env(R.ENV_FILE)
        b2 = R.B2(env['B2_KEY_ID'], env['B2_APPLICATION_KEY'], env['B2_BUCKET'])
        sha1 = R.sha1_of(path)
        r = b2.upload(path, PREFIX + name, sha1, size, int(time.time() * 1000), content_type='application/gzip')
        if int(r.get('contentLength', -1)) != size or r.get('contentSha1') != sha1:
            raise RuntimeError('B2 reply does not match the file')
        prune()
        save_state(ok=True, name=name, size=size, raw=raw, b2=PREFIX + name)
        log(f'backup OK: {name} in B2 ({size / 1e6:.1f} MB), last {KEEP} kept on the server')
        return 0
    except Exception as e:  # noqa: BLE001 - every failure is recorded for the bot
        for p in (path + '.part',):
            if os.path.exists(p):
                os.remove(p)
        save_state(ok=False, name=name, error=str(e)[:300])
        log(f'backup FAILED: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

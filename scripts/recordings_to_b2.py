#!/usr/bin/env python3
"""Copy finished call recordings (MP3) to Backblaze B2 (CH-304), serve them
from B2 once they are off the disk and remove old local copies (CH-305).

Sir, 26 Oct: 200K calls a month at a 14-minute AHT will not fit on the server's
disk. Backblaze B2 is approved. This is step 1: every finished MP3 is copied
to the bucket and checked there. Nothing on the server is deleted and the
links do not change - recordings still play from the server as today.
Step 2 (later, separately): links fall back to B2, then old local copies go.

Runs on the HOST (the recordings are in the host's folder), like
recordings_to_mp3.sh. Python standard library only; restarts nothing and does
not touch Asterisk, the containers or the calls.

For each <id>.mp3 not written to for 10 minutes and not copied yet:
  1. its SHA-1 is computed and it is uploaded as recordings/<id>.mp3
     (the same path nginx serves it under)
  2. B2 checks the SHA-1 itself and refuses a damaged upload; the reply's
     size and SHA-1 are checked again here
  3. only then is it recorded as copied (state: /var/lib/avortyx/b2.sqlite3)
A failure is logged and the file is tried again next run.

Settings (from /opt/call_platform/.env): B2_KEY_ID, B2_APPLICATION_KEY,
B2_BUCKET.

    scripts/recordings_to_b2.py --dry-run     # list what it would copy
    scripts/recordings_to_b2.py --limit 5     # copy 5 now
    scripts/recordings_to_b2.py --verify 3    # download 3 back and compare
    scripts/recordings_to_b2.py --status      # copied / waiting
    cron: */10 * * * * /opt/call_platform/scripts/recordings_to_b2.py >> /var/log/recordings_to_b2.log 2>&1

CH-305, step 2 (links fall back to B2, then old local copies go):

    scripts/recordings_to_b2.py --nginx        # write nginx's B2 settings, check, reload
    scripts/recordings_to_b2.py --delete-local-days 7

--nginx writes /etc/nginx/avortyx-b2.conf: where the bucket is and a read-only
download token for recordings/ (7 days; renewed daily by cron), then runs
`nginx -t` and reloads nginx (a reload, not a restart: open downloads finish,
calls are not involved). A failed check puts the previous file back.
scripts/b2_nginx.sh sets rec.v0l1.com up to use it.

--delete-local-days N removes a local MP3 only when all of these hold:
  - the link fallback works right now: a test file that is only in B2
    (recordings/b2-canary.mp3) plays through the public link
  - it was copied (CH-304) and B2 still has it with the same size and SHA-1
  - it is older than N days and unchanged since it was copied
Otherwise nothing is deleted that run.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

ENV_FILE = os.environ.get('ENV_FILE', '/opt/call_platform/.env')
DIR = os.environ.get('RECORDINGS_DIR', '/var/spool/asterisk/recordings')
STATE = os.environ.get('STATE_DB', '/var/lib/avortyx/b2.sqlite3')
AUTH_URL = os.environ.get('B2_AUTH_URL', 'https://api.backblazeb2.com/b2api/v3/b2_authorize_account')
MIN_AGE_MIN = int(os.environ.get('MIN_AGE_MIN', '10'))
BATCH = int(os.environ.get('BATCH', '500'))
PREFIX = 'recordings/'
RETRIES = 3
NGINX_SNIPPET = os.environ.get('NGINX_SNIPPET', '/etc/nginx/avortyx-b2.conf')
CANARY = 'b2-canary.mp3'
CANARY_BODY = b'AVORTYX B2 FALLBACK TEST - CH-305\n'
TOKEN_SECONDS = 7 * 24 * 3600
DELETE_PER_RUN = int(os.environ.get('DELETE_PER_RUN', '2000'))


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def log(msg):
    print(f'{now()} {msg}', flush=True)


def read_env(path):
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class B2Error(Exception):
    def __init__(self, status, text):
        super().__init__(f'HTTP {status}: {text[:200]}')
        self.status = status


def _request(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise B2Error(e.code, e.read().decode('utf-8', 'replace'))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise B2Error(0, str(e))


class B2:
    def __init__(self, key_id, key, bucket):
        basic = base64.b64encode(f'{key_id}:{key}'.encode()).decode()
        info = json.loads(_request(AUTH_URL, headers={'Authorization': f'Basic {basic}'}))
        api = info['apiInfo']['storageApi']
        self.token = info['authorizationToken']
        self.api_url = api['apiUrl']
        self.download_url = api['downloadUrl']
        self.bucket = bucket
        self.bucket_id = self._bucket_id(info['accountId'], bucket)
        self._upload = None

    def _call(self, name, body):
        return json.loads(_request(f'{self.api_url}/b2api/v3/{name}', json.dumps(body).encode(),
                                   {'Authorization': self.token, 'Content-Type': 'application/json'}))

    def _bucket_id(self, account_id, name):
        buckets = self._call('b2_list_buckets', {'accountId': account_id, 'bucketName': name}).get('buckets') or []
        if not buckets:
            raise SystemExit(f'bucket {name!r} not found for this key')
        return buckets[0]['bucketId']

    def upload(self, path, name, sha1, size, mtime_ms, data=None):
        last = None
        for attempt in range(RETRIES):
            if self._upload is None:
                self._upload = self._call('b2_get_upload_url', {'bucketId': self.bucket_id})
            if path is not None:
                with open(path, 'rb') as f:
                    data = f.read()
            if len(data) != size:
                raise B2Error(0, 'file changed while copying')
            headers = {
                'Authorization': self._upload['authorizationToken'],
                'X-Bz-File-Name': urllib.parse.quote(name),
                'Content-Type': 'audio/mpeg',
                'Content-Length': str(size),
                'X-Bz-Content-Sha1': sha1,
                'X-Bz-Info-src_last_modified_millis': str(mtime_ms),
            }
            try:
                return json.loads(_request(self._upload['uploadUrl'], data, headers, 'POST', timeout=300))
            except B2Error as e:
                last = e
                # B2's rule: on 401, 408, 429 or 5xx (or no answer) get a new
                # upload URL and try again, waiting a little longer each time.
                if e.status in (0, 401, 408, 429) or e.status >= 500:
                    self._upload = None
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last

    def file_info(self, file_id):
        return self._call('b2_get_file_info', {'fileId': file_id})

    def download_token(self):
        return self._call('b2_get_download_authorization', {
            'bucketId': self.bucket_id, 'fileNamePrefix': PREFIX,
            'validDurationInSeconds': TOKEN_SECONDS})['authorizationToken']

    def download(self, name):
        url = f'{self.download_url}/file/{urllib.parse.quote(self.bucket)}/{urllib.parse.quote(name)}'
        return _request(url, headers={'Authorization': self.token}, timeout=300)


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def open_state():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    db = sqlite3.connect(STATE)
    db.execute('CREATE TABLE IF NOT EXISTS copied (name TEXT PRIMARY KEY, size INTEGER, sha1 TEXT,'
               ' file_id TEXT, copied_at INTEGER)')
    cols = [r[1] for r in db.execute('PRAGMA table_info(copied)')]
    if 'deleted_at' not in cols:
        db.execute('ALTER TABLE copied ADD COLUMN deleted_at INTEGER')  # CH-305
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    db.commit()
    return db


def ensure_canary(db, b2):
    """The test file the fallback is checked with: only in B2, never on disk."""
    sha1 = hashlib.sha1(CANARY_BODY).hexdigest()
    if db.execute("SELECT value FROM meta WHERE key = 'canary'").fetchone() == (sha1,):
        return
    b2.upload(None, PREFIX + CANARY, sha1, len(CANARY_BODY), int(time.time() * 1000), data=CANARY_BODY)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('canary', ?)", (sha1,))
    db.commit()


def fallback_works(base_url):
    """Does a file that is only in B2 play through the public link now?"""
    url = f'{base_url.rstrip("/")}/recordings/{CANARY}'
    try:
        return _request(url, timeout=30) == CANARY_BODY
    except B2Error as e:
        log(f'fallback check {url}: {e}')
        return False


def write_nginx(b2, reload=True):
    """nginx's B2 settings: bucket path, host and a 7-day read token for recordings/."""
    import subprocess
    token = b2.download_token()
    host = urlsplit(b2.download_url).netloc
    text = (f'# Written by scripts/recordings_to_b2.py --nginx (CH-305) at {now()}.\n'
            f'# Renewed daily by cron; do not edit. Read-only token for {PREFIX} in {b2.bucket}.\n'
            f'rewrite ^/recordings/([A-Za-z0-9_.-]+)\\.(wav|mp3)$ /file/{urllib.parse.quote(b2.bucket)}/{PREFIX}$1.mp3 break;\n'
            f'proxy_pass {b2.download_url};\n'
            f'proxy_set_header Host {host};\n'
            f'proxy_set_header Authorization "{token}";\n')
    prev = NGINX_SNIPPET + '.prev'
    had = os.path.exists(NGINX_SNIPPET)
    if had:
        os.replace(NGINX_SNIPPET, prev)
    fd = os.open(NGINX_SNIPPET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    if not reload:
        log(f'wrote {NGINX_SNIPPET} (token valid {TOKEN_SECONDS // 86400} days)')
        return True
    test = subprocess.run(['nginx', '-t'], capture_output=True, text=True)
    if test.returncode != 0:
        if had:
            os.replace(prev, NGINX_SNIPPET)
        log(f'nginx -t failed, previous settings kept: {test.stderr.strip()[-300:]}')
        return False
    subprocess.run(['systemctl', 'reload', 'nginx'], check=False)
    log(f'wrote {NGINX_SNIPPET} and reloaded nginx (token valid {TOKEN_SECONDS // 86400} days)')
    return True


def delete_local(db, b2, days, base_url):
    """Remove local MP3s that are safely in B2 and older than `days` (CH-305)."""
    if not fallback_works(base_url):
        log('B2 fallback is not answering through the public link: nothing deleted this run')
        return 0
    cutoff = time.time() - days * 86400
    rows = db.execute('SELECT name, size, sha1, file_id FROM copied WHERE deleted_at IS NULL'
                      ' ORDER BY copied_at').fetchall()
    deleted = freed = 0
    for name, size, sha1, file_id in rows:
        if deleted >= DELETE_PER_RUN:
            break
        path = os.path.join(DIR, name)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            db.execute('UPDATE copied SET deleted_at = ? WHERE name = ?', (int(time.time()), name))
            continue
        if st.st_mtime > cutoff or st.st_size != size:
            continue
        try:
            info = b2.file_info(file_id)
        except B2Error as e:
            log(f'not deleted {name}: B2 did not confirm it ({e})')
            continue
        if int(info.get('contentLength', -1)) != size or info.get('contentSha1') != sha1:
            log(f'not deleted {name}: B2 copy differs')
            continue
        os.remove(path)
        db.execute('UPDATE copied SET deleted_at = ? WHERE name = ?', (int(time.time()), name))
        deleted += 1
        freed += size
        if deleted % 100 == 0:
            db.commit()
    db.commit()
    if deleted:
        log(f'deleted {deleted} local copies older than {days} days ({freed / 1e9:.2f} GB freed); all in B2')
    return deleted


def waiting(db):
    """Finished MP3s not copied yet, oldest first."""
    done = {row[0]: row[1] for row in db.execute('SELECT name, size FROM copied')}
    cutoff = time.time() - MIN_AGE_MIN * 60
    out = []
    with os.scandir(DIR) as it:
        for e in it:
            if not e.is_file() or not e.name.endswith('.mp3'):
                continue
            st = e.stat()
            if st.st_mtime > cutoff or st.st_size == 0:
                continue
            if done.get(e.name) == st.st_size:
                continue
            out.append((st.st_mtime, e.name, st.st_size))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser(description='Copy finished MP3 recordings to Backblaze B2 (CH-304).')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=BATCH)
    ap.add_argument('--verify', type=int, metavar='N', help='download N copied files back and compare')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--nginx', action='store_true', help='write nginx\'s B2 settings, check and reload (CH-305)')
    ap.add_argument('--no-reload', action='store_true', help='with --nginx: write only')
    ap.add_argument('--delete-local-days', type=int, default=0, metavar='N',
                    help='after copying, remove local MP3s older than N days that are safely in B2 (CH-305)')
    a = ap.parse_args()

    if a.status:
        # Read-only; works while a copy run holds the lock (CH-305).
        db = open_state()
        todo = waiting(db)
        n, size = db.execute('SELECT COUNT(*), COALESCE(SUM(size), 0) FROM copied').fetchone()
        gone = db.execute('SELECT COUNT(*) FROM copied WHERE deleted_at IS NOT NULL').fetchone()[0]
        log(f'copied to B2: {n} files, {size / 1e9:.2f} GB (removed from disk: {gone}); '
            f'waiting: {len(todo)} files, {sum(s for _, _, s in todo) / 1e9:.2f} GB')
        return 0

    # The daily --nginx renewal must not be skipped because a copy run is
    # going, so only copying takes the lock.
    lock = open('/tmp/recordings_to_b2.lock', 'w')
    if not a.nginx:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('previous run still going, skipped')
            return 0

    db = open_state()
    db.execute('PRAGMA busy_timeout = 30000')
    todo = waiting(db) if not a.nginx else []
    if a.dry_run:
        n, size = db.execute('SELECT COUNT(*), COALESCE(SUM(size), 0) FROM copied').fetchone()
        log(f'copied to B2: {n} files, {size / 1e9:.2f} GB; waiting: {len(todo)} files, '
            f'{sum(s for _, _, s in todo) / 1e9:.2f} GB')
        for _, name, size in todo[:a.limit]:
            print(f'  would copy {name} ({size} bytes)')
        return 0

    env = read_env(ENV_FILE)
    missing = [k for k in ('B2_KEY_ID', 'B2_APPLICATION_KEY', 'B2_BUCKET') if not env.get(k)]
    if missing:
        log(f'not set in {ENV_FILE}: {", ".join(missing)}')
        return 1
    try:
        b2 = B2(env['B2_KEY_ID'], env['B2_APPLICATION_KEY'], env['B2_BUCKET'])
    except B2Error as e:
        log(f'B2 sign-in failed: {e}')
        return 1

    if a.nginx:
        try:
            ensure_canary(db, b2)
            return 0 if write_nginx(b2, reload=not a.no_reload) else 1
        except B2Error as e:
            log(f'nginx settings not written: {e}')
            return 1

    if a.verify:
        rows = db.execute('SELECT name, size, sha1 FROM copied').fetchall()
        bad = 0
        for name, size, sha1 in random.sample(rows, min(a.verify, len(rows))):
            try:
                data = b2.download(PREFIX + name)
                ok = len(data) == size and hashlib.sha1(data).hexdigest() == sha1
            except B2Error as e:
                ok = False
                log(f'download failed {name}: {e}')
            bad += 0 if ok else 1
            log(f'{"OK " if ok else "BAD"} {name} ({size} bytes)')
        log(f'verified {min(a.verify, len(rows))}, bad {bad}')
        return 1 if bad else 0

    copied = failed = 0
    nbytes = 0
    for _, name, size in todo[:a.limit]:
        path = os.path.join(DIR, name)
        try:
            st = os.stat(path)
            sha1 = sha1_of(path)
            r = b2.upload(path, PREFIX + name, sha1, st.st_size, int(st.st_mtime * 1000))
            if int(r.get('contentLength', -1)) != st.st_size or r.get('contentSha1') != sha1:
                raise B2Error(0, 'B2 reply does not match the file')
        except FileNotFoundError:
            continue
        except B2Error as e:
            failed += 1
            log(f'copy failed {name}: {e}')
            if failed >= 10:
                log('10 failures this run, stopping until the next run')
                break
            continue
        db.execute('INSERT OR REPLACE INTO copied (name, size, sha1, file_id, copied_at)'
                   ' VALUES (?, ?, ?, ?, ?)',
                   (name, st.st_size, sha1, r.get('fileId', ''), int(time.time())))
        db.commit()
        copied += 1
        nbytes += st.st_size
    left = len(todo) - copied
    if copied or failed or left:
        log(f'copied {copied} ({nbytes / 1e6:.1f} MB), failed {failed}, still waiting {left}')
    if a.delete_local_days > 0:
        base = env.get('RECORDING_BASE_URL') or 'https://rec.v0l1.com'
        try:
            delete_local(db, b2, a.delete_local_days, base)
        except B2Error as e:
            log(f'delete step stopped: {e}')
    return 1 if failed and not copied else 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Copy finished call recordings (MP3) to Backblaze B2 (CH-304).

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

ENV_FILE = os.environ.get('ENV_FILE', '/opt/call_platform/.env')
DIR = os.environ.get('RECORDINGS_DIR', '/var/spool/asterisk/recordings')
STATE = os.environ.get('STATE_DB', '/var/lib/avortyx/b2.sqlite3')
AUTH_URL = os.environ.get('B2_AUTH_URL', 'https://api.backblazeb2.com/b2api/v3/b2_authorize_account')
MIN_AGE_MIN = int(os.environ.get('MIN_AGE_MIN', '10'))
BATCH = int(os.environ.get('BATCH', '500'))
PREFIX = 'recordings/'
RETRIES = 3


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

    def upload(self, path, name, sha1, size, mtime_ms):
        last = None
        for attempt in range(RETRIES):
            if self._upload is None:
                self._upload = self._call('b2_get_upload_url', {'bucketId': self.bucket_id})
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
    return db


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
    a = ap.parse_args()

    lock = open('/tmp/recordings_to_b2.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('previous run still going, skipped')
        return 0

    db = open_state()
    todo = waiting(db)
    if a.status or a.dry_run:
        n, size = db.execute('SELECT COUNT(*), COALESCE(SUM(size), 0) FROM copied').fetchone()
        log(f'copied to B2: {n} files, {size / 1e9:.2f} GB; waiting: {len(todo)} files, '
            f'{sum(s for _, _, s in todo) / 1e9:.2f} GB')
        if a.dry_run:
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
        db.execute('INSERT OR REPLACE INTO copied VALUES (?, ?, ?, ?, ?)',
                   (name, st.st_size, sha1, r.get('fileId', ''), int(time.time())))
        db.commit()
        copied += 1
        nbytes += st.st_size
    left = len(todo) - copied
    if copied or failed or left:
        log(f'copied {copied} ({nbytes / 1e6:.1f} MB), failed {failed}, still waiting {left}')
    return 1 if failed and not copied else 0


if __name__ == '__main__':
    sys.exit(main())

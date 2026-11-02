#!/usr/bin/env python3
"""Server A <-> Server B for the fallback routing (CH-464). Runs on Server A
as a service (deploy/avx-fallback-sync.service), as root:

    fallback_sync.py --loop      # the service: the steps below, every 5 seconds
    fallback_sync.py             # the steps once
    fallback_sync.py --status    # plan age, calls waiting for B; changes nothing

Each round:
  1. sends B the calls A routed on its own, in batches; B writes each one with
     A's call id. First, so that B counts them (caps, concurrency) as soon as
     it is back, and so that the plan fetched next already includes them;
  2. lets go of their held hang-up reports (call_ended.sh) and resends them at
     once, so B bills those calls;
  3. once a minute, fetches the routing plan from B into
     /var/lib/avortyx/fallback/plan.sqlite3 (kept as it is when B does not
     answer - that plan is what A routes with);
  4. once an hour, forgets old journal rows: refusals after 35 days, calls
     sent to a line after 400 (the duplicate rules look a year back).

Settings come from /opt/call_platform/.env (ASTERISK_SHARED_SECRET) and the
environment: FALLBACK_B_URL (default http://10.20.0.2:8001).
"""
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avx_fallback as fb  # noqa: E402

B_URL = os.environ.get('FALLBACK_B_URL', 'http://10.20.0.2:8001').rstrip('/')
ENV_FILE = os.environ.get('ENV_FILE', '/opt/call_platform/.env')
CALL_ENDED = os.environ.get('CALL_ENDED_BIN', '/usr/local/bin/call_ended.sh')
ASTERISK_USER = os.environ.get('ASTERISK_USER', 'asterisk')
BATCH = 200
KEEP_REFUSED_DAYS = 35
KEEP_DIALS_DAYS = 400
LOOP_SECONDS = 5
PLAN_EVERY = 60


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


def secret():
    value = os.environ.get('ASTERISK_SHARED_SECRET', '')
    if not value and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if line.startswith('ASTERISK_SHARED_SECRET='):
                value = line.split('=', 1)[1].strip().strip('"').strip("'")
    return value


def call_b(path, body=None, timeout=20):
    req = urllib.request.Request(
        B_URL + path, data=json.dumps(body).encode() if body is not None else None,
        headers={'X-Asterisk-Secret': secret(), 'Content-Type': 'application/json'},
        method='POST' if body is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def ensure_dirs():
    """The folder, the held-report markers and the journal belong to the
    asterisk user: the AGI writes them. Root (this script) can always write."""
    os.makedirs(fb.PENDING_DIR, exist_ok=True)
    fb.Journal().db.close()
    if os.geteuid() != 0:
        return
    try:
        import pwd
        pw = pwd.getpwnam(ASTERISK_USER)
    except KeyError:
        return
    for path in (fb.STATE_DIR, fb.PENDING_DIR, fb.JOURNAL_DB):
        os.chown(path, pw.pw_uid, pw.pw_gid)


def fetch_plan():
    try:
        plan = call_b('/api/twilio/asterisk/fallback-plan/', timeout=60)
    except Exception as e:
        log(f'plan not fetched, keeping the last one: {e}')
        return False
    if plan.get('version') != 1 or 'numbers' not in plan:
        log('plan not understood, keeping the last one')
        return False
    fb.write_plan(plan)
    return True


def push_calls(journal):
    """Send the calls A decided on its own. Returns the ids B now has."""
    done = []
    while True:
        cur = journal.db.execute(
            "SELECT * FROM calls WHERE source = 'a' AND synced = 0 ORDER BY created_at LIMIT ?", (BATCH,))
        names = [c[0] for c in cur.description]
        rows = [dict(zip(names, r)) for r in cur]
        if not rows:
            return done
        calls = []
        for r in rows:
            entry = {k: r[k] for k in (
                'id', 'uniqueid', 'created_at', 'caller', 'called', 'campaign_id', 'organization_id',
                'publisher_id', 'buyer_id', 'rule_id', 'rule_destination_id', 'outcome', 'error',
                'refused_destination', 'refused_buyer_id')}
            entry['destination'] = r['dest']
            entry['auto_block'] = bool(r['auto_block'])
            try:
                entry['trace'] = json.loads(r['trace'] or '{}')
            except ValueError:
                entry['trace'] = {}
            calls.append(entry)
        try:
            results = call_b('/api/twilio/asterisk/fallback-calls/', {'calls': calls}, timeout=120)['results']
        except Exception as e:
            log(f'{len(calls)} call(s) not sent, B did not answer: {e}')
            return done
        accepted = [cid for cid, res in results.items() if res == 'created' or res == 'exists'
                    or str(res).startswith('skipped')]
        for cid, res in results.items():
            if str(res).startswith('skipped'):
                log(f'B skipped call {cid}: {res}')
        if not accepted:
            log(f'B took none of {len(calls)} call(s): {results}')
            return done
        at = time.time()
        journal.db.executemany('UPDATE calls SET synced = 1, synced_at = ? WHERE id = ?',
                               [(at, c) for c in accepted])
        journal.db.commit()
        done += accepted
        if len(accepted) < len(calls):
            return done


def release_reports(ids):
    for cid in ids:
        try:
            os.remove(os.path.join(fb.PENDING_DIR, cid))
        except FileNotFoundError:
            pass
    if ids and os.path.exists(CALL_ENDED):
        cmd = [CALL_ENDED, '--replay']
        if os.geteuid() == 0:
            cmd = ['runuser', '-u', ASTERISK_USER, '--'] + cmd
        subprocess.run(cmd, timeout=600, check=False)


def prune(journal):
    now = time.time()
    journal.db.execute("DELETE FROM calls WHERE synced = 1 AND outcome != 'dial' AND created_at < ?",
                       (now - KEEP_REFUSED_DAYS * 86400,))
    journal.db.execute('DELETE FROM calls WHERE synced = 1 AND created_at < ?', (now - KEEP_DIALS_DAYS * 86400,))
    journal.db.commit()


def status():
    ensure_dirs()
    plan = fb.Plan.open()
    journal = fb.Journal()
    waiting = journal.db.execute("SELECT COUNT(*) FROM calls WHERE source = 'a' AND synced = 0").fetchone()[0]
    routed = journal.db.execute("SELECT COUNT(*) FROM calls WHERE source = 'a'").fetchone()[0]
    held = len(os.listdir(fb.PENDING_DIR)) if os.path.isdir(fb.PENDING_DIR) else 0
    if plan is None:
        print('plan: NONE - A cannot route on its own until B has answered once')
    else:
        n = plan.db.execute('SELECT COUNT(*) FROM numbers').fetchone()[0]
        age = fb.plan_age(plan)
        print(f'plan: {age} s old, {n} numbers')
        if age > 180:
            print('WARNING: the plan is not being refreshed. Is Server B down, or is the sync '
                  'service stopped (systemctl status avx-fallback-sync), or the secret wrong?')
    print(f'calls routed by A on its own (35 days): {routed}; waiting for B: {waiting}; '
          f'hang-up reports held: {held}')


def push_round(journal):
    sent = push_calls(journal)
    if sent:
        log(f'{len(sent)} call(s) routed by A now on B')
        release_reports(sent)
    return sent


def main():
    if '--status' in sys.argv:
        status()
        return 0
    ensure_dirs()
    lock = open(os.path.join(fb.STATE_DIR, '.sync.lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('another sync is running')
        return 0
    journal = fb.Journal()
    if '--loop' not in sys.argv:
        push_round(journal)
        fetched = fetch_plan()
        prune(journal)
        return 0 if fetched else 1
    last_plan = last_prune = 0.0
    failing_since = None
    while True:
        try:
            push_round(journal)
            now = time.time()
            if now - last_plan >= PLAN_EVERY:
                last_plan = now
                if fetch_plan():
                    if failing_since:
                        log(f'plan fetched again after {int(now - failing_since)} s')
                    failing_since = None
                elif failing_since is None:
                    failing_since = now
            if now - last_prune >= 3600:
                last_prune = now
                prune(journal)
        except Exception as e:
            log(f'sync round failed: {e!r}')
        time.sleep(LOOP_SECONDS)


if __name__ == '__main__':
    sys.exit(main())

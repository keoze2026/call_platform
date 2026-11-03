#!/usr/bin/env python3
"""Change the shared secret between Server A and Server B (CH-473).

Every call Server A routes, every hang-up report, the backup routing's sync,
the channel sync, the host report and the cancel-hold relay's held-answer
report carry ASTERISK_SHARED_SECRET. It is still the one from the old server.
This changes it without a single call being refused. The new secret is
generated here, from a secure random source, and is never printed. It
travels from B to A in a root-only file.

  Server B   python3 scripts/rotate_asterisk_secret.py stage
               new secret; B accepts the old and the new one; the new one is
               written to /root/asterisk_secret.new for Server A
  PC         scp avx-web:/root/asterisk_secret.new .   then   scp asterisk_secret.new avx-calls:/root/
  Server A   python3 scripts/rotate_asterisk_secret.py apply /root/asterisk_secret.new
               the routing script, hang-up reports and syncs use the new one at once
  Server A   python3 scripts/rotate_asterisk_secret.py kamailio
               the cancel-hold relay too; it restarts Kamailio only while no
               call is up, otherwise it says so and changes nothing live
  Server B   python3 scripts/rotate_asterisk_secret.py finish
               B stops accepting the old secret. Refused while anything used
               it in the last 15 minutes
  either     python3 scripts/rotate_asterisk_secret.py status

Every file is backed up under /root before it is changed.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = os.environ.get('APP_DIR', '/opt/call_platform')
ENV = os.path.join(APP, '.env')
NEW_FILE = '/root/asterisk_secret.new'
SECRET_FILE = os.environ.get('AVX_SECRET_FILE', '/etc/asterisk/call_ended.secret')
KAMAILIO_CFG = os.environ.get('KAMAILIO_CFG', '/etc/kamailio/kamailio.cfg')
B_ROUTE = 'http://10.20.0.2:8001/api/twilio/asterisk/route/'
HELD_URL_B = 'http://10.20.0.2:8001/api/twilio/asterisk/held-answer/'
QUIET_MINUTES = 15
KEY = 'ASTERISK_SHARED_SECRET'
PREVIOUS = 'ASTERISK_SHARED_SECRET_PREVIOUS'
GOOD = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


class Refused(Exception):
    pass


# ── pure helpers (tested) ────────────────────────────────────────────────────

def new_secret():
    return secrets.token_urlsafe(32)


def env_get(text, key):
    for line in text.splitlines():
        if line.startswith(key + '='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    return ''


def env_set(text, values):
    """Sets (or, with None, removes) keys in .env text; every other line,
    comment and blank line stays as it was."""
    out, done = [], set()
    for line in text.splitlines():
        key = line.split('=', 1)[0] if '=' in line and not line.lstrip().startswith('#') else None
        if key in values:
            done.add(key)
            if values[key] is not None:
                out.append(f'{key}={values[key]}')
            continue
        out.append(line)
    for key, value in values.items():
        if key not in done and value is not None:
            out.append(f'{key}={value}')
    return '\n'.join(out) + '\n'


SECRET_LINE = re.compile(r'^(#!substdef "!SECRET!)([^!\n]*)(!g")$', re.M)
HELD_LINE = re.compile(r'^(#!substdef "!HELD_URL!)([^!\n]*)(!g")$', re.M)


def kamailio_set(cfg, secret):
    """The relay's config with the new secret. A held-answer address on this
    server itself (127.0.0.1 / localhost, from the old one-server setup) is
    pointed at Server B, where the endpoint is. Returns (cfg, notes)."""
    if len(SECRET_LINE.findall(cfg)) != 1:
        raise Refused('the relay config does not have exactly one SECRET line: not changed')
    cfg = SECRET_LINE.sub(lambda m: m.group(1) + secret + m.group(3), cfg)
    notes = []
    m = HELD_LINE.search(cfg)
    if m and re.search(r'//(127\.0\.0\.1|localhost)[:/]', m.group(2)):
        cfg = HELD_LINE.sub(lambda x: x.group(1) + HELD_URL_B + x.group(3), cfg)
        notes.append(f'held-answer address {m.group(2)} -> {HELD_URL_B} (Server B)')
    elif m:
        notes.append(f'held-answer address {m.group(2)}')
    return cfg, notes


# ── servers ──────────────────────────────────────────────────────────────────

def sh(cmd, check=True, **kw):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise Refused(f'{cmd if isinstance(cmd, str) else " ".join(cmd)} failed: {(r.stderr or r.stdout)[-300:]}')
    return r.stdout


def which_server():
    addrs = sh('ip -4 -brief addr', check=False)
    if '10.20.0.2/' in addrs:
        return 'B'
    if '10.20.0.1/' in addrs:
        return 'A'
    return '?'


def backup(path):
    if os.path.exists(path):
        dst = f'/root/{os.path.basename(path).lstrip(".")}.before-secret-change.{time.strftime("%Y%m%d-%H%M%S")}'
        sh(['cp', '-a', path, dst])
        os.chmod(dst, 0o600)
        return dst
    return None


def write_keep_mode(path, text):
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o600
    tmp = path + '.new-secret'
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), 'w') as f:
        f.write(text)
    if os.path.exists(path):
        st = os.stat(path)
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)


def read(path):
    with open(path) as f:
        return f.read()


def recreate_web():
    """compose reads .env when a container is created, not on restart."""
    sh(f'cd {APP} && docker compose up -d --no-deps web_calls web')
    for _ in range(30):
        codes = [sh(f"curl -s -o /dev/null -w '%{{http_code}}' -X POST http://127.0.0.1:{p}/api/twilio/asterisk/route/",
                    check=False) for p in (8001, 8000)]
        if codes == ['403', '403']:
            return
        time.sleep(2)
    raise Refused('web_calls / web did not answer after the change: check `docker compose ps`')


def previous_last_used():
    out = sh(f'cd {APP} && docker compose exec -T web python manage.py shell -c '
             '"from django.core.cache import cache; print(cache.get(\'asterisk_secret_previous_used\') or 0)"')
    return float(out.strip().splitlines()[-1] or 0)


def b_accepts(secret):
    """True when Server B takes this secret: an empty request is then refused
    for its missing fields (400), not for the secret (403)."""
    req = urllib.request.Request(B_ROUTE, data=b'{}', method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Asterisk-Secret': secret})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        return e.code == 400
    except Exception:  # noqa: BLE001
        return False


# ── steps ────────────────────────────────────────────────────────────────────

def stage():
    text = read(ENV)
    current = env_get(text, KEY)
    if not current:
        raise Refused(f'{KEY} is not set in {ENV}')
    if env_get(text, PREVIOUS):
        raise Refused('a change is already under way: finish it first ("finish")')
    secret = new_secret()
    print(f'.env backed up to {backup(ENV)}')
    write_keep_mode(ENV, env_set(text, {KEY: secret, PREVIOUS: current}))
    with open(os.open(NEW_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        f.write(secret + '\n')
    print('new secret generated (not shown); Server B now accepts the old and the new one')
    recreate_web()
    print(f'routing and portal answering again. For Server A: {NEW_FILE} (root only).')


def apply(path):
    secret = read(path).strip()
    if not GOOD.match(secret):
        raise Refused(f'{path} does not hold a secret made by "stage"')
    if not b_accepts(secret):
        raise Refused('Server B does not accept this secret yet: run "stage" on Server B first')
    for p in (ENV, SECRET_FILE):
        print(f'{p} backed up to {backup(p)}')
    existed = os.path.exists(SECRET_FILE)
    write_keep_mode(SECRET_FILE, secret + '\n')
    if not existed:     # the routing script runs as the asterisk user
        sh(['chown', 'asterisk:asterisk', SECRET_FILE], check=False)
    write_keep_mode(ENV, env_set(read(ENV), {KEY: secret}))
    os.remove(path)
    print('Server A now uses the new secret: routing, hang-up reports, backup sync, channel sync, host report.')
    print('The cancel-hold relay still uses the old one until: rotate_asterisk_secret.py kamailio')


def kamailio(now=False):
    secret = env_get(read(ENV), KEY)
    if not GOOD.match(secret or ''):
        raise Refused('run "apply" on Server A first')
    cfg, notes = kamailio_set(read(KAMAILIO_CFG), secret)
    trial = KAMAILIO_CFG + '.check'
    write_keep_mode(trial, cfg)
    try:
        sh(['kamailio', '-c', '-f', trial])
    finally:
        os.remove(trial)
    print(f'{KAMAILIO_CFG} backed up to {backup(KAMAILIO_CFG)}')
    write_keep_mode(KAMAILIO_CFG, cfg)
    for n in notes:
        print(n)
    m = re.search(r'(\d+) active call', sh('asterisk -rx "core show channels count"', check=False))
    live = int(m.group(1)) if m else -1
    if live != 0 and not now:
        print(f'{live} call(s) up: the relay config is ready but Kamailio was NOT restarted. '
              'Run this again when no call is up.')
        return
    sh('systemctl restart kamailio')
    time.sleep(2)
    if sh('systemctl is-active kamailio', check=False).strip() != 'active':
        raise Refused('Kamailio did not start: the backup is in /root; check `journalctl -u kamailio -n 30`')
    print('Kamailio restarted with the new secret.')


def finish():
    text = read(ENV)
    if not env_get(text, PREVIOUS):
        raise Refused('no change under way')
    last = previous_last_used()
    if last and time.time() - last < QUIET_MINUTES * 60:
        raise Refused(f'the old secret was still used {int((time.time() - last) / 60)} min ago (Server A or its '
                      f'relay): wait until nothing has used it for {QUIET_MINUTES} minutes')
    print(f'.env backed up to {backup(ENV)}')
    write_keep_mode(ENV, env_set(text, {PREVIOUS: None}))
    recreate_web()
    if os.path.exists(NEW_FILE):
        os.remove(NEW_FILE)
    print('done: only the new secret is accepted.')


def status(server):
    text = read(ENV) if os.path.exists(ENV) else ''
    out = {'server': server, 'secret_set': bool(env_get(text, KEY))}
    if server == 'B':
        out['change_under_way'] = bool(env_get(text, PREVIOUS))
        try:
            last = previous_last_used()
            out['old_secret_last_used'] = time.strftime('%H:%M:%S UTC', time.gmtime(last)) if last else 'never'
        except Refused:
            pass
        out['file_for_server_a'] = os.path.exists(NEW_FILE)
    if server == 'A':
        secret = env_get(text, KEY)
        out['call_ended_file_matches_env'] = os.path.exists(SECRET_FILE) and read(SECRET_FILE).strip() == secret
        out['server_b_accepts_it'] = b_accepts(secret) if secret else False
        if os.path.exists(KAMAILIO_CFG):
            m = SECRET_LINE.search(read(KAMAILIO_CFG))
            out['kamailio_config_matches_env'] = bool(m and m.group(2) == secret)
    print(json.dumps(out, indent=1))


def main(argv):
    if os.geteuid() != 0:
        raise Refused('run as root')
    step = argv[1] if len(argv) > 1 else ''
    server = which_server()
    need = {'stage': 'B', 'finish': 'B', 'apply': 'A', 'kamailio': 'A'}
    if step in need and need[step] != server:
        raise Refused(f'"{step}" runs on Server {need[step]}; this is Server {server}')
    if step == 'stage':
        stage()
    elif step == 'apply' and len(argv) > 2:
        apply(argv[2])
    elif step == 'kamailio':
        kamailio(now='--now' in argv)
    elif step == 'finish':
        finish()
    elif step == 'status':
        status(server)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Refused as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        sys.exit(2)

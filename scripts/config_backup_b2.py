#!/usr/bin/env python3
"""Daily backup of a server's own settings, copied to Backblaze B2 (CH-470).

The database is backed up every night (db_backup_b2.py) and recordings live
in B2, but the servers' settings were nowhere else. Since the move, Server A
is the only server the carriers send calls to. If it were lost, its phone
system would have to be set up again from memory. Every day this:

  1. packs the settings that exist on this server into one file (see PATHS):
     - Asterisk, Kamailio, nginx and SSL certificates;
     - WireGuard, firewall, Fail2Ban and SSH;
     - systemd units and overrides;
     - the routing scripts and the fallback code;
     - .env, docker-compose.override.yml, crontabs, root's small scripts;
     - Asterisk's own database (cancel-hold and other keys), copied safely
       while Asterisk runs;
     - rebuild-info.txt: versions, packages, firewall rules, IPs, enabled
       services, crontabs;
  2. reads the file back to check it is complete;
  3. uploads it to B2 as backups/config/<server>-<UTC time>.tar.gz, with B2
     checking the SHA-1. It sits in the same private bucket as the database
     backups. The recordings link can only reach recordings/, never backups/;
  4. keeps the last 14 on this server (/var/backups/avortyx/config);
  5. writes /var/lib/avortyx/config_backup_last.json. The watchdog warns
     when no good backup is newer than 36 hours.

Runs on the HOST of either server, as root. Python standard library only,
uses recordings_to_b2.py for B2. Reads only; restarts nothing.

    python3 scripts/config_backup_b2.py                 # back up now
    python3 scripts/config_backup_b2.py --status        # last backup
    python3 scripts/config_backup_b2.py --list          # backups in B2
    python3 scripts/config_backup_b2.py --fetch NAME    # download one from B2
    cron: 40 4 * * * python3 /opt/call_platform/scripts/config_backup_b2.py >> /var/log/config_backup.log 2>&1

Rebuilding a server from it: docs/SERVER-REBUILD.md.
"""
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recordings_to_b2 as R  # noqa: E402

OUT = os.environ.get('CONFIG_BACKUP_DIR', '/var/backups/avortyx/config')
STATE = os.environ.get('CONFIG_BACKUP_STATE', '/var/lib/avortyx/config_backup_last.json')
KEEP = int(os.environ.get('CONFIG_BACKUP_KEEP', '14'))
PREFIX = 'backups/config/'
ROOT_FILE_MAX = 5 * 1024 * 1024   # root's own scripts and notes, not dumps

# Everything that makes the server what it is. A path that does not exist on
# this server is skipped, so one list serves both Server A and Server B.
PATHS = [
    '/etc/asterisk', '/etc/kamailio', '/etc/nginx', '/etc/letsencrypt',
    '/etc/wireguard', '/etc/ufw', '/etc/fail2ban', '/etc/ssh/sshd_config.d',
    '/etc/systemd/system', '/etc/hosts', '/etc/hostname', '/etc/netplan',
    '/etc/sysctl.d', '/etc/security/limits.d', '/etc/logrotate.d', '/etc/docker/daemon.json',
    '/usr/share/asterisk/agi-bin', '/var/lib/asterisk/agi-bin',
    '/usr/local/bin', '/usr/local/lib/avortyx',
    '/opt/call_platform/.env', '/opt/call_platform/docker-compose.override.yml',
    '/root/.ssh', '/var/spool/cron/crontabs',
]
ROOT_HOME = '/root'
ASTDB = '/var/lib/asterisk/astdb.sqlite3'

# What a rebuild needs to know that is not in a file: versions, packages,
# firewall rules, addresses, services. Each command's failure is written in.
INFO = [
    ('server', ['hostname']),
    ('system', ['sh', '-c', 'head -2 /etc/os-release; uname -r; nproc; free -g | head -2; df -h /']),
    ('addresses', ['ip', '-brief', 'addr']),
    ('asterisk version', ['asterisk', '-V']),
    ('kamailio version', ['sh', '-c', 'kamailio -v | head -1']),
    ('asterisk keys (database show)', ['asterisk', '-rx', 'database show']),
    ('pjsip endpoints', ['asterisk', '-rx', 'pjsip show endpoints']),
    ('firewall', ['ufw', 'status', 'numbered']),
    ('enabled services', ['systemctl', 'list-unit-files', '--state=enabled', '--no-pager']),
    ('root crontab', ['crontab', '-l', '-u', 'root']),
    ('asterisk crontab', ['crontab', '-l', '-u', 'asterisk']),
    ('docker containers', ['docker', 'ps', '--format', '{{.Names}}  {{.Image}}  {{.Status}}']),
    ('packages', ['dpkg-query', '-W', '-f', '${Package} ${Version}\n']),
]


def log(msg):
    print(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {msg}', flush=True)


def save_state(**kw):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    kw['at'] = int(time.time())
    old = {}
    if os.path.exists(STATE):
        try:
            with open(STATE) as f:
                old = json.load(f)
        except ValueError:
            old = {}
    if kw.get('ok'):
        old['last_ok'] = kw
    old['last_run'] = kw
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(old, f, indent=1)
    os.replace(tmp, STATE)


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = (p.stdout or '') + (p.stderr or '')
        return out if p.returncode == 0 else f'(exit {p.returncode}) {out}'
    except Exception as e:  # noqa: BLE001 - a missing tool is noted, not fatal
        return f'(not run: {e})'


def rebuild_info():
    parts = [f'Settings backup of {socket.gethostname()} at {time.strftime("%Y-%m-%d %H:%M:%S %Z")}\n']
    for title, cmd in INFO:
        parts.append(f'\n===== {title} =====\n{run(cmd).rstrip()}\n')
    return ''.join(parts).encode()


def root_files():
    """root's own small files (scripts, notes, crontab drafts); never dumps."""
    try:
        names = sorted(os.listdir(ROOT_HOME))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(ROOT_HOME, n)
        if os.path.isfile(p) and not os.path.islink(p) and os.path.getsize(p) <= ROOT_FILE_MAX \
                and not n.endswith(('.gz', '.sql', '.dump', '.tar', '.zip', '.pcap')):
            out.append(p)
    return out


def astdb_copy(tmpdir):
    """A consistent copy of Asterisk's database while Asterisk keeps writing."""
    if not os.path.exists(ASTDB):
        return None
    dst = os.path.join(tmpdir, 'astdb.sqlite3')
    src = sqlite3.connect(f'file:{ASTDB}?mode=ro', uri=True, timeout=10)
    try:
        with sqlite3.connect(dst) as d:
            src.backup(d)
    finally:
        src.close()
    return dst


def build(path):
    """Writes the archive. Returns (files packed, paths skipped)."""
    skipped = []
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(path + '.part', 'w:gz') as tar:
        for p in PATHS + root_files():
            if not os.path.lexists(p):
                skipped.append(p)
                continue
            tar.add(p, arcname=p.lstrip('/'), recursive=True,
                    filter=lambda t: t if (t.isfile() or t.isdir() or t.issym() or t.islnk()) else None)
        db = astdb_copy(tmp)
        if db:
            tar.add(db, arcname=ASTDB.lstrip('/'))
        data = rebuild_info()
        info = tarfile.TarInfo('rebuild-info.txt')
        info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
        tar.addfile(info, io.BytesIO(data))
    # Read the whole archive back: a damaged file fails here, not on rebuild day.
    with tarfile.open(path + '.part', 'r:gz') as tar:
        members = tar.getmembers()
        for m in members:
            if m.isfile():
                f = tar.extractfile(m)
                while f.read(1 << 20):
                    pass
    os.chmod(path + '.part', 0o600)
    os.replace(path + '.part', path)
    return sum(1 for m in members if m.isfile()), skipped


def prune():
    files = sorted(f for f in os.listdir(OUT) if f.endswith('.tar.gz'))
    for f in files[:-KEEP]:
        os.remove(os.path.join(OUT, f))


def b2_client():
    env = R.read_env(R.ENV_FILE)
    return R.B2(env['B2_KEY_ID'], env['B2_APPLICATION_KEY'], env['B2_BUCKET'])


def list_backups(b2):
    r = b2._call('b2_list_file_names', {'bucketId': b2.bucket_id, 'prefix': PREFIX, 'maxFileCount': 1000})
    return [(f['fileName'], f['contentLength'], f.get('uploadTimestamp', 0)) for f in r.get('files', [])]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--status' in argv:
        if os.path.exists(STATE):
            with open(STATE) as f:
                print(f.read())
        else:
            print('no settings backup yet')
        return 0
    if '--list' in argv:
        for name, size, ts in list_backups(b2_client()):
            print(f'{time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts / 1000))} UTC  {size / 1e6:6.1f} MB  {name}')
        return 0
    if '--fetch' in argv:
        name = argv[argv.index('--fetch') + 1]
        name = name if name.startswith(PREFIX) else PREFIX + name
        dest = os.path.join(os.getcwd(), os.path.basename(name))
        data = b2_client().download(name)
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        print(f'{dest} ({len(data) / 1e6:.1f} MB)')
        return 0

    os.makedirs(OUT, mode=0o700, exist_ok=True)
    host = socket.gethostname()
    name = f'{host}-{time.strftime("%Y%m%d-%H%M", time.gmtime())}.tar.gz'
    path = os.path.join(OUT, name)
    try:
        count, skipped = build(path)
        size = os.path.getsize(path)
        log(f'packed {count} files -> {name} ({size / 1e6:.1f} MB); not on this server: {len(skipped)}')
        b2 = b2_client()
        sha1 = R.sha1_of(path)
        r = b2.upload(path, PREFIX + name, sha1, size, int(time.time() * 1000), content_type='application/gzip')
        if int(r.get('contentLength', -1)) != size or r.get('contentSha1') != sha1:
            raise RuntimeError('B2 reply does not match the file')
        prune()
        save_state(ok=True, name=name, size=size, files=count, b2=PREFIX + name)
        log(f'settings backup OK: {name} in B2, last {KEEP} kept on the server')
        return 0
    except Exception as e:  # noqa: BLE001 - every failure is recorded for the watchdog
        if os.path.exists(path + '.part'):
            os.remove(path + '.part')
        save_state(ok=False, name=name, error=str(e)[:300])
        log(f'settings backup FAILED: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

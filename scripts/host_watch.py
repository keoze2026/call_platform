#!/usr/bin/env python3
"""The server watching itself (CH-327; Sir, 28 Oct): section "0. Server" of
the status report and the instant alerts.

Run every minute by scripts/host_status.py (no cron line of its own). Each run
compares the server with how it was a minute ago and sends what changed to
the status chat at once: SSH logins, sudo, deploys, config and .env changes,
software installed or removed, new users or SSH keys, firewall and cron
changes, system files, services restarted or down, unknown processes and
outbound connections, open ports, database dumps, bulk recording downloads,
logs cleared, the server rebooting, its IP on a blacklist.

Read-only: it changes nothing on the server and restarts nothing. It keeps
only fingerprints (sha256) of what it watches, never the content: the .env
values in particular are hashed, and an alert names the key, never its value.

The first run only takes the picture (no alerts), so switching it on does not
send a flood. To look without sending or saving: python3 scripts/host_watch.py --look
After a planned change (a port opened on purpose):
python3 scripts/host_watch.py --accept
Critical conditions that are still true (a service down, a new port open, an
unknown process, a dump running, the IP blacklisted) are sent again every 15
minutes until they are gone, then "Resolved".
To start again from nothing: rm /var/lib/avortyx/host_watch_state.json

What it cannot see, and says so in the report instead of showing green:
  - the server being unreachable: if the server is down it cannot send
    anything; that needs an outside monitor
  - the Contabo account (panel logins, 2FA, invoices, snapshots)
  - who ran a command as root: root works without sudo, and nothing records
    root's commands unless auditd is installed
"""
import fcntl
import glob
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request

APP = os.environ.get('APP_DIR', '/opt/call_platform')
STATE = os.environ.get('HOST_WATCH_STATE', '/var/lib/avortyx/host_watch_state.json')
LOCK = STATE + '.lock'
ENV_FILE = os.path.join(APP, '.env')
OUR_IPS = tuple(os.environ.get('OUR_SIP_IPS', '156.67.25.167').split(','))
SERVER_LABEL = os.environ.get('SERVER_LABEL', 'Contabo VPS')
DAY = 86400
# One IP fetching more recordings than this in an hour is a bulk download.
BULK_PER_HOUR = int(os.environ.get('BULK_DOWNLOAD_PER_HOUR', '200'))

# Files whose change is worth an alert. Content is never stored, only a hash.
CONFIG_GLOBS = [
    '/etc/asterisk/*.conf', '/etc/kamailio/*', '/etc/nginx/nginx.conf',
    '/etc/nginx/sites-enabled/*', '/etc/nginx/conf.d/*', '/etc/ssh/sshd_config',
    '/etc/ssh/sshd_config.d/*', '/etc/fail2ban/jail.local', '/etc/fail2ban/jail.d/*',
    '/etc/sudoers', '/etc/sudoers.d/*', '/etc/hosts', '/etc/sysctl.conf',
    os.path.join(APP, 'docker-compose.yml'),
]
# Programs an intruder replaces to hide; a change outside a package update is red.
SYSTEM_FILES = ['/usr/sbin/sshd', '/usr/bin/sudo', '/usr/bin/su', '/usr/bin/passwd', '/usr/bin/login',
                '/usr/bin/ssh', '/usr/bin/bash', '/usr/bin/crontab', '/usr/bin/ps', '/usr/bin/ss',
                '/usr/bin/top', '/usr/bin/ls', '/usr/bin/netstat', '/usr/sbin/useradd']
WATCHED_LOGS = ['/var/log/auth.log', '/var/log/syslog', '/var/log/asterisk/messages',
                '/var/log/asterisk/cdr-csv/Master.csv', '/var/log/nginx/access.log', '/var/log/ufw.log']
SYSTEMD = [('asterisk', 'SIP engine'), ('kamailio', 'Cancel-hold relay'), ('nginx', 'Web server'),
           ('docker', 'Docker'), ('fail2ban', 'fail2ban'), ('cron', 'Cron jobs')]
CONTAINERS = [('web', 'Backend API'), ('web_calls', 'Call routing API'), ('postgres', 'Database'), ('redis', 'Redis/queue'),
              ('celery_worker', 'Worker'), ('celery_beat', 'Scheduler')]
COMPOSE_PROJECT = os.environ.get('COMPOSE_PROJECT', 'call_platform')
# Programs expected to use CPU or to open connections out of this server.
KNOWN_PROGRAMS = {
    'asterisk', 'kamailio', 'python', 'python3', 'daphne', 'uvicorn', 'celery', 'postgres', 'redis-server', 'redis-serve',
    'tcpdump', 'node', 'ffmpeg', 'lame', 'sox', 'dockerd', 'containerd', 'containerd-shim', 'containerd-shim-runc-v2',
    'docker-proxy', 'docker', 'nginx', 'sshd', 'ssh', 'gzip', 'xz', 'tar', 'pg_dump', 'rclone', 'b2', 'apt', 'apt-get',
    'http', 'https', 'dpkg', 'unattended-upgr', 'fail2ban-server', 'f2b/server', 'journald', 'systemd',
    'systemd-journal', 'systemd-resolve', 'systemd-timesyn', 'chronyd', 'ntpd', 'rsync', 'bash', 'sh', 'curl',
    'wget', 'git', 'snapd', 'kworker', 'cron', 'tshark', 'dumpcap', 'voipmonitor', 'kamcmd', 'sngrep',
}
PORT_RTP_FROM = 10000   # Asterisk opens a UDP port per live call above this
EVENT_KINDS = ('ssh', 'sudo', 'deploy', 'config', 'env', 'software', 'users', 'firewall', 'cron', 'sysfile',
               'restart', 'down', 'process', 'port', 'outbound', 'dump', 'download', 'logs', 'reboot',
               'blacklist', 'backup')


def sh(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ''


def digest(data) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()[:24]


def file_digest(path):
    try:
        with open(path, 'rb') as f:
            return digest(f.read())
    except OSError:
        return None


def hhmm(ts=None) -> str:
    return time.strftime('%H:%M UTC', time.gmtime(ts or time.time()))


def public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.strip('[]'))
    except ValueError:
        return False
    return a.is_global


class Run:
    """One minute's look: the saved state, the events found, the report."""

    def __init__(self, state):
        self.state = state
        self.first = not state.get('at')
        self.events = []        # (kind, red, text)
        self.ongoing = {}       # key -> text: critical conditions still true now
        self.report = {}
        self.now = time.time()

    def still(self, key, text):
        """A critical condition that is true right now. Sent again every
        15 minutes until it is gone (Sir's spec), then "Resolved"."""
        self.ongoing[key] = text

    def event(self, kind, text, red=False):
        self.events.append((kind, red, text))

    def diff_sets(self, key, current: dict):
        """{name: fingerprint} against last time -> (added, removed, changed)."""
        before = self.state.get(key)
        self.state[key] = current
        if before is None:
            return [], [], []
        added = sorted(k for k in current if k not in before)
        removed = sorted(k for k in before if k not in current)
        changed = sorted(k for k in current if k in before and before[k] != current[k])
        return added, removed, changed


# ── the server itself ────────────────────────────────────────────────────────

def check_uptime(r):
    up = float(open('/proc/uptime').read().split()[0])
    boot = int(r.now - up)
    prev = r.state.get('boot')
    if prev and abs(boot - prev) > 120:
        r.event('reboot', f'⚠️ <b>Unexpected reboot</b>: up again since {hhmm(boot)} '
                          f'(was up {(r.now - prev) / DAY:.1f} days)')
    r.state['boot'] = boot
    r.report['uptime_days'] = round(up / DAY, 1)
    r.report['boot_at'] = boot


def _cpu_times():
    f = open('/proc/stat').readline().split()[1:]
    return [int(x) for x in f]


def check_cpu(r):
    cur = _cpu_times()
    prev = r.state.get('cpu')
    r.state['cpu'] = cur
    if prev and len(prev) == len(cur):
        d = [c - p for c, p in zip(cur, prev)]
        total = sum(d) or 1
        # user nice system idle iowait irq softirq steal ...
        r.report['iowait_pct'] = round(d[4] * 100 / total, 1)
        r.report['steal_pct'] = round(d[7] * 100 / total, 1) if len(d) > 7 else 0
    mem = dict(line.split(':', 1) for line in open('/proc/meminfo'))
    kb = lambda k: int(mem.get(k, '0 kB').split()[0])  # noqa: E731
    total = kb('SwapTotal')
    r.report['swap_used_pct'] = round((total - kb('SwapFree')) * 100 / total, 1) if total else 0
    r.report['swap_total_mb'] = total // 1024


def _default_iface():
    m = re.search(r'dev (\S+)', sh('ip route get 1.1.1.1 2>/dev/null'))
    return m.group(1) if m else os.environ.get('NET_IFACE', 'eth0')


def check_network(r):
    iface = _default_iface()
    for line in open('/proc/net/dev'):
        if line.strip().startswith(iface + ':'):
            f = line.split(':', 1)[1].split()
            cur = {'t': r.now, 'rx': int(f[0]), 'rx_err': int(f[2]) + int(f[3]),
                   'tx': int(f[8]), 'tx_err': int(f[10]) + int(f[11])}
            break
    else:
        return
    prev = r.state.get('net')
    r.state['net'] = cur
    r.report['iface'] = iface
    if prev and cur['t'] > prev['t'] and cur['rx'] >= prev['rx']:
        secs = cur['t'] - prev['t']
        r.report['rx_mbps'] = round((cur['rx'] - prev['rx']) * 8 / secs / 1e6, 1)
        r.report['tx_mbps'] = round((cur['tx'] - prev['tx']) * 8 / secs / 1e6, 1)
        r.report['net_errors'] = (cur['rx_err'] - prev['rx_err']) + (cur['tx_err'] - prev['tx_err'])


def check_time(r):
    synced = sh('timedatectl show -p NTPSynchronized --value 2>/dev/null').strip()
    r.report['ntp_synced'] = synced == 'yes' if synced else None
    offset = None
    m = re.search(r'System time\s*:\s*([\d.]+) seconds (fast|slow)', sh('chronyc tracking 2>/dev/null'))
    if m:
        offset = float(m.group(1)) * 1000
    else:
        m = re.search(r'Offset:\s*([+-]?[\d.]+)(us|ms|s)', sh('timedatectl timesync-status 2>/dev/null'))
        if m:
            offset = abs(float(m.group(1))) * {'us': 0.001, 'ms': 1, 's': 1000}[m.group(2)]
    r.report['ntp_offset_ms'] = round(offset, 1) if offset is not None else None


def check_blacklist(r):
    """Our IP on the public blacklists, once an hour (DNS lookups)."""
    last = r.state.get('bl') or {}
    if r.now - last.get('at', 0) < 3600:
        r.report['blacklist'] = last
        for z in last.get('listed') or []:
            r.still(f'bl:{z}', f'🔴 <b>Server IP blacklisted</b>: {last.get("ip")} on {z}')
        return
    ip = OUR_IPS[0]
    rev = '.'.join(reversed(ip.split('.')))
    listed, checked = [], 0
    for zone in ('zen.spamhaus.org', 'b.barracudacentral.org', 'bl.spamcop.net', 'dnsbl-1.uceprotect.net'):
        out = sh(f'getent hosts {rev}.{zone}', timeout=5).split()
        checked += 1
        # 127.255.255.x is the list refusing the query (public resolver), not a listing.
        if out and out[0].startswith('127.') and not out[0].startswith('127.255.255.'):
            listed.append(zone)
    now = {'at': r.now, 'ip': ip, 'listed': listed, 'checked': checked}
    for z in listed:
        r.still(f'bl:{z}', f'🔴 <b>Server IP blacklisted</b>: {ip} on {z}')
    new = [z for z in listed if z not in (last.get('listed') or [])]
    if new:
        r.event('blacklist', f'🔴 <b>Server IP blacklisted</b>: {ip} on {", ".join(new)}', red=True)
    r.state['bl'] = now
    r.report['blacklist'] = now


# ── services ─────────────────────────────────────────────────────────────────

def _container_info():
    names = sh(f"docker ps -a --filter label=com.docker.compose.project={COMPOSE_PROJECT} -q").split()
    if not names:
        return {}
    out = {}
    fmt = '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}'
    for line in sh(f"docker inspect -f '{fmt}' {' '.join(names)}").splitlines():
        parts = line.strip().split('|')
        if len(parts) == 4:
            out[parts[0]] = {'state': parts[1], 'started': parts[2][:19], 'restarts': int(parts[3] or 0)}
    return out


def check_services(r):
    seen = r.state.get('svc') or {}
    now = {}
    rows = []
    for unit, label in SYSTEMD:
        state = sh(f'systemctl is-active {unit}').strip() or 'unknown'
        if state in ('unknown', 'inactive') and not sh(f'systemctl cat {unit} 2>/dev/null').strip():
            continue   # not installed here
        started = sh(f'systemctl show -p ActiveEnterTimestamp --value {unit}').strip()
        now[unit] = {'label': label, 'ok': state == 'active', 'started': started}
    for svc, info in _container_info().items():
        label = dict(CONTAINERS).get(svc, svc)
        now[f'docker:{svc}'] = {'label': label, 'ok': info['state'] == 'running', 'started': info['started']}
    cap = bool(sh("pgrep -f '[t]cpdump.*sipcap'").strip())
    now['capture'] = {'label': 'Packet capture', 'ok': cap, 'started': ''}
    for key, cur in now.items():
        before = seen.get(key)
        if before:
            if not cur['ok']:
                r.still(f'svc:{key}', f'🔴 <b>Service down</b>: {cur["label"]}')
            if before['ok'] and not cur['ok']:
                r.event('down', f'🔴 <b>Service down</b>: {cur["label"]} stopped ({hhmm()})', red=True)
            elif not before['ok'] and cur['ok']:
                r.event('down', f'✅ <b>Service running again</b>: {cur["label"]} ({hhmm()})')
            elif cur['ok'] and cur['started'] and before.get('started') and cur['started'] != before['started']:
                r.event('restart', f'⚠️ <b>Service restarted</b>: {cur["label"]} at {hhmm()} · now running')
        rows.append({'label': cur['label'], 'ok': cur['ok']})
    r.state['svc'] = now
    r.report['services'] = rows


def check_ports(r):
    """Listening ports open to the outside, against the ones seen on day one."""
    ports = {}
    for line in sh('ss -tulnpH').splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        proto, local = f[0], f[4]
        host, _, port = local.rpartition(':')
        host = host.strip('[]').split('%')[0]
        if host.startswith('127.') or host == '::1':
            continue
        prog = re.search(r'users:\(\("([^"]+)"', line)
        prog = prog.group(1) if prog else '?'
        if proto == 'udp' and port.isdigit() and (
                int(port) >= 32768 or (prog == 'asterisk' and int(port) >= PORT_RTP_FROM)):
            continue   # a live call's audio port, or a short-lived client socket
        ports[f'{proto}/{port}'] = prog
    approved = r.state.get('ports_ok')
    if approved is None:
        r.state['ports_ok'] = dict(ports)
        approved = ports
    new = {k: v for k, v in ports.items() if k not in approved}
    alerted = set(r.state.get('ports_alerted') or [])
    for k, prog in sorted(new.items()):
        r.still(f'port:{k}', f'🔴 <b>New port open</b>: {k} by {html.escape(prog)}')
        if k not in alerted:
            r.event('port', f'🔴 <b>New port opened</b>: {k} by {html.escape(prog)}', red=True)
            alerted.add(k)
    r.state['ports_alerted'] = sorted(alerted & set(new))
    r.report['ports'] = {'open': len(ports), 'new': sorted(new)}


# Short-lived helpers that always show a high CPU figure: ps works out %CPU
# over a process's whole life, so anything a second old reads 100-300%.
# runc starts and stops containers (the crash-looping ivr-platform ones
# every few seconds).
SHORT_LIVED = ('runc', 'ps', 'docker-init', 'pgrep', 'ss', 'journalctl', 'git', 'sshd', 'sha256sum')
MIN_AGE = 120   # seconds a process must have been running


def check_processes(r):
    """A program nobody knows, the same process (same PID) using over 50% CPU
    and running for 2 minutes or more. Not alerted for a name alone: the
    first version flagged runc and ps, which live for a second (28 Oct)."""
    hot = {}
    for line in sh('ps -eo pid=,user=,pcpu=,etimes=,comm= --sort=-pcpu').splitlines()[:20]:
        f = line.split(None, 4)
        if len(f) < 5:
            continue
        pid, user, comm = f[0], f[1], f[4].strip()
        try:
            cpu, age = float(f[2]), int(f[3])
        except ValueError:
            continue
        if cpu < 50 or age < MIN_AGE:
            continue
        if comm in KNOWN_PROGRAMS or comm.split(':')[0] in SHORT_LIVED or comm.startswith(
                ('kworker', 'systemd', 'containerd', 'runc')):
            continue
        hot[pid] = {'comm': comm, 'user': user, 'cpu': cpu, 'age': age}
    before = r.state.get('hot') or {}
    alerted = set(r.state.get('hot_alerted') or [])
    for pid, p in hot.items():
        if pid not in before:
            continue          # seen once: wait for the next minute
        r.still(f'proc:{pid}', f'🔴 <b>Unknown process running</b>: {html.escape(p["comm"])} (pid {pid})')
        if pid not in alerted:
            r.event('process', f'🔴 <b>Unknown process running</b>: {html.escape(p["comm"])} · {p["cpu"]:.0f}% CPU · '
                               f'user {html.escape(p["user"])} · pid {pid} · running {p["age"] // 60} min', red=True)
            alerted.add(pid)
    r.state['hot'] = hot
    r.state['hot_alerted'] = sorted(alerted & set(hot))
    r.report['unknown_processes'] = sorted({p['comm'] for k, p in hot.items() if k in before})


def check_outbound(r):
    """Programs we do not know talking to the internet."""
    seen = set(r.state.get('out_seen') or [])
    unknown = []
    for line in sh('ss -tnpH state established').splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        local, peer = f[2], f[3]
        if local.rsplit(':', 1)[-1] == '22':
            continue   # someone's SSH session in, counted under SSH logins
        peer_ip = peer.rsplit(':', 1)[0].strip('[]')
        if not public_ip(peer_ip):
            continue
        prog = re.search(r'users:\(\("([^"]+)"', line)
        prog = prog.group(1) if prog else '?'
        if prog in KNOWN_PROGRAMS:
            continue
        key = f'{prog}>{peer_ip}'
        unknown.append(key)
        if key not in seen and not r.first:
            r.event('outbound', f'🔴 <b>Outbound connection to unknown IP</b>: {html.escape(prog)} → {peer} '
                                f'{country(r, peer_ip)}', red=True)
        seen.add(key)
    r.state['out_seen'] = sorted(seen)[-500:]
    r.report['unknown_outbound'] = len(set(unknown))


def check_dumps(r):
    """A database dump that is not our own backup."""
    ps = sh('ps -eo args=')
    ours = 'db_backup_b2.py' in ps
    dumps = [a for a in ps.splitlines() if re.search(r'\bpg_dump(all)?\b', a) and 'grep' not in a]
    if dumps and not ours:
        r.still('dump', '🔴 <b>Database export / dump running</b>')
        key = digest('\n'.join(sorted(dumps)))
        if key != r.state.get('dump_seen'):
            r.event('dump', f'🔴 <b>Database export / dump running</b> (not the nightly backup): '
                            f'{html.escape(dumps[0][:120])}', red=True)
        r.state['dump_seen'] = key
    elif not dumps:
        r.state.pop('dump_seen', None)


def check_downloads(r):
    """Recordings fetched from nginx, per IP per hour."""
    path = '/var/log/nginx/access.log'
    try:
        st = os.stat(path)
    except OSError:
        return
    pos = r.state.get('nginx_pos') or {}
    start = pos.get('off', 0) if pos.get('ino') == st.st_ino and pos.get('off', 0) <= st.st_size else 0
    if r.first:
        start = st.st_size
    hour = time.strftime('%Y%m%d%H', time.gmtime(r.now))
    counts = r.state.get('dl') or {}
    if counts.get('hour') != hour:
        counts = {'hour': hour, 'ip': {}, 'alerted': []}
    with open(path, 'rb') as f:
        f.seek(start)
        chunk = f.read(20_000_000)
        end = start + len(chunk)
    for line in chunk.decode('utf-8', 'replace').splitlines():
        m = re.match(r'(\S+) .*?"GET [^"]*/recordings/[^" ]+\.(?:mp3|wav)[^"]*" (200|206) ', line)
        if m and public_ip(m.group(1)) and m.group(1) not in OUR_IPS:
            counts['ip'][m.group(1)] = counts['ip'].get(m.group(1), 0) + 1
    for ip, n in counts['ip'].items():
        if n >= BULK_PER_HOUR and ip not in counts['alerted']:
            r.event('download', f'🔴 <b>Bulk recording download</b>: {n} files this hour from {ip} '
                                f'{country(r, ip)}', red=True)
            counts['alerted'].append(ip)
    r.state['nginx_pos'] = {'ino': st.st_ino, 'off': end}
    r.state['dl'] = counts
    r.report['downloads_hour'] = sum(counts['ip'].values())
    r.report['downloads_top'] = max(counts['ip'].values()) if counts['ip'] else 0


def check_logs(r):
    """A log that got smaller without being rotated was cleared."""
    before = r.state.get('logs') or {}
    now = {}
    for path in WATCHED_LOGS:
        try:
            st = os.stat(path)
        except OSError:
            if path in before:
                r.event('logs', f'🔴 <b>Log deleted</b>: {path}', red=True)
            continue
        now[path] = [st.st_ino, st.st_size]
        b = before.get(path)
        if b and b[0] == st.st_ino and st.st_size < b[1] and not _just_rotated(path, r.now):
            r.event('logs', f'🔴 <b>Log cleared</b>: {path} (was {b[1] / 1e6:.1f} MB, now '
                            f'{st.st_size / 1e6:.1f} MB)', red=True)
    r.state['logs'] = now
    r.report['logs_watched'] = len(now)


def _just_rotated(path, now):
    """logrotate's copytruncate empties the file in place: a fresh .1 or
    dated copy next to it says it was rotation, not someone clearing it."""
    for p in glob.glob(path + '.1') + glob.glob(path + '.1.gz') + glob.glob(path + '-*') + glob.glob(path + '.0'):
        try:
            if now - os.path.getmtime(p) < 600:
                return True
        except OSError:
            continue
    return False


def check_backups(r):
    """Backup files deleted or changed after they were written."""
    files = {p: [os.path.getsize(p), int(os.path.getmtime(p))]
             for p in glob.glob('/var/backups/avortyx/db-*.sql.gz')}
    before = r.state.get('backups')
    r.state['backups'] = files
    r.report['backup_files'] = len(files)
    if before is None:
        return
    # The backup job keeps a set number of days and deletes the oldest itself;
    # deleting the newest, or changing a finished file, is not the job.
    newest_before = max(before, default=None)
    for p, v in before.items():
        if p not in files and p == newest_before:
            r.event('backup', f'🔴 <b>Backup deleted</b>: {os.path.basename(p)}', red=True)
        elif p in files and files[p] != v:
            r.event('backup', f'🔴 <b>Backup changed after it was written</b>: {os.path.basename(p)}', red=True)


# ── access and changes ───────────────────────────────────────────────────────

def _journal(args, since):
    return sh(f'journalctl {args} --since=@{int(since)} --no-pager -o short-unix -q 2>/dev/null', timeout=20)


def country(r, ip):
    """Two-letter country of an IP, looked up once and kept."""
    cc = r.state.setdefault('cc', {})
    if ip in cc:
        return f'({cc[ip]})' if cc[ip] else ''
    code = ''
    try:
        with urllib.request.urlopen(f'https://ipinfo.io/{urllib.parse.quote(ip)}/country', timeout=3) as resp:
            code = resp.read().decode().strip()[:2]
    except Exception:  # noqa: BLE001
        pass
    if len(cc) > 300:
        cc.clear()
    cc[ip] = code
    return f'({code})' if code else ''


def check_ssh(r):
    since = r.state.get('ssh_since') or (r.now - 30 * DAY)
    text = _journal('_COMM=sshd _COMM=sshd-session', since)
    known = set(r.state.get('ssh_ips') or [])
    log = r.state.get('ssh_log') or []
    done = {tuple(x) for x in log}
    for line in text.splitlines():
        m = re.match(r'^(\d+)\.\d+ .*Accepted (\S+) for (\S+) from (\S+) port', line)
        if not m:
            continue
        ts, method, user, ip = int(m.group(1)), m.group(2), m.group(3), m.group(4)
        if (ts, ip) in done:
            continue   # read last minute already (the boundary second)
        done.add((ts, ip))
        if r.state.get('ssh_since') and ts >= r.state['ssh_since'] - 1:
            new_ip = ip not in known
            r.event('ssh', f'{"🔴" if new_ip else "🔐"} <b>SSH login</b>: {html.escape(user)} · {ip} '
                           f'{country(r, ip)} · {hhmm(ts)}' + (' · <b>new IP</b>' if new_ip else ''), red=new_ip)
        known.add(ip)
        log.append([ts, ip])
    r.state['ssh_since'] = int(r.now) - 1
    r.state['ssh_ips'] = sorted(known)[-200:]
    log = [x for x in log if x[0] > r.now - DAY]
    r.state['ssh_log'] = log
    cfg = sh('sshd -T 2>/dev/null')
    get = lambda k: (re.search(rf'^{k} (\S+)', cfg, re.M) or [None, None])[1]  # noqa: E731
    r.report['ssh'] = {'logins_24h': len(log), 'ips_24h': len({x[1] for x in log}),
                       'root_login': get('permitrootlogin'), 'password_login': get('passwordauthentication')}


def check_sudo(r):
    since = r.state.get('sudo_since') or r.now
    log = r.state.get('sudo_log') or []
    done = set(r.state.get('sudo_done') or [])
    for line in _journal('_COMM=sudo', since).splitlines():
        m = re.match(r'^(\d+\.\d+) \S+ sudo\[\d+\]:\s+(\S+) : .*COMMAND=(.*)$', line)
        if m and m.group(1) not in done and float(m.group(1)) >= since:
            done.add(m.group(1))
            r.event('sudo', f'🛠 <b>sudo</b>: {html.escape(m.group(2))} · <code>{html.escape(m.group(3)[:100])}</code> '
                            f'· {hhmm(float(m.group(1)))}')
            log.append(int(float(m.group(1))))
    r.state['sudo_since'] = int(r.now) - 1
    r.state['sudo_done'] = sorted(done, key=float)[-50:]
    r.state['sudo_log'] = [t for t in log if t > r.now - DAY]
    r.report['sudo_24h'] = len(r.state['sudo_log'])


def check_deploy(r):
    out = sh(f'git -c safe.directory={APP} -C {APP} log -1 --format=%H%x09%h%x09%s%x09%an').strip().split('\t')
    if len(out) < 4:
        return
    full, short, subject, author = out
    prev = r.state.get('head')
    log = [x for x in (r.state.get('deploy_log') or []) if x[0] > r.now - DAY]
    if prev and prev != full:
        count = sh(f'git -c safe.directory={APP} -C {APP} rev-list --count {prev}..{full} 2>/dev/null').strip()
        more = f' (+{int(count) - 1} more)' if count.isdigit() and int(count) > 1 else ''
        r.event('deploy', f'🚀 <b>Deploy completed</b>: {short} {html.escape(subject[:80])}{more} · {hhmm()}')
        log.append([int(r.now), short, subject[:80]])
    r.state['head'] = full
    r.state['deploy_log'] = log
    r.report['deploy'] = {'head': short, 'subject': subject[:80], 'count_24h': len(log),
                          'last': log[-1] if log else None}


def check_config(r):
    files = {}
    for g in CONFIG_GLOBS:
        for p in glob.glob(g):
            if os.path.isfile(p):
                files[p] = file_digest(p)
    added, removed, changed = r.diff_sets('cfg', files)
    log = [x for x in (r.state.get('cfg_log') or []) if x[0] > r.now - DAY]
    for verb, items in (('changed', changed), ('added', added), ('removed', removed)):
        for p in items:
            r.event('config', f'📝 <b>Config file {verb}</b>: {p} · {hhmm()}')
            log.append([int(r.now), p])
    r.state['cfg_log'] = log
    r.report['config'] = {'watched': len(files), 'changed_24h': sorted({x[1] for x in log})}


def _env_values():
    out = {}
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = digest(v.strip().strip('"\''))
    except OSError:
        pass
    return out


def check_env(r):
    added, removed, changed = r.diff_sets('env', _env_values())
    log = [x for x in (r.state.get('env_log') or []) if x[0] > r.now - DAY]
    names = [f'{k} (changed)' for k in changed] + [f'{k} (added)' for k in added] + [f'{k} (removed)' for k in removed]
    if names:
        r.event('env', f'🔑 <b>.env / API key changed</b>: {html.escape(", ".join(names)[:300])} · {hhmm()}', red=True)
        log.append([int(r.now), len(names)])
    r.state['env_log'] = log
    r.report['env_changes_24h'] = sum(x[1] for x in log)


def check_software(r):
    since = r.state.get('dpkg_since') or r.now
    items = []
    recent = False
    for path in ('/var/log/dpkg.log',):
        try:
            lines = open(path, errors='replace').read().splitlines()[-5000:]
        except OSError:
            continue
        for line in lines:
            m = re.match(r'^(\S+ \S+) (install|upgrade|remove|purge) (\S+)', line)
            if not m:
                continue
            try:
                ts = time.mktime(time.strptime(m.group(1), '%Y-%m-%d %H:%M:%S'))
            except ValueError:
                continue
            if ts > r.now - 600:
                recent = True
            if ts > since:
                sign = {'install': '+', 'upgrade': '↑'}.get(m.group(2), '−')
                items.append(f'{sign}{m.group(3).split(":")[0]}')
    r.state['dpkg_since'] = r.now
    # A system file replaced within ten minutes of a package run is the update.
    r.state['dpkg_active'] = recent
    log = [x for x in (r.state.get('sw_log') or []) if x[0] > r.now - DAY]
    if items:
        shown = ', '.join(items[:12]) + (f' and {len(items) - 12} more' if len(items) > 12 else '')
        r.event('software', f'📦 <b>Software installed, updated or removed</b>: {html.escape(shown)} · {hhmm()}')
        log.append([int(r.now), len(items)])
    r.state['sw_log'] = log
    r.report['software_24h'] = sum(x[1] for x in log)


def check_users(r):
    users = {}
    for line in open('/etc/passwd'):
        f = line.strip().split(':')
        if len(f) >= 7 and not f[6].endswith(('nologin', 'false', 'sync')):
            users[f'user:{f[0]}'] = f'{f[2]}:{f[6]}'
    for path in ['/root/.ssh/authorized_keys'] + glob.glob('/home/*/.ssh/authorized_keys'):
        try:
            for line in open(path):
                parts = line.split()
                if len(parts) >= 2 and not line.startswith('#'):
                    owner = 'root' if path.startswith('/root') else path.split('/')[2]
                    label = parts[2] if len(parts) > 2 else parts[1][-12:]
                    users[f'key:{owner}:{digest(parts[1])}'] = label[:40]
        except OSError:
            continue
    added, removed, _ = r.diff_sets('users', users)
    for k in added + removed:
        verb = 'added' if k in added else 'removed'
        kind, rest = k.split(':', 1)
        if kind == 'user':
            r.event('users', f'👤 <b>User {verb}</b>: {html.escape(rest)} · {hhmm()}', red=verb == 'added')
        else:
            owner = rest.split(':')[0]
            r.event('users', f'👤 <b>SSH key {verb}</b> for {html.escape(owner)}: '
                             f'{html.escape(users.get(k) or "")} · {hhmm()}', red=verb == 'added')
    r.report['users'] = {'logins': sum(1 for k in users if k.startswith('user:')),
                         'keys': sum(1 for k in users if k.startswith('key:'))}


def _rule_lines(text):
    # 200, not 100: ufw pads its columns, and Fail2Ban's comment ("# by
    # Fail2Ban after 3 attempts against sshd") sits at the end of the line.
    return {digest(x.strip()): x.strip()[:200] for x in text.splitlines() if x.strip()}


_F2B = re.compile(r'by\s+fail2ban\s+after\s+(\d+)\s+attempts?(?:\s+against\s+(\S+))?', re.I)
_IP = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?\b|\b[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,}\b', re.I)


def _ban(line):
    """(ip, attempts) when a ufw line is a Fail2Ban ban, else None."""
    m = _F2B.search(line or '')
    if not m:
        return None
    rule = line.split('#', 1)[0]
    ips = [ip for ip in _IP.findall(rule) if ip not in ('0.0.0.0/0', '::/0')]
    return (ips[0] if ips else '?'), int(m.group(1))


def firewall_text(added, removed, when) -> str:
    """CH-351 (Sir, 28 Oct): the firewall alert, laid out the way Sir showed -
    "Firewall rules updated · 09:31 UTC", one line per ban, and how many
    failed attempts led to it. Rules a person changed are listed below."""
    e = html.escape
    lines = [f'<b>Firewall rules updated</b> · {when}', '']
    attempts = []
    for line in added:
        b = _ban(line)
        if b:
            lines.append(f'• Blocked by Fail2Ban: <code>{e(b[0])}</code>')
            attempts.append(b[1])
    for line in removed:
        b = _ban(line)
        if b:
            lines.append(f'• Ban removed: <code>{e(b[0])}</code>')
            attempts.append(b[1])
    other = [('Rule added', x) for x in added if not _ban(x)] + [('Rule removed', x) for x in removed if not _ban(x)]
    for what, line in other[:6]:
        lines.append(f'• {what}: <code>{e(" ".join(line.split()))}</code>')
    if attempts:
        lines.append('')
        if len(set(attempts)) == 1:
            n = attempts[0]
            who = 'This action' if len(attempts) == 1 else 'Both actions' if len(attempts) == 2 else 'All actions'
            lines.append(f'{who} followed {n} failed attempt{"s" if n != 1 else ""}.')
        else:
            lines.append('Each ban followed ' + ', '.join(str(n) for n in attempts) + ' failed attempts.')
    if other:
        lines.append('')
        lines.append('A firewall rule was changed by hand: please confirm it was expected.')
    return '\n'.join(lines)


def check_firewall(r):
    rules = sh('ufw status 2>/dev/null')
    if not rules.strip():
        return
    lines = _rule_lines(rules)
    added, removed, _ = r.diff_sets('fw', lines)
    before = r.state.get('fw_text') or {}
    r.state['fw_text'] = lines
    if added or removed:
        r.event('firewall', firewall_text([lines[k] for k in added][:8],
                                          [before.get(k, '?') for k in removed][:8], hhmm()), red=True)
    r.report['firewall_rules'] = len(lines)


def check_cron(r):
    sources = {'root': sh('crontab -l -u root 2>/dev/null'), 'asterisk': sh('crontab -l -u asterisk 2>/dev/null')}
    for p in ['/etc/crontab'] + glob.glob('/etc/cron.d/*'):
        try:
            sources[p] = open(p, errors='replace').read()
        except OSError:
            pass
    lines = {}
    for src, text in sources.items():
        for x in text.splitlines():
            x = x.strip()
            if x and not x.startswith('#'):
                lines[digest(f'{src}|{x}')] = f'{src}: {x[:90]}'
    before = r.state.get('cron_text') or {}
    added, removed, _ = r.diff_sets('cron', {k: 1 for k in lines})
    r.state['cron_text'] = lines
    for k in added:
        r.event('cron', f'⏰ <b>Cron job added</b>: <code>{html.escape(lines[k])}</code>')
    for k in removed:
        r.event('cron', f'⏰ <b>Cron job removed</b>: <code>{html.escape(before.get(k, "?"))}</code>')
    r.report['cron_jobs'] = len(lines)


def check_system_files(r):
    files = {p: file_digest(p) for p in SYSTEM_FILES if os.path.exists(p)}
    files['/etc/ld.so.preload'] = file_digest('/etc/ld.so.preload') if os.path.exists('/etc/ld.so.preload') else None
    _, _, changed = r.diff_sets('sysfiles', files)
    package_update = r.state.get('dpkg_active')
    for p in changed:
        if p == '/etc/ld.so.preload' or not package_update:
            r.event('sysfile', f'🔴 <b>System file modified</b> (possible intrusion): {p}', red=True)
        else:
            r.event('sysfile', f'📦 System file updated by a package update: {p}')
    r.report['system_files'] = {'checked': len(files), 'preload': files['/etc/ld.so.preload'] is not None}


CHECKS = [check_uptime, check_cpu, check_network, check_time, check_services, check_ports, check_processes,
          check_outbound, check_dumps, check_downloads, check_logs, check_backups, check_ssh, check_sudo,
          check_deploy, check_software, check_config, check_env, check_users, check_firewall, check_cron,
          check_system_files, check_blacklist]


# ── sending ──────────────────────────────────────────────────────────────────

def _env(name):
    try:
        for line in open(ENV_FILE):
            if line.startswith(name + '='):
                return line.split('=', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ''


def telegram(text) -> bool:
    """To the status chat, the same chat the status bot uses."""
    token = _env('TELEGRAM_BOT_TOKEN')
    support = _env('TELEGRAM_SUPPORT_CHAT_ID')
    chat = _env('STATUS_CHAT_ID') or support or _env('TELEGRAM_BOSS_CHAT_ID') or _env('TELEGRAM_CHAT_ID')
    if not (token and chat):
        return False
    body = {'chat_id': chat, 'text': text[:4000], 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if support and chat == support and _env('TELEGRAM_NOTIFY_THREAD_ID'):
        body['message_thread_id'] = _env('TELEGRAM_NOTIFY_THREAD_ID')
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',
                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            answer = resp.read()
    except Exception:  # noqa: BLE001
        return False
    _remember(chat, answer)
    return ok


def _remember(chat, answer):
    """CH-332: noted so it is deleted from the chat after a week."""
    try:
        import sys
        if APP not in sys.path:
            sys.path.insert(0, APP)
        from support.telegram_autodelete import remember
        remember(chat, answer, 'server')
    except Exception:  # noqa: BLE001
        pass


REPEAT = int(os.environ.get('CRITICAL_REPEAT_SECONDS', '900'))


def follow_up(r, state):
    """Critical conditions still true: again every 15 minutes; gone: Resolved."""
    open_ = state.get('ongoing') or {}
    out = []
    for k, text in r.ongoing.items():
        if k not in open_:
            open_[k] = {'text': text, 'since': int(r.now), 'last': int(r.now)}   # its first alert went already
        elif r.now - open_[k]['last'] >= REPEAT:
            mins = int((r.now - open_[k]['since']) // 60)
            out.append(('repeat', True, f'🔁 Still not resolved ({mins} min): {text}'))
            open_[k]['last'] = int(r.now)
    for k in [k for k in open_ if k not in r.ongoing]:
        if not k.startswith('svc:'):          # a service says "running again" itself
            out.append(('resolved', False, f'✅ Resolved: {open_[k]["text"]}'))
        del open_[k]
    state['ongoing'] = open_
    return out


def message(events) -> str:
    red = any(e[1] for e in events)
    host = html.escape(socket.gethostname())
    # CH-351: "Avortyx Server Alert · <host>", and a block of its own for an
    # alert that has several lines (Sir: professional and organised).
    head = f'🔴 <b>Avortyx Server Alert</b> · <code>{host}</code>' if red \
        else f'🔔 <b>Avortyx Server</b> · <code>{host}</code>'
    lines = [e[2] for e in sorted(events, key=lambda e: not e[1])]
    if len(lines) > 25:
        lines = lines[:25] + [f'… and {len(lines) - 25} more']
    if any('\n' in x for x in lines):
        return head + '\n\n' + '\n\n'.join(lines)
    return head + '\n' + '\n'.join(lines)


# ── one run ──────────────────────────────────────────────────────────────────

def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + '.tmp'
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def run(send=True, checks=None, save=True):
    """One look. Returns the report for host_status.json. send=False, save=False
    only looks: nothing is sent and the next real run still sees every change."""
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return {'skipped': 'previous run still going'}
    state = load_state()
    r = Run(state)
    errors = {}
    for check in checks or CHECKS:
        try:
            check(r)
        except Exception as e:  # noqa: BLE001
            errors[check.__name__] = f'{type(e).__name__}: {str(e)[:100]}'
    # Alerts not delivered last time (Telegram down) go with this run's.
    pending = state.get('pending') or []
    events = [] if r.first else r.events + follow_up(r, state)
    if r.first:
        follow_up(r, state)
    if send and (events or pending):
        text = message([tuple(e) for e in pending] + events)
        state['pending'] = [] if telegram(text) else ([list(e) for e in pending] + [list(e) for e in events])[-50:]
    # The day's events, for the report's counts.
    log = [x for x in (state.get('events') or []) if x[0] > r.now - DAY]
    log += [[int(r.now), e[0], e[1]] for e in events]
    state['events'] = log[-500:]
    state['at'] = int(r.now)
    if save:
        save_state(state)
    r.report['events_24h'] = {k: sum(1 for x in log if x[1] == k) for k in EVENT_KINDS}
    r.report['red_24h'] = sum(1 for x in log if x[2] and x[1] in EVENT_KINDS)
    r.report['open_critical'] = sorted(v['text'] for v in (state.get('ongoing') or {}).values())
    r.report['red_by_kind_24h'] = {k: sum(1 for x in log if x[1] == k and x[2]) for k in EVENT_KINDS}
    r.report['server'] = {'hostname': socket.gethostname(), 'ip': OUR_IPS[0], 'label': SERVER_LABEL}
    r.report['first_run'] = r.first
    if errors:
        r.report['errors'] = errors
    return r.report


def accept():
    """After a planned change: today's open ports become the approved list
    and open alerts stop repeating."""
    state = load_state()
    for k in ('ports_ok', 'ports_alerted', 'ongoing', 'hot', 'hot_alerted'):
        state.pop(k, None)
    save_state(state)
    return 'Accepted: current ports approved, open alerts cleared. The next run takes the picture again.'


if __name__ == '__main__':
    import sys
    if '--accept' in sys.argv:
        print(accept())
        sys.exit(0)
    look = '--look' in sys.argv
    print(json.dumps(run(send=not look, save=not look), indent=1))

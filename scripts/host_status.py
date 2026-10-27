#!/usr/bin/env python3
"""What only the host can see, for the status bot (CH-314, issue 6).

The bot runs in the web container, which cannot see Asterisk, the relay, the
firewall, the disk or the SIP capture. Every minute this collects them into
/opt/call_platform/run/host_status.json (the container reads it as
/app/run/host_status.json). Read-only: it changes nothing and restarts nothing.

    python3 scripts/host_status.py            # write the file
    python3 scripts/host_status.py --print    # print it
    cron: * * * * * python3 /opt/call_platform/scripts/host_status.py >/dev/null 2>&1

SIP numbers come from the capture (/root/sipcap/xolo-*.pcap, one file an
hour): outbound calls we sent to buyers, the carrier's final answer to each,
and post-dial delay (INVITE to first ringing). A finished hour is read once
and kept in /var/lib/avortyx/sipstats/; only the current hour is re-read.
"""
import glob
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time

APP = os.environ.get('APP_DIR', '/opt/call_platform')
OUT = os.environ.get('HOST_STATUS_FILE', os.path.join(APP, 'run', 'host_status.json'))
CAPTURES = os.environ.get('SIP_CAPTURES', '/root/sipcap/xolo-*.pcap')
STATS_DIR = os.environ.get('SIP_STATS_DIR', '/var/lib/avortyx/sipstats')
RECORDINGS = '/var/spool/asterisk/recordings'
OUR_IPS = tuple(os.environ.get('OUR_SIP_IPS', '156.67.25.167').split(','))
# The carrier refused or could not complete the call: the buyer never rang.
# Busy (486), no answer (480) and the caller hanging up (487) are not here:
# those calls reached the buyer's line.
REJECTED = {'400', '403', '404', '408', '410', '484', '488', '500', '502', '503', '504',
            '603', '604', '606', '607', '608'}
SECTIONS = []


def sh(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ''


def part(fn):
    """Each part on its own: one failing never stops the others."""
    SECTIONS.append(fn)
    return fn


@part
def system():
    load1, load5, _ = os.getloadavg()
    mem = dict(line.split(':', 1) for line in open('/proc/meminfo'))
    kb = lambda k: int(mem[k].split()[0])  # noqa: E731
    disk = shutil.disk_usage('/')
    return {'cpus': os.cpu_count(), 'load1': round(load1, 2), 'load5': round(load5, 2),
            'mem_total_mb': kb('MemTotal') // 1024, 'mem_avail_mb': kb('MemAvailable') // 1024,
            'disk_total_gb': round(disk.total / 1e9, 1), 'disk_used_pct': round(disk.used * 100 / (disk.used + disk.free), 1),
            'disk_free_gb': round(disk.free / 1e9, 1)}


@part
def watch():
    """Section 0 of the report and the instant alerts (CH-327): what changed on
    the server since a minute ago. scripts/host_watch.py; read-only too.
    --print looks without sending any alert."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import host_watch
    dry = '--print' in sys.argv
    return host_watch.run(send=not dry, save=not dry)


@part
def asterisk():
    out = {}
    m = re.search(r'(\d+) active call', sh('asterisk -rx "core show channels count"'))
    out['active_calls'] = int(m.group(1)) if m else None
    out['running'] = out['active_calls'] is not None
    # The carrier trunk: contact status and round-trip time.
    contacts = []
    for line in sh('asterisk -rx "pjsip show contacts"').splitlines():
        m = re.search(r'Contact:\s+(\S+)/\S+\s+\S+\s+(Avail|Unavail|NonQual|Unknown|Created)\s+([\d.]+|nan)?', line)
        if m and m.group(1).startswith('carrier'):
            contacts.append({'endpoint': m.group(1), 'status': m.group(2), 'rtt_ms': m.group(3)})
    out['trunk'] = contacts
    out['quality'] = channel_quality(sh('asterisk -rx "pjsip show channelstats"'))
    db = sh('asterisk -rx "database get avortyx cancel_hold"')
    out['cancel_hold_on'] = 'Value: 1' in db
    return out


def channel_quality(text):
    """Live calls' audio from "pjsip show channelstats": codec, packet loss %,
    jitter and round trip. Columns after the up-time: codec, then receive
    count / lost / lost% / jitter(s), transmit count / lost / lost% / jitter(s),
    and RTT(s)."""
    rows = []
    for line in text.splitlines():
        cells = line.split()
        idx = next((i for i, c in enumerate(cells) if re.match(r'^\d+:\d\d:\d\d$', c)), None)
        if idx is None or len(cells) < idx + 11:
            continue
        try:
            nums = [float(x) for x in cells[idx + 2: idx + 11]]
        except ValueError:
            continue
        rows.append({'codec': cells[idx + 1], 'rx_loss_pct': nums[2], 'rx_jitter_ms': nums[3] * 1000,
                     'tx_loss_pct': nums[6], 'rtt_ms': nums[8] * 1000})
    if not rows:
        return {'channels': 0}
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 1)  # noqa: E731
    return {'channels': len(rows), 'codecs': sorted({r['codec'] for r in rows}),
            'loss_pct_avg': avg('rx_loss_pct'), 'loss_pct_max': max(r['rx_loss_pct'] for r in rows),
            'jitter_ms_avg': avg('rx_jitter_ms'), 'jitter_ms_max': round(max(r['rx_jitter_ms'] for r in rows), 1),
            'rtt_ms_avg': avg('rtt_ms')}


@part
def services():
    def active(name):
        return sh(f'systemctl is-active {name}').strip() or 'unknown'
    ufw = sh('ufw status')
    return {'kamailio': active('kamailio'), 'nginx': active('nginx'), 'fail2ban': active('fail2ban'),
            'asterisk': active('asterisk'), 'docker': active('docker'),
            'ufw_active': 'Status: active' in ufw,
            'nginx_rate_limits': bool(sh("grep -rl limit_req_zone /etc/nginx 2>/dev/null").strip())}


@part
def firewall_blocks():
    """UFW blocks in the last hour (all ports, and the SIP port)."""
    since = time.time() - 3600
    total = sip = 0
    srcs = {}
    for line in sh('tail -n 20000 /var/log/ufw.log 2>/dev/null').splitlines():
        if 'BLOCK' not in line:
            continue
        m = re.match(r'^(\S+)', line)
        ts = None
        if m:
            try:
                ts = time.mktime(time.strptime(m.group(1)[:19], '%Y-%m-%dT%H:%M:%S'))
            except ValueError:
                try:
                    ts = time.mktime(time.strptime(time.strftime('%Y ') + line[:15], '%Y %b %d %H:%M:%S'))
                except ValueError:
                    ts = None
        if ts is None or ts < since:
            continue
        total += 1
        if 'DPT=5060' in line:
            sip += 1
        s = re.search(r'SRC=(\S+)', line)
        if s:
            srcs[s.group(1)] = srcs.get(s.group(1), 0) + 1
    top = sorted(srcs.items(), key=lambda x: -x[1])[:3]
    return {'blocked_last_hour': total, 'sip_blocked_last_hour': sip, 'top_sources': top}


def _sip_file_stats(path):
    sys.path.insert(0, os.path.join(APP, 'docs', 'asterisk'))
    import late_answers as L
    calls = {}
    for ts, src, dst, pay in L.udp_payloads([path]):
        m = L.parse_sip(pay)
        if not m or not m['call_id']:
            continue
        cid = m['call_id']
        if m['kind'] == 'request' and m['method'] == 'INVITE' and cid not in calls:
            outbound = src.split(':')[0] in OUR_IPS
            calls[cid] = {'t': ts, 'out': outbound, 'dest': L.user_digits(m['uri'] or m['to'])[-11:],
                          'caller': L.user_digits(m['from'] or '')[-11:], 'ring': None, 'final': None}
            continue
        c = calls.get(cid)
        if not c or m['kind'] != 'response' or m['cseq_method'] != 'INVITE':
            continue
        code = m['code'] or ''
        if code in ('180', '183') and c['ring'] is None:
            c['ring'] = ts - c['t']
        if code[:1] in '2456' and code != '100' and c['final'] is None and not code.startswith('1'):
            c['final'] = code
    st = {'inbound': 0, 'outbound': 0, 'codes': {}, 'pdd': [], 'dest': {}, 'spam_callers': {}}
    for c in calls.values():
        if not c['out']:
            st['inbound'] += 1
            continue
        st['outbound'] += 1
        code = c['final'] or 'none'
        st['codes'][code] = st['codes'].get(code, 0) + 1
        if c['ring'] is not None:
            st['pdd'].append(round(c['ring'], 2))
        d = st['dest'].setdefault(c['dest'], {'total': 0, 'rejected': 0, 'codes': {}})
        d['total'] += 1
        if code in REJECTED:
            d['rejected'] += 1
            d['codes'][code] = d['codes'].get(code, 0) + 1
        # 607/608: refused for the caller ID's spam label. Which caller IDs,
        # so the team can see whether it is a few numbers or many (CH-326).
        if code in SPAM_CODES and c.get('caller'):
            sc = st['spam_callers']
            sc[c['caller']] = sc.get(c['caller'], 0) + 1
    return st


SPAM_CODES = {'607', '608'}


@part
def sip():
    files = sorted(glob.glob(CAPTURES))[-24:]
    if not files:
        return {'captures': 0}
    os.makedirs(STATS_DIR, exist_ok=True)
    per = []
    reread_done = False
    for i, f in enumerate(files):
        cache = os.path.join(STATS_DIR, os.path.basename(f) + '.json')
        current = i == len(files) - 1
        if not current and os.path.exists(cache):
            saved = json.load(open(cache))
            # Hours read before CH-326 have no caller IDs: read one of them
            # again per run, so the day fills in without one long run.
            if 'spam_callers' in saved or reread_done:
                per.append(saved)
                continue
            reread_done = True
        try:
            st = _sip_file_stats(f)
        except Exception as e:  # noqa: BLE001
            st = {'error': str(e)[:200], 'inbound': 0, 'outbound': 0, 'codes': {}, 'pdd': [], 'dest': {}}
        st['file'] = os.path.basename(f)
        if not current:
            json.dump(st, open(cache, 'w'))
        per.append(st)

    def total(parts):
        out = {'inbound': 0, 'outbound': 0, 'codes': {}, 'pdd': [], 'dest': {}, 'spam_callers': {}}
        for p in parts:
            for k, v in p.get('spam_callers', {}).items():
                out['spam_callers'][k] = out['spam_callers'].get(k, 0) + v
            out['inbound'] += p.get('inbound', 0)
            out['outbound'] += p.get('outbound', 0)
            out['pdd'] += p.get('pdd', [])
            for k, v in p.get('codes', {}).items():
                out['codes'][k] = out['codes'].get(k, 0) + v
            for k, v in p.get('dest', {}).items():
                d = out['dest'].setdefault(k, {'total': 0, 'rejected': 0, 'codes': {}})
                d['total'] += v['total']
                d['rejected'] += v['rejected']
                for c, n in v['codes'].items():
                    d['codes'][c] = d['codes'].get(c, 0) + n
        rejected = sum(v for k, v in out['codes'].items() if k in REJECTED)
        pdd = sorted(out['pdd'])
        out['rejected'] = rejected
        out['delivery_pct'] = round(100 * (out['outbound'] - rejected) / out['outbound'], 2) if out['outbound'] else None
        out['pdd_median'] = round(statistics.median(pdd), 2) if pdd else None
        out['pdd_p90'] = round(pdd[int(len(pdd) * 0.9) - 1], 2) if len(pdd) >= 10 else None
        out.pop('pdd')
        # Only the numbers with refusals, worst first, to keep the file small.
        out['dest'] = dict(sorted(((k, v) for k, v in out['dest'].items() if v['rejected']),
                                  key=lambda x: -x[1]['rejected'])[:20])
        out['spam_caller_count'] = len(out['spam_callers'])
        out['spam_callers'] = dict(sorted(out['spam_callers'].items(), key=lambda x: -x[1])[:30])
        return out
    return {'captures': len(files), 'last_hour': total(per[-1:]), 'day': total(per),
            'newest': os.path.basename(files[-1])}


@part
def recordings():
    out = {}
    if os.path.isdir(RECORDINGS):
        wav = mp3 = 0
        with os.scandir(RECORDINGS) as it:
            for e in it:
                if e.name.endswith('.wav'):
                    wav += 1
                elif e.name.endswith('.mp3'):
                    mp3 += 1
        out.update(wav=wav, mp3=mp3)
    out['mp3_log'] = sh('tail -n 1 /var/log/recordings_to_mp3.log 2>/dev/null').strip()[-200:]
    out['b2_log'] = sh('tail -n 1 /var/log/recordings_to_b2.log 2>/dev/null').strip()[-200:]
    db = '/var/lib/avortyx/b2.sqlite3'
    if os.path.exists(db):
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        n, size, last = con.execute('SELECT COUNT(*), COALESCE(SUM(size),0), MAX(copied_at) FROM copied').fetchone()
        try:
            gone = con.execute('SELECT COUNT(*) FROM copied WHERE deleted_at IS NOT NULL').fetchone()[0]
        except sqlite3.OperationalError:
            gone = 0
        out.update(b2_files=n, b2_gb=round(size / 1e9, 2), b2_last_copy=last, local_removed=gone)
    return out


@part
def backups():
    p = '/var/lib/avortyx/backup_last.json'
    return json.load(open(p)) if os.path.exists(p) else {}


@part
def logs():
    """Last lines of the host jobs the bot reports on."""
    return {'daily_status': sh('tail -n 1 /var/log/avortyx-daily-status.log 2>/dev/null').strip()[-200:],
            'late_answers': sh('tail -n 1 /var/log/late_answers.log 2>/dev/null').strip()[-200:],
            'watchdog': sh('tail -n 1 /var/log/avortyx-watchdog.log 2>/dev/null').strip()[-200:]}


def main():
    data = {'at': int(time.time())}
    for fn in SECTIONS:
        try:
            data[fn.__name__] = fn()
        except Exception as e:  # noqa: BLE001
            data[fn.__name__] = {'error': str(e)[:200]}
    if '--print' in sys.argv:
        print(json.dumps(data, indent=1))
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    json.dump(data, open(tmp, 'w'))
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)


if __name__ == '__main__':
    main()

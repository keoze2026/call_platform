#!/usr/bin/env python3
"""How long a cancel hold would have to be (CH-295). Read-only.

Sir, 27 Oct: "Size the window with today's 29 calls. Compare each caller's
hangup time with Revo's answer time. If some answers came later than 500 ms,
raise the hold to 800-1000 ms."

For each affected call: our CANCEL time from the SIP capture (to the ms) and
the buyer's answer time on Revo (Revo's start + TTC, whole seconds, read from
the Revo API). Revo gives whole seconds, so each gap is a 1-second range:
the answer came between LOW and LOW+1 s after our cancel.

    python3 docs/asterisk/size_cancel_hold.py /root/affected.txt "/root/sipcap/xolo-20261026-*.pcap"
"""
import datetime as dt
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import late_answers as L  # noqa: E402

ENV = '/opt/call_platform/.env'


def env(name, default=''):
    for line in open(ENV):
        if line.startswith(name + '='):
            return line.split('=', 1)[1].strip().strip("'\"")
    return default


def revo_rows(caller, day):
    key, ws = env('REVO_API_KEY'), env('REVO_WORKSPACE')
    base = env('REVO_API_BASE', 'https://main-api.revocalls.com/api').rstrip('/')
    q = urllib.parse.urlencode({'date': day, 'dateTo': day, 'timezone': env('REVO_TIMEZONE_ID', '46'),
                                'search': caller})
    req = urllib.request.Request(f'{base}/{ws}/calls/log?{q}',
                                 headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'})
    body = json.load(urllib.request.urlopen(req, timeout=60))
    return (body.get('payload') or {}).get('data') or []


def utc(s):
    return dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc).timestamp()


calls = []
for line in open(sys.argv[1]):
    p = line.split()
    if len(p) >= 4 and p[2].replace('.', '').isdigit():
        calls.append({'caller': p[0][-10:], 'dest': p[1][-10:], 'at': float(p[2])})
files = sorted(glob.glob(sys.argv[2] if len(sys.argv) > 2 else '/root/sipcap/xolo-*.pcap'))
dialogs, invites = {}, []
for ts, src, dst, pay in L.udp_payloads(files):
    m = L.parse_sip(pay)
    if not m or not m['call_id']:
        continue
    d = dialogs.setdefault(m['call_id'], {})
    if m['kind'] == 'request' and m['method'] == 'INVITE' and 'invite' not in d:
        d['invite'] = ts
        invites.append((ts, m['call_id'], L.user_digits(m['from']), L.user_digits(m['uri'] or m['to'])))
    if m['kind'] == 'request' and m['method'] == 'CANCEL' and 'cancel' not in d:
        d['cancel'] = ts

lows = []
print('caller      our CANCEL (UTC)   Revo answer   answer came after our cancel')
for c in calls:
    cand = [i for i in invites if i[2].endswith(c['caller']) and i[3].endswith(c['dest'])
            and c['at'] - 5 <= i[0] <= c['at'] + 30]
    if not cand or 'cancel' not in dialogs[cand[0][1]]:
        print(f"{c['caller']}  no CANCEL in capture")
        continue
    invite, cancel = cand[0][0], dialogs[cand[0][1]]['cancel']
    day = dt.datetime.fromtimestamp(invite, dt.timezone(dt.timedelta(hours=-4))).date().isoformat()
    try:
        rows = revo_rows(c['caller'], day)
    except Exception as exc:
        print(f"{c['caller']}  Revo not read: {exc}")
        continue
    best = None
    for r in rows:
        try:
            start = utc(r['created_at'])
        except Exception:
            continue
        if invite - 2 <= start <= invite + 20 and str(r.get('caller_number', '')).endswith(c['caller']):
            best = (start, int(r.get('ttc') or 0))
            break
    if not best:
        print(f"{c['caller']}  no Revo row")
        continue
    answer = best[0] + best[1]
    low = answer - cancel
    lows.append(low)
    t = dt.datetime.fromtimestamp(cancel, dt.timezone.utc).strftime('%H:%M:%S.%f')[:-3]
    a = dt.datetime.fromtimestamp(answer, dt.timezone.utc).strftime('%H:%M:%S')
    print(f"{c['caller']}  {t}     {a}      between {low:+.2f}s and {low + 1:+.2f}s")

if lows:
    print(f'\n{len(lows)} calls measured.')
    for hold in (0.5, 0.8, 1.0, 1.5, 2.0):
        sure = sum(1 for x in lows if x + 1 <= hold)
        maybe = sum(1 for x in lows if x <= hold < x + 1)
        print(f'hold {int(hold * 1000):4} ms: surely caught {sure:2}, maybe {maybe:2}, missed {len(lows) - sure - maybe:2}')

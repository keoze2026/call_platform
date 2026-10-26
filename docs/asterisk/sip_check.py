#!/usr/bin/env python3
"""What the SIP capture shows for each affected call (CH-293). Read-only.

Sir, 26 Oct: "run packet captures on the affected calls. If the buyer's answer
(200 OK) arrives at our server just after the cancel, record it as Answered."
For every call in the list it prints the SIP messages between us and XOLO in
order (INVITE, 180/183, CANCEL, 487, 200 OK, BYE) and whether a 200 OK
reached us, and when: before our cancel, after it, or never.

    # the affected calls (Completed by Revo) from the database:
    docker compose exec -T web python manage.py shell -c "..." > /root/affected.txt
    python3 docs/asterisk/sip_check.py /root/affected.txt [capture glob]

List lines: <caller> <destination> <unix time> <label>.
"""
import os, sys, glob, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import late_answers as L
calls = []
for line in open(sys.argv[1]):
    p = line.split()
    if len(p) >= 4 and p[2].replace('.', '').isdigit():
        calls.append({'caller': p[0][-10:], 'dest': p[1][-10:], 'at': float(p[2]), 'label': p[3]})
files = sorted(glob.glob(sys.argv[2] if len(sys.argv) > 2 else '/root/sipcap/xolo-*.pcap'))
msgs = {}
invites = []
for ts, src, dst, pay in L.udp_payloads(files):
    m = L.parse_sip(pay)
    if not m or not m['call_id']:
        continue
    msgs.setdefault(m['call_id'], []).append((ts, m))
    if m['kind'] == 'request' and m['method'] == 'INVITE':
        invites.append((ts, m['call_id'], L.user_digits(m['from']), L.user_digits(m['uri'] or m['to'])))
def hhmmss(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%H:%M:%S.%f')[:-3]
summary = {}
for c in calls:
    cand = [i for i in invites if i[2].endswith(c['caller']) and i[3].endswith(c['dest'])
            and c['at'] - 5 <= i[0] <= c['at'] + 30]
    if not cand:
        verdict = 'no INVITE in capture'
        print(f"{c['label']} {c['caller']} -> {c['dest']}: {verdict}")
        summary[verdict] = summary.get(verdict, 0) + 1
        continue
    cid = cand[0][1]
    seq, cancel, ok = [], None, None
    for ts, m in sorted(msgs[cid], key=lambda x: x[0]):
        what = m['method'] if m['kind'] == 'request' else f"{m['code']}({m['cseq_method']})"
        seq.append(f'{hhmmss(ts)[3:]} {what}')
        if m['kind'] == 'request' and m['method'] == 'CANCEL' and cancel is None:
            cancel = ts
        if m['kind'] == 'response' and m['cseq_method'] == 'INVITE' and m['code'].startswith('2') and ok is None:
            ok = ts
    if ok is None:
        verdict = '200 OK never reached us'
    elif cancel is None or ok < cancel:
        verdict = '200 OK before our cancel (normal answer)'
    else:
        verdict = f'200 OK {ok - cancel:.2f}s AFTER our cancel'
    key = verdict if 'AFTER' not in verdict else '200 OK after our cancel'
    summary[key] = summary.get(key, 0) + 1
    print(f"{c['label']} {c['caller']} -> {c['dest']}: {verdict}")
    print('     ' + ' | '.join(seq[:9]))
print('\nSUMMARY:', summary)

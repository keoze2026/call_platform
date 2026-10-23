#!/usr/bin/env python3
"""Buyer answers that reached us just after the caller hung up (CH-225).

Read-only. Reads the SIP capture and prints one JSON line per call where the far
side's answer (200 OK to our INVITE) arrived AFTER we had cancelled the call
because the caller hung up - the answer and the cancel crossed on the line.
The far side (Revo) counts such a call as connected; mark_late_answers counts it
the same way on our side.

    python3 /root/late_answers.py /root/sipcap            # the 2 newest captures
    python3 /root/late_answers.py "/root/sipcap/xolo-20261023-*.pcap"

Piped into the backend by cron every 2 minutes:
    cd /opt/call_platform && python3 /root/late_answers.py /root/sipcap \\
      | docker compose exec -T web python manage.py mark_late_answers --apply

Needs only Python 3.
"""
import glob
import json
import os
import struct
import sys

# An answer more than this long after our cancel is not one that crossed it.
CROSSED_WITHIN = 2.0
NEWEST_FILES = 2

COMPACT = {'i': 'call-id', 'f': 'from', 't': 'to', 'c': 'content-type', 'l': 'content-length'}


# ── reading the capture ──────────────────────────────────────────────────────

def packets(path):
    """(timestamp, ip_bytes) for every IPv4 packet in a classic pcap file."""
    with open(path, 'rb') as fh:
        head = fh.read(24)
        if len(head) < 24:
            return
        magic = head[:4]
        if magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1'):
            end = '<'
        elif magic in (b'\xa1\xb2\xc3\xd4', b'\xa1\xb2\x3c\x4d'):
            end = '>'
        else:
            sys.exit(f'{path}: not a classic pcap file (pcapng is not supported)')
        nano = magic in (b'\x4d\x3c\xb2\xa1', b'\xa1\xb2\x3c\x4d')
        linktype = struct.unpack(end + 'I', head[20:24])[0]
        while True:
            rec = fh.read(16)
            if len(rec) < 16:
                return
            sec, frac, incl, _orig = struct.unpack(end + 'IIII', rec)
            data = fh.read(incl)
            if len(data) < incl:
                return
            ts = sec + frac / (1e9 if nano else 1e6)
            ip = _ip_of(linktype, data)
            if ip is not None:
                yield ts, ip


def _ip_of(linktype, data):
    if linktype == 1:                       # Ethernet
        off, proto = 14, data[12:14]
        while proto in (b'\x81\x00', b'\x88\xa8'):   # VLAN tags
            proto = data[off + 2:off + 4]
            off += 4
    elif linktype == 113:                   # Linux cooked (tcpdump -i any)
        off, proto = 16, data[14:16]
    elif linktype == 276:                   # Linux cooked v2
        off, proto = 20, data[0:2]
    elif linktype in (12, 101, 228):        # raw IP
        off, proto = 0, b'\x08\x00'
    elif linktype == 0:                     # BSD loopback
        off, proto = 4, b'\x08\x00'
    else:
        return None
    if proto != b'\x08\x00':
        return None
    ip = data[off:]
    if len(ip) < 20 or ip[0] >> 4 != 4:
        return None
    return ip


def udp_payloads(paths):
    """(timestamp, src, dst, payload) for every UDP datagram, fragments joined."""
    frags = {}
    for path in paths:
        for ts, ip in packets(path):
            ihl = (ip[0] & 0x0F) * 4
            total = struct.unpack('>H', ip[2:4])[0]
            ip = ip[:total]
            if ip[9] != 17:
                continue
            src = '.'.join(str(b) for b in ip[12:16])
            dst = '.'.join(str(b) for b in ip[16:20])
            ident = struct.unpack('>H', ip[4:6])[0]
            flags_off = struct.unpack('>H', ip[6:8])[0]
            more = bool(flags_off & 0x2000)
            offset = (flags_off & 0x1FFF) * 8
            body = ip[ihl:]
            if more or offset:
                key = (src, dst, ident)
                parts = frags.setdefault(key, {'ts': ts, 'parts': {}, 'last': None})
                parts['parts'][offset] = body
                if not more:
                    parts['last'] = offset + len(body)
                if parts['last'] is None:
                    continue
                joined, pos = b'', 0
                for o in sorted(parts['parts']):
                    if o != pos:
                        break
                    joined += parts['parts'][o]
                    pos += len(parts['parts'][o])
                if pos != parts['last']:
                    continue
                del frags[key]
                body, ts = joined, parts['ts']
            if len(body) < 8:
                continue
            sport, dport = struct.unpack('>HH', body[:4])
            yield ts, f'{src}:{sport}', f'{dst}:{dport}', body[8:]


# ── reading SIP ──────────────────────────────────────────────────────────────

def parse_sip(payload):
    try:
        text = payload.decode('utf-8', 'replace')
    except Exception:
        return None
    head, _, body = text.partition('\r\n\r\n')
    lines = head.split('\r\n')
    first = lines[0]
    if first.startswith('SIP/2.0 '):
        kind, code = 'response', first.split(' ', 2)[1]
        method = None
        uri = ''
    else:
        parts = first.split(' ')
        if len(parts) < 3 or not parts[2].startswith('SIP/2.0'):
            return None
        kind, code, method, uri = 'request', None, parts[0], parts[1]
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(':')
        if not sep:
            continue
        name = name.strip().lower()
        name = COMPACT.get(name, name)
        headers.setdefault(name, value.strip())
    cseq = headers.get('cseq', '').split()
    return {
        'kind': kind, 'code': code, 'method': method, 'uri': uri,
        'call_id': headers.get('call-id', ''),
        'cseq_method': cseq[1] if len(cseq) > 1 else '',
        'from': headers.get('from', ''), 'to': headers.get('to', ''),
        'sdp': body.strip() if 'v=0' in body else '',
    }


def digits_of(s):
    return ''.join(ch for ch in s.split('@')[0] if ch.isdigit())


def user_digits(header_or_uri):
    """Digits of the user part of a SIP URI inside a header."""
    s = header_or_uri
    if 'sip:' in s:
        s = s.split('sip:', 1)[1]
    return digits_of(s)



def late_answers(paths):
    calls = {}
    for ts, src, dst, payload in udp_payloads(paths):
        msg = parse_sip(payload)
        if not msg or not msg['call_id']:
            continue
        d = calls.setdefault(msg['call_id'], {})
        if msg['kind'] == 'request' and msg['method'] == 'INVITE' and 'invite' not in d:
            d['invite'] = (ts, src, user_digits(msg['from']), user_digits(msg['uri'] or msg['to']))
        elif msg['kind'] == 'request' and msg['method'] == 'CANCEL' and 'cancel' not in d:
            d['cancel'] = (ts, src)
        elif (msg['kind'] == 'response' and msg['cseq_method'] == 'INVITE'
              and msg['code'].startswith('2') and 'answer' not in d):
            d['answer'] = ts
    for d in calls.values():
        if not {'invite', 'cancel', 'answer'} <= d.keys():
            continue
        invite_at, us, caller, called = d['invite']
        cancel_at, cancel_src = d['cancel']
        # Our own outgoing call, cancelled by us, answered after the cancel.
        if cancel_src != us:
            continue
        if not (cancel_at <= d['answer'] <= cancel_at + CROSSED_WITHIN):
            continue
        yield {'caller': caller, 'called': called, 'invite_at': round(invite_at, 3),
               'cancel_at': round(cancel_at, 3), 'answer_at': round(d['answer'], 3)}


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: python3 late_answers.py <capture folder | capture files...>')
    paths = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            found = sorted(glob.glob(os.path.join(arg, '*.pcap')), key=os.path.getmtime)
            paths += found[-NEWEST_FILES:]
        else:
            paths += sorted(glob.glob(arg))
    for event in late_answers(sorted(set(paths))):
        print(json.dumps(event))


if __name__ == '__main__':
    main()

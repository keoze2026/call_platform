#!/usr/bin/env python3
"""AGI script - bridges Asterisk to Django routing engine.

Server A's copy (CH-464). Installed as /usr/share/asterisk/agi-bin/route_call.py
by scripts/fallback/install_a.sh, which also puts the fallback code where the
asterisk user can read it: /usr/local/lib/avortyx/fallback (CH-466).
It is the script that ran on the old server, with two changes:

  1. When Server B cannot be reached it no longer hangs up: Server A routes
     the call itself (scripts/fallback/fallback_agi.py). When B answers,
     B's reply is used exactly as before.
  2. The shared secret is no longer written in this file. It is read from
     /etc/asterisk/call_ended.secret (the file call_ended.sh already uses),
     so changing the secret means changing one file.
  3. CH-514: "route_call.py next" (from the dialplan, after a destination did
     not answer) asks B for the next buyer.
"""
import sys
import os
import json
import urllib.request
import urllib.error

sys.dont_write_bytecode = True

DJANGO_BASE = os.environ.get("AVX_ROUTE_BASE", "http://10.20.0.2:8001")
SECRET_FILE = os.environ.get("AVX_SECRET_FILE", "/etc/asterisk/call_ended.secret")
FALLBACK_DIR = os.environ.get("AVX_FALLBACK_CODE", "/usr/local/lib/avortyx/fallback")
ROUTE_TIMEOUT = float(os.environ.get("AVX_ROUTE_TIMEOUT", "30"))


def shared_secret():
    try:
        with open(SECRET_FILE) as f:
            return f.readline().strip().strip('"').strip("'")
    except OSError:
        return ''


def agi_read_env():
    env = {}
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            break
        if ':' in line:
            k, v = line.split(':', 1)
            env[k.strip()] = v.strip()
    return env

def agi_cmd(cmd):
    sys.stdout.write(cmd + "\n")
    sys.stdout.flush()
    return sys.stdin.readline().strip()

def agi_verbose(msg):
    safe = str(msg).replace('"', '\\"')
    agi_cmd(f'VERBOSE "{safe}" 1')

def agi_set_var(name, value):
    safe = str(value).replace('"', '\\"')
    agi_cmd(f'SET VARIABLE {name} "{safe}"')


def agi_get_var(name):
    """A channel variable, or '' ("200 result=1 (value)" / "200 result=0")."""
    r = agi_cmd(f'GET VARIABLE {name}')
    if '(' in r and r.endswith(')'):
        return r[r.index('(') + 1:-1]
    return ''


def set_dial_vars(data):
    """What the dialplan needs to dial B's answer (CH-514: ring time and next)."""
    agi_set_var("BUYER_NUMBER", data.get("buyer_number", ""))
    agi_set_var("CALL_LOG_ID", data.get("call_log_id", ""))
    agi_set_var("MAX_DURATION", str(data.get("max_duration", 3600)))
    agi_set_var("RECORD_CALL", "1" if data.get("recording_enabled", True) else "0")
    ring = data.get("ring_timeout")
    agi_set_var("RING_TIMEOUT", str(int(ring)) if ring else "")
    agi_set_var("NEXT_ON_FAIL", "1" if data.get("next_on_fail") else "0")


def route_next(env):
    """CH-514: the destination did not answer - ask B for the next buyer.
    B down or no next buyer: the call ends as it does without this."""
    payload = json.dumps({
        "call_log_id": agi_get_var("CALL_LOG_ID"),
        "dial_status": agi_get_var("DIALSTATUS"),
        "dialed_seconds": agi_get_var("DIALEDTIME") or "0",
        "asterisk_call_id": env.get('agi_uniqueid', ''),
    }).encode('utf-8')
    req = urllib.request.Request(
        f"{DJANGO_BASE}/api/twilio/asterisk/route-next/",
        data=payload,
        headers={"Content-Type": "application/json", "X-Asterisk-Secret": shared_secret()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=min(ROUTE_TIMEOUT, 10)) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        agi_verbose(f"next buyer: B not reached ({e}); ending the call")
        agi_set_var("ROUTE_ACTION", "hangup")
        return
    action = data.get("action", "hangup")
    agi_set_var("ROUTE_ACTION", action)
    if action == "dial":
        set_dial_vars(data)
        agi_verbose(f"Next buyer: {data.get('buyer_number')}")
    else:
        agi_verbose(f"No next buyer: {data.get('reason', 'unknown')}")


def clean_caller(raw):
    raw = (raw or '').strip()
    digits = ''.join(c for c in raw if c.isdigit())
    if len(raw) <= 20 and len(digits) <= 15:
        return raw
    if digits.startswith('1') and len(digits) >= 11:
        return '+' + digits[:11]
    if digits:
        return '+' + digits[:15]
    return raw[:20]


def load_fallback():
    """Server A's own routing; None if it is not installed (then a call B
    cannot take is hung up, as before)."""
    try:
        if FALLBACK_DIR not in sys.path:
            sys.path.insert(0, FALLBACK_DIR)
        import fallback_agi
        return fallback_agi
    except Exception as e:
        agi_verbose(f"fallback routing not available: {e}")
        return None


def main():
    env = agi_read_env()
    if len(sys.argv) > 1 and sys.argv[1] == 'next':
        return route_next(env)
    caller = clean_caller(env.get('agi_callerid', ''))
    if caller != env.get('agi_callerid', ''):
        # CH-205: send the cleaned caller ID on the call to the buyer; the raw
        # repeated number is rejected by the carrier with 608 No STI IDHDR.
        agi_cmd(f'SET CALLERID "{caller}"')
    called = env.get('agi_extension', '')
    call_id = env.get('agi_uniqueid', '')

    agi_verbose(f"Routing call from {caller} to {called}")

    caller_number = "+" + caller if caller and not caller.startswith('+') else caller
    called_number = "+" + called if called and not called.startswith('+') else called
    payload = json.dumps({
        "caller_number": caller_number,
        "called_number": called_number,
        "asterisk_call_id": call_id,
    }).encode('utf-8')

    req = urllib.request.Request(
        f"{DJANGO_BASE}/api/twilio/asterisk/route/",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Asterisk-Secret": shared_secret(),
        },
        method="POST",
    )

    def ask_b():
        with urllib.request.urlopen(req, timeout=ROUTE_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))

    fallback = load_fallback()
    try:
        if fallback is not None:
            data = fallback.route(DJANGO_BASE, ask_b, called_number, caller_number, call_id,
                                  log=agi_verbose)
        else:
            data = ask_b()
    except Exception as e:
        agi_verbose(f"Django call failed: {e}")
        agi_set_var("ROUTE_ACTION", "hangup")
        return

    action = data.get("action", "hangup")
    agi_set_var("ROUTE_ACTION", action)

    if action == "dial":
        set_dial_vars(data)
        agi_verbose(f"Routing to {data.get('buyer_number')}")
    else:
        agi_verbose(f"No buyer found: {data.get('reason', 'unknown')}")

if __name__ == '__main__':
    main()

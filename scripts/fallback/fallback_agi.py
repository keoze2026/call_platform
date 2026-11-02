"""The piece route_call.py (the AGI) calls on Server A (CH-464).

    import sys; sys.path.insert(0, '/opt/call_platform/scripts/fallback')
    import fallback_agi
    reply = fallback_agi.route(DJANGO_BASE, ask_b, called, caller, uniqueid)

`ask_b()` is route_call.py's own request to B's route endpoint, returning
B's reply as a dict. route() asks B exactly as before. Only when B cannot be
reached - nothing listening, no answer within the time, a 5xx - does A decide
on its own (avx_fallback.decide). Every reply B gives is noted so A knows
which calls are live. Nothing here can stop a call that B answers: any error
in the fallback code itself is caught and B's reply is returned untouched.

Only a B that does not answer the connection at all (switched off, network
down) makes the next calls skip B for a few seconds, so callers do not each
wait for it. A refused connection or a failed request sends only that one
call to A's routing; the next call asks B again. B sees A's calls within
seconds (fallback_sync.py --loop), so the two never route blind for long.
"""
import os
import socket
import sys
import time
import urllib.error
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avx_fallback as fb  # noqa: E402

PROBE_SECONDS = float(os.environ.get('FALLBACK_PROBE_SECONDS', '1.5'))
DOWN_HOLD = float(os.environ.get('FALLBACK_DOWN_HOLD', '10'))


def _flag():
    return os.path.join(fb.STATE_DIR, 'b_down')


def _mark_down():
    try:
        with open(_flag(), 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _mark_up():
    try:
        os.remove(_flag())
    except OSError:
        pass


def recently_down():
    try:
        return time.time() - os.path.getmtime(_flag()) < DOWN_HOLD
    except OSError:
        return False


def reachable(base):
    """A quick TCP connect to B before the real request: B switched off
    answers nothing, and waiting the full request time on every call would
    hold every caller."""
    if recently_down():
        return False
    u = urlparse(base)
    try:
        socket.create_connection((u.hostname, u.port or (443 if u.scheme == 'https' else 80)),
                                 PROBE_SECONDS).close()
        return True
    except (socket.timeout, TimeoutError):
        _mark_down()            # nothing answers: skip B for a few seconds
        return False
    except OSError:
        return False            # refused (B restarting): this call only


def b_is_down(exc):
    """True when the error means B is not there, not that B said no."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError,
                            ConnectionError, OSError, ValueError))


DECIDE_LOCK_WAIT = 3.0


def _decide_lock():
    """CH-467: A's own decisions one at a time, so two calls deciding at the
    same moment cannot both take a buyer's last free place. Waited on for at
    most DECIDE_LOCK_WAIT; a decision takes about 20 ms."""
    import fcntl
    try:
        f = open(os.path.join(fb.STATE_DIR, '.decide.lock'), 'a')
    except OSError:
        return None
    deadline = time.monotonic() + DECIDE_LOCK_WAIT
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.monotonic() >= deadline:
                return f            # route anyway; never hold up the call
            time.sleep(0.005)


def own_decision(called, caller, uniqueid, log=None):
    lock = _decide_lock()
    try:
        reply = fb.decide(called, caller, uniqueid)
    except Exception as e:  # the fallback itself failed: hang up, as before
        if log:
            log(f'fallback routing failed: {e!r}')
        return {'action': 'hangup', 'reason': 'fallback_error'}
    finally:
        if lock is not None:
            lock.close()            # closing the file releases the lock
    if log:
        log(f"Server B unreachable; routed by Server A: {reply.get('action')} "
            f"{reply.get('buyer_number') or reply.get('reason', '')}")
    return reply


def route(base, ask_b, called, caller, uniqueid, log=None):
    if not reachable(base):
        return own_decision(called, caller, uniqueid, log)
    try:
        reply = ask_b()
    except Exception as e:
        if not b_is_down(e):
            raise
        if log:
            log(f'Server B did not answer: {e!r}')
        return own_decision(called, caller, uniqueid, log)
    _mark_up()
    try:
        fb.note_b_call(called, caller, uniqueid, reply)
    except Exception as e:
        if log:
            log(f'journal note failed (call unaffected): {e!r}')
    return reply

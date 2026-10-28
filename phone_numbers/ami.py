"""A small Asterisk Manager Interface client, enough to place one test call.

Plain socket, no dependency. Only used by the TFN test (CH-175). If the manager
link is not configured (ASTERISK_AMI_HOST empty) `configured()` is False and
the test falls back to the platform check only.
"""
import logging
import socket
import time
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)


class AMIError(Exception):
    pass


def configured() -> bool:
    return bool(getattr(settings, 'ASTERISK_AMI_HOST', '') and getattr(settings, 'ASTERISK_AMI_SECRET', ''))


def _read_block(sock, buf: bytearray, deadline: float):
    """The next complete message (dict), or None when the time is up."""
    while True:
        i = buf.find(b'\r\n\r\n')
        if i >= 0:
            raw = bytes(buf[:i]).decode('utf-8', 'replace')
            del buf[:i + 4]
            msg = {}
            for line in raw.split('\r\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    msg[k.strip()] = v.strip()
            return msg
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        sock.settimeout(min(left, 1.0))
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            raise AMIError('Asterisk closed the manager connection')
        buf.extend(chunk)


def _send(sock, action: dict):
    lines = ''.join(f'{k}: {v}\r\n' for k, v in action.items()) + '\r\n'
    sock.sendall(lines.encode())


class AMIClient:
    def __init__(self):
        self.host = settings.ASTERISK_AMI_HOST
        self.port = int(getattr(settings, 'ASTERISK_AMI_PORT', 5038))
        self.sock = None
        self.buf = bytearray()

    def __enter__(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
        except OSError as e:
            raise AMIError(f'cannot reach Asterisk manager at {self.host}:{self.port} ({e})')
        # Banner line: "Asterisk Call Manager/x.y.z"
        self.sock.settimeout(5)
        try:
            self.sock.recv(256)
        except OSError as e:
            raise AMIError(f'no banner from Asterisk manager ({e})')
        _send(self.sock, {
            'Action': 'Login',
            'Username': settings.ASTERISK_AMI_USER,
            'Secret': settings.ASTERISK_AMI_SECRET,
            'Events': 'call',
        })
        reply = _read_block(self.sock, self.buf, time.monotonic() + 5)
        if not reply or reply.get('Response') != 'Success':
            raise AMIError('Asterisk manager login refused')
        return self

    def __exit__(self, *exc):
        try:
            _send(self.sock, {'Action': 'Logoff'})
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def originate(self, channel: str, caller_id: str, timeout_ms: int = 25000, wait_seconds: int = 20) -> str:
        """Queue an outbound call and return its ActionID. The call is async:
        what happens to it arrives later as an OriginateResponse event."""
        action_id = uuid.uuid4().hex
        _send(self.sock, {
            'Action': 'Originate',
            'ActionID': action_id,
            'Channel': channel,
            'Application': 'Wait',
            'Data': str(wait_seconds),
            'CallerID': f'"TFN Test" <{caller_id}>',
            'Timeout': timeout_ms,
            'Async': 'true',
        })
        deadline = time.monotonic() + 5
        while True:
            msg = _read_block(self.sock, self.buf, deadline)
            if msg is None:
                raise AMIError('no answer to the originate request')
            if msg.get('ActionID') == action_id and msg.get('Response'):
                if msg['Response'] != 'Success':
                    raise AMIError(msg.get('Message', 'originate refused'))
                return action_id

    def wait_event(self, name: str, action_id: str, seconds: float):
        """Wait up to `seconds` for an event of this name for this action."""
        deadline = time.monotonic() + seconds
        while True:
            msg = _read_block(self.sock, self.buf, deadline)
            if msg is None:
                return None
            if msg.get('Event') == name and msg.get('ActionID') == action_id:
                return msg

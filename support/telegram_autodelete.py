"""CH-332: the bot's notifications on Telegram are deleted after one week
(Sir, 28 Oct).

Every notification the bot sends to the team's group (server alerts from
scripts/host_watch.py; status warnings, portal changes and /status answers
from support/system_status.py; the EOD report; the watchdog's outage alerts)
is noted here with its chat and message id. Once an hour (celery beat
`telegram-autodelete`) the ones older than TELEGRAM_AUTODELETE_DAYS are
deleted from the chat.

.env (both optional):
  TELEGRAM_AUTODELETE_DAYS   days a notification stays (default 7; 0 = never delete)
  TELEGRAM_AUTODELETE_KEEP   kinds never deleted, comma separated, from:
                             server, status, eod, watchdog, command
                             e.g. TELEGRAM_AUTODELETE_KEEP=eod keeps the EOD reports

Only group chats are handled (chat ids starting with "-"): in a private chat
Telegram never lets a bot delete a message older than 48 hours.

In a group, Telegram lets the bot delete messages older than 48 hours only
when the bot is an admin there with the "Delete messages" right. /status says
whether it has it ("Telegram auto-delete" in section 7). A delete Telegram
refuses is tried again once a day and given up after 30 days, so making the
bot an admin later still clears the backlog.

Standard library only: the host scripts use it outside Django. The list is
run/telegram_sent.jsonl (the containers see the same folder as /app/run),
one JSON line per message; writers and the cleaner lock the file.

    python3 support/telegram_autodelete.py            # how many are noted / due
    python3 support/telegram_autodelete.py --prune    # delete what is due now
"""
import fcntl
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, 'run', 'telegram_sent.jsonl')
ENV_FILE = os.path.join(ROOT, '.env')
DAY = 86400
GIVE_UP_DAYS = 30
KINDS = ('server', 'status', 'eod', 'watchdog', 'command')


def _setting(name, default=''):
    """The environment first (the containers), then .env (the host scripts)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in open(ENV_FILE):
            if line.startswith(name + '='):
                return line.split('=', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return default


def days() -> int:
    try:
        return int(_setting('TELEGRAM_AUTODELETE_DAYS', '7'))
    except ValueError:
        return 7


def kept_kinds() -> set:
    return {k.strip().lower() for k in _setting('TELEGRAM_AUTODELETE_KEEP', '').split(',') if k.strip()}


def _append(chat, message_id, kind) -> bool:
    chat = str(chat or '')
    if not chat.startswith('-') or not message_id or days() <= 0 or kind in kept_kinds():
        return False
    line = json.dumps({'chat': chat, 'id': int(message_id), 'kind': kind, 'at': int(time.time())}) + '\n'
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
    return True


def remember(chat, answer, kind='status') -> bool:
    """Note one message the bot sent, from Telegram's sendMessage answer (the
    response body as text, bytes or dict). Never raises: a message that cannot
    be noted is only not deleted later."""
    try:
        data = json.loads(answer) if isinstance(answer, (str, bytes, bytearray)) else (answer or {})
        msg = data.get('result') or {}
        if not data.get('ok') or not isinstance(msg, dict):
            return False
        return _append((msg.get('chat') or {}).get('id', chat), msg.get('message_id'), kind)
    except Exception:  # noqa: BLE001
        return False


def remember_incoming(chat, message_id, kind='command') -> bool:
    """A team member's /status in the group goes with the bot's answer."""
    try:
        return _append(chat, message_id, kind)
    except Exception:  # noqa: BLE001
        return False


def _parse(raw):
    out = []
    for line in raw.splitlines():
        try:
            e = json.loads(line)
            if e.get('chat') and e.get('id'):
                out.append(e)
        except ValueError:
            continue
    return out


def entries():
    try:
        with open(LOG) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            return _parse(f.read())
    except OSError:
        return []


def _delete(token, chat, message_id) -> str:
    """'ok'; 'gone' (already deleted, or the bot is no longer in the chat);
    'refused' (Telegram does not allow it: the bot is not an admin with
    Delete messages); 'error' (network etc.: tried again next hour)."""
    body = urllib.parse.urlencode({'chat_id': chat, 'message_id': message_id}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/deleteMessage', data=body)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 'ok' if json.loads(resp.read() or b'{}').get('ok') else 'error'
    except urllib.error.HTTPError as e:
        try:
            why = (json.loads(e.read() or b'{}').get('description') or '').lower()
        except ValueError:
            why = ''
        if e.code == 403 or 'not found' in why:
            return 'gone'
        if e.code == 400 and ("can't be deleted" in why or 'not enough rights' in why):
            return 'refused'
        return 'error'
    except Exception:  # noqa: BLE001
        return 'error'


def prune(token, now=None, delete=None) -> dict:
    """Delete the noted messages that are due. Returns the counts."""
    keep_days = days()
    if keep_days <= 0 or not token:
        return {'off': True}
    now = now or time.time()
    delete = delete or _delete
    try:
        with open(LOG) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            raw = f.read()
    except OSError:
        return {'deleted': 0, 'gone': 0, 'refused': 0, 'errors': 0, 'waiting': 0}
    size = len(raw.encode())
    keep, done = [], {'deleted': 0, 'gone': 0, 'refused': 0, 'errors': 0}
    for e in _parse(raw):
        age = now - e.get('at', now)
        if age < keep_days * DAY or e.get('retry_at', 0) > now:
            keep.append(e)
            continue
        res = delete(token, e['chat'], e['id'])
        if res == 'ok':
            done['deleted'] += 1
        elif res == 'gone':
            done['gone'] += 1
        elif res == 'refused':
            done['refused'] += 1
            if age < GIVE_UP_DAYS * DAY:
                keep.append({**e, 'retry_at': int(now + DAY)})
        else:
            done['errors'] += 1
            keep.append(e)
    # Write back what is left, plus whatever was sent while we were deleting.
    newer = ''
    with open(LOG, 'a+b') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        newer = f.read()[size:].decode(errors='ignore')
        f.seek(0)
        f.truncate()
        f.write((''.join(json.dumps(e) + '\n' for e in keep) + newer).encode())
    done['waiting'] = len(keep) + len(_parse(newer))
    return done


def bot_can_delete(token, chat) -> str:
    """'yes', 'no' (not an admin with Delete messages) or 'unknown'."""
    try:
        q = urllib.parse.urlencode({'chat_id': chat, 'user_id': token.split(':', 1)[0]})
        with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getChatMember?{q}', timeout=10) as resp:
            m = json.loads(resp.read() or b'{}').get('result') or {}
        if m.get('status') == 'creator' or (m.get('status') == 'administrator' and m.get('can_delete_messages')):
            return 'yes'
        return 'no' if m.get('status') else 'unknown'
    except Exception:  # noqa: BLE001
        return 'unknown'


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--remember':
        # deploy/watchdog.sh: --remember <chat> <kind>, Telegram's answer on stdin
        remember(sys.argv[2] if len(sys.argv) > 2 else '', sys.stdin.read(),
                 sys.argv[3] if len(sys.argv) > 3 else 'watchdog')
    elif '--prune' in sys.argv:
        print(json.dumps(prune(_setting('TELEGRAM_BOT_TOKEN'))))
    else:
        noted = entries()
        due = sum(1 for e in noted if time.time() - e.get('at', 0) >= days() * DAY)
        print(f'{len(noted)} message(s) noted, {due} due for deletion '
              f'(after {days()} days; 0 = off)')

"""CH-669: support chat visitors who only say "hi" (12 Nov).

  "For any visitor who is texting Hi Hi every time with nothing to say: if
   he/she says Hi more than 3 times without adding a word or the help he
   needs, prevent him from sending more chats to our system."
  - 24 hours; greeting again within the 24 hours: for good.
  - No further greetings, but a real question still gets through.
  - Nobody blocks or unblocks: the system knows it is the same person.
  - Only when someone is blocked: his location and device in the Non
    Priority group.

The same person: each new chat has a new code, so a person is the same chat,
OR the same browser (a random id the chat keeps in the browser), OR the same
internet address. A greeting is a message of greeting words only ("hi",
"hiii", "hello", "good morning", "hi team" ...) or nothing readable at all
(an emoji, "?"). Anything else is a real message: always sent, and it starts
the count again. Nothing is deleted: a held greeting is kept with the chat,
marked held, and not sent to the team.
"""
import logging
import re
from datetime import timedelta
from html import escape

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

DAY, FOREVER = 'day', 'forever'
NOTICE = 'Please tell us what you need help with.'

_GREETING = re.compile(
    r'^(h+i+|h+a+i+|h+y+|h+e+y+|h+e+l+o+w?|h+a+l+o+|h+l+o+|h+o+l+a+|y+o+|s+u+p+|w+a+s+u+p+|h+o+w+d+y+|g+m+'
    r'|g+r+e+e+t+i+n+g+s?|morning|afternoon|evening|hii+ya+|hiya)$')
# Words that may come with a greeting without saying anything.
_FILLER = {
    'good', 'day', 'there', 'dear', 'sir', 'madam', 'maam', 'team', 'support', 'avortyx', 'avortyxsupport',
    'guys', 'all', 'everyone', 'bro', 'friend', 'again', 'is', 'anyone', 'anybody', 'someone', 'here',
    'u', 'you', 'ok', 'okay', 'pls', 'please', 'admin', 'agent', 'and', 'a', 'the', 'to', 'oh', 'hmm',
}


def is_greeting(text: str) -> bool:
    """Only greeting words (or nothing readable): nothing said yet."""
    words = re.findall(r"[a-z]+", (text or '').lower().replace("'", ''))
    if not words:
        return True
    return any(_GREETING.match(w) for w in words) and all(_GREETING.match(w) or w in _FILLER for w in words)


def auto_reply(session):
    """The answer to a held greeting, in the chat itself as a message from
    the team (12 Nov: a pop-up notice was missed) - never sent to Telegram."""
    from support.models import SupportMessage
    SupportMessage.objects.create(session=session, sender='agent', message=NOTICE)


def _person(session):
    """Every chat of this person: the same chat, browser or internet address."""
    from support.models import SupportSession
    q = Q(id=session.id)
    if session.browser_id:
        q |= Q(browser_id=session.browser_id)
    if session.client_ip:
        q |= Q(client_ip=session.client_ip)
    return SupportSession.objects.filter(q)


def check(session, text: str) -> tuple:
    """(greeting, held) for a new visitor message on `session` - before it is
    saved. Sets the block and tells the Non Priority group when one starts."""
    from accounts import rls
    from support.models import SupportMessage
    if not is_greeting(text):
        return False, False
    now = timezone.now()
    with rls.system():
        chats = _person(session)
        blocked = list(chats.filter(Q(greeting_block=FOREVER)
                                    | Q(greeting_block=DAY, greeting_block_until__gt=now)))
        if any(c.greeting_block == FOREVER for c in blocked):
            return True, True
        if blocked:
            # Greeting again within the 24 hours: for good.
            _block(session, FOREVER, now, 'greeted again during the 24-hour block')
            return True, True
        hours = max(1, int(getattr(settings, 'SUPPORT_GREETING_BLOCK_HOURS', 24) or 24))
        since = now - timedelta(hours=24)
        last_real = (SupportMessage.objects.filter(session__in=chats, sender='visitor', greeting=False)
                     .order_by('-created_at').values_list('created_at', flat=True).first())
        if last_real and last_real > since:
            since = last_real
        sent = SupportMessage.objects.filter(session__in=chats, sender='visitor', greeting=True, held=False,
                                             created_at__gt=since).count()
        limit = max(1, int(getattr(settings, 'SUPPORT_GREETING_LIMIT', 3) or 3))
        if sent < limit:
            return True, False
        before = chats.exclude(greeting_block='').exists()
        if before:
            _block(session, FOREVER, now, f'{limit + 1} greetings with nothing else, blocked before')
        else:
            _block(session, DAY, now, f'{limit + 1} greetings with nothing else', until=now + timedelta(hours=hours))
        return True, True


def _block(session, level, now, why, until=None):
    from support.models import SupportSession
    SupportSession.objects.filter(pk=session.pk).update(greeting_block=level, greeting_block_until=until,
                                                        updated_at=now)
    session.greeting_block, session.greeting_block_until = level, until
    try:
        tell(session, level, why)
    except Exception:  # noqa: BLE001 - the block stands even if Telegram fails
        logger.exception('greeting block notice not sent: %s', session.pk)


def tell(session, level, why):
    from support import nonpriority
    hours = int(getattr(settings, 'SUPPORT_GREETING_BLOCK_HOURS', 24) or 24)
    head = f'for {hours} hours' if level == DAY else 'for good'
    who = escape(session.name or 'Anonymous') + (f' ({escape(session.email)})' if session.email else '')
    text = (f'🚫 <b>Support chat: greetings blocked {head}</b>\n'
            f'👤 {who}\n'
            f'💬 Chat <code>{str(session.id)[:8]}</code> - {escape(why)}\n'
            f'📍 Location: {escape(location(session.client_ip))}\n'
            f'💻 Device: {escape(device(session.user_agent))}\n'
            f'Real questions from this person still reach the team.')
    nonpriority.post(text)


def location(ip: str) -> str:
    """City, region, country of an internet address (approximate - the
    internet provider's place, not GPS), and the address."""
    import ipaddress

    import requests
    if not ip:
        return 'unknown'
    try:
        if not ipaddress.ip_address(ip).is_global:
            return f'private network ({ip})'
    except ValueError:
        return f'unknown ({ip})'
    token = getattr(settings, 'IPINFO_TOKEN', '')
    try:
        r = requests.get(f'https://ipinfo.io/{ip}/json', params={'token': token} if token else None, timeout=4)
        d = r.json() if r.ok else {}
    except Exception:  # noqa: BLE001
        d = {}
    place = ', '.join(x for x in (d.get('city'), d.get('region'), d.get('country')) if x)
    org = f" - {d['org']}" if d.get('org') else ''
    return f'{place or "unknown"}{org} (IP {ip})'


def device(ua: str) -> str:
    """'Chrome on Windows', 'Safari on iPhone' ... from the browser's own
    description of itself."""
    ua = ua or ''
    if not ua:
        return 'unknown'
    if 'iPhone' in ua:
        os_ = 'iPhone'
    elif 'iPad' in ua:
        os_ = 'iPad'
    elif 'Android' in ua:
        m = re.search(r'Android ([\d.]+)', ua)
        os_ = f'Android {m.group(1)}' if m else 'Android'
    elif 'Windows NT 10.0' in ua:
        os_ = 'Windows 10/11'
    elif 'Windows' in ua:
        os_ = 'Windows'
    elif 'Mac OS X' in ua or 'Macintosh' in ua:
        os_ = 'Mac'
    elif 'CrOS' in ua:
        os_ = 'Chromebook'
    elif 'Linux' in ua:
        os_ = 'Linux'
    else:
        os_ = 'unknown system'
    for name, key in (('Edge', 'Edg/'), ('Opera', 'OPR/'), ('Samsung Internet', 'SamsungBrowser/'),
                      ('Firefox', 'Firefox/'), ('Chrome', 'CriOS/'), ('Chrome', 'Chrome/'),
                      ('Firefox', 'FxiOS/'), ('Safari', 'Safari/')):
        if key in ua:
            m = re.search((r'Version/' if name == 'Safari' else re.escape(key)) + r'(\d+)', ua)
            return f'{name}{" " + m.group(1) if m else ""} on {os_}'
    return f'{ua[:60]} on {os_}'

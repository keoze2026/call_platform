"""The team report from our own reports bot (CH-347, Sir 28 Oct).

Sir: "add an automated reporting bot system in-house which gets its own
Telegram ID to send automated reports". This is a second Telegram bot, made by
us in BotFather and run by this server, that does one job: the team report.
The support bot (TELEGRAM_BOT_TOKEN) keeps support chats and alerts.

How a chat is connected - nobody looks up or types a chat ID:
  1. On the Automated Report page an admin presses "Connect Telegram".
  2. The page shows our bot's link: t.me/<bot>?start=team_<code> (own chat) or
     ?startgroup=team_<code> (a group).
  3. Telegram sends the bot "/start team_<code>" from that chat; the webhook
     (team_report_api.webhook) saves the chat for the workspace the code was
     made for and answers "Connected".
  A code works once, for 7 days, and lives only in the cache.

What is sent, every day at the chat's time (Eastern, 9:00 AM by default):
  - a short summary of the previous day (Eastern): calls, connected, converted,
    revenue, payout, profit, and the top campaigns;
  - one Excel file: Summary, By Campaign, By Publisher, By Buyer.
Figures come from the same call records as the Reports page. TFN test calls
(caller "TFN Test") are left out. A failed send is tried again on the next run,
three times a day at most.
"""
import hashlib
import html
import logging
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo('America/New_York')
PREFIX = 'team_'
LINK_LIFETIME = timedelta(days=7)
MAX_ATTEMPTS = 3
TOP_CAMPAIGNS = 5
_LINK_KEY = 'team_report_connect:{}'
ZERO = Decimal('0')
CONNECTED = ('completed', 'in_progress')


class TeamReportError(Exception):
    pass


# ── the bot ──────────────────────────────────────────────────────────────────

def bot_token() -> str:
    return (getattr(settings, 'REPORTS_BOT_TOKEN', '') or '').strip()


def configured() -> bool:
    return bool(bot_token())


def webhook_secret() -> str:
    """The secret Telegram sends back on every update (setWebhook
    secret_token): REPORTS_BOT_WEBHOOK_SECRET, or one worked out from the
    token so there is nothing else to set."""
    explicit = (getattr(settings, 'REPORTS_BOT_WEBHOOK_SECRET', '') or '').strip()
    if explicit:
        return explicit
    token = bot_token()
    return hashlib.sha256(f'avortyx-team-report:{token}'.encode()).hexdigest()[:48] if token else ''


def telegram(method: str, data: dict, files=None):
    import requests
    token = bot_token()
    if not token:
        raise TeamReportError('The reports bot is not set up on the server (REPORTS_BOT_TOKEN).')
    r = requests.post(f'https://api.telegram.org/bot{token}/{method}', data=data, files=files, timeout=30)
    if r.status_code != 200:
        try:
            why = r.json().get('description') or r.text
        except Exception:
            why = r.text
        low = why.lower()
        if 'chat not found' in low:
            why = 'Telegram cannot find this chat. Press Start on the reports bot again, or add it to the group.'
        elif 'blocked' in low or 'kicked' in low:
            why = 'The reports bot was blocked or removed from this chat.'
        raise TeamReportError(why[:200])
    return r


def bot_username() -> str:
    """The reports bot's name, without @: REPORTS_BOT_USERNAME, else asked
    from Telegram once a day."""
    name = (getattr(settings, 'REPORTS_BOT_USERNAME', '') or '').strip().lstrip('@')
    if name or not configured():
        return name
    name = cache.get('team_report_bot_username')
    if name is None:
        name = ''
        try:
            name = telegram('getMe', {}).json()['result']['username']
        except Exception:
            logger.warning('could not read the reports bot name from Telegram')
        cache.set('team_report_bot_username', name, 86400 if name else 600)
    return name


def reply(chat_id, text: str) -> None:
    """One HTML message to a chat. Logged, never raised: used from the webhook."""
    try:
        telegram('sendMessage', {'chat_id': str(chat_id), 'text': text, 'parse_mode': 'HTML',
                                 'disable_web_page_preview': 'true'})
    except Exception:
        logger.exception('reports bot: could not reply to %s', chat_id)


# ── connecting a chat ────────────────────────────────────────────────────────

def make_link(organization, user=None) -> dict:
    if not configured():
        raise TeamReportError('The reports bot is not set up on the server yet (REPORTS_BOT_TOKEN).')
    bot = bot_username()
    if not bot:
        raise TeamReportError('The reports bot name is not known yet. Set REPORTS_BOT_USERNAME in .env.')
    code = secrets.token_urlsafe(12)
    cache.set(_LINK_KEY.format(code), {'org': str(organization.id), 'user': str(getattr(user, 'id', '') or '')},
              int(LINK_LIFETIME.total_seconds()))
    payload = PREFIX + code
    return {
        'bot': f'@{bot}',
        'url': f'https://t.me/{bot}?start={payload}',
        'group_url': f'https://t.me/{bot}?startgroup={payload}',
        'command': f'/start {payload}',
        'expires_at': (timezone.now() + LINK_LIFETIME).isoformat(),
    }


def connect(payload: str, chat: dict):
    """Save the chat for the workspace the code belongs to. Returns the
    TeamReportChat, or None for an unknown or used code."""
    from accounts.models import Organization, User
    from analytics.models import TeamReportChat
    code = payload[len(PREFIX):] if payload.startswith(PREFIX) else ''
    found = cache.get(_LINK_KEY.format(code)) if code else None
    chat_id = str((chat or {}).get('id', '') or '')
    if not found or not chat_id:
        return None
    org = Organization.objects.filter(id=found.get('org')).first()
    if org is None:
        return None
    # One use: a forwarded link cannot later move the report to another chat.
    cache.delete(_LINK_KEY.format(code))
    user = User.objects.filter(id=found.get('user')).first() if found.get('user') else None
    title = chat.get('title') or ' '.join(x for x in (chat.get('first_name'), chat.get('last_name')) if x) \
        or (('@' + chat['username']) if chat.get('username') else '')
    row, _ = TeamReportChat.objects.update_or_create(
        organization=org, chat_id=chat_id[:64],
        defaults={'title': (title or '')[:255], 'kind': (chat.get('type') or '')[:16],
                  'connected_by': user, 'enabled': True, 'attempts': 0, 'last_result': ''})
    return row


def disconnect_chat(chat_id) -> int:
    """The chat said /stop, or removed the bot: no more reports there."""
    from analytics.models import TeamReportChat
    return TeamReportChat.objects.filter(chat_id=str(chat_id), enabled=True).update(enabled=False)


# ── the report ───────────────────────────────────────────────────────────────

def previous_day(now=None):
    """(day, start, end): the whole previous day, Eastern."""
    now = (now or timezone.now()).astimezone(EASTERN)
    day = now.date() - timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=EASTERN)
    return day, start, start + timedelta(days=1)


def today_so_far(now=None):
    now = (now or timezone.now()).astimezone(EASTERN)
    day = now.date()
    return day, datetime.combine(day, time.min, tzinfo=EASTERN), now


def _calls(organization, start, end):
    from analytics.models import CallRecord
    return (CallRecord.objects.filter(organization=organization, created_at__gte=start, created_at__lt=end)
            .exclude(twilio_call_sid__startswith='tfntest-'))


def _figures(qs) -> dict:
    agg = qs.aggregate(
        calls=Count('id'),
        connected=Count('id', filter=Q(status__in=CONNECTED)),
        converted=Count('id', filter=Q(is_converted=True)),
        revenue=Coalesce(Sum('revenue'), ZERO),
        payout=Coalesce(Sum('payout'), ZERO),
        profit=Coalesce(Sum('profit'), ZERO),
        avg_duration=Avg('duration_seconds', filter=Q(status__in=CONNECTED)),
    )
    agg['avg_duration'] = int(round(agg['avg_duration'] or 0))
    return agg


def _by(qs, field):
    rows = []
    for r in (qs.values(field)
              .annotate(calls=Count('id'),
                        connected=Count('id', filter=Q(status__in=CONNECTED)),
                        converted=Count('id', filter=Q(is_converted=True)),
                        revenue=Coalesce(Sum('revenue'), ZERO),
                        payout=Coalesce(Sum('payout'), ZERO),
                        profit=Coalesce(Sum('profit'), ZERO),
                        avg_duration=Avg('duration_seconds', filter=Q(status__in=CONNECTED)))
              .order_by('-calls')):
        r['name'] = (r.pop(field) or '').strip() or '(none)'
        r['avg_duration'] = int(round(r['avg_duration'] or 0))
        rows.append(r)
    return rows


def _pct(part, whole) -> str:
    return f'{round(part / whole * 100)}%' if whole else '0%'


def _money(v) -> str:
    return f'${Decimal(v or 0):,.2f}'


def _mmss(seconds) -> str:
    s = int(seconds or 0)
    return f'{s // 60}:{s % 60:02d}'


HEADER = ['Name', 'Calls', 'Connected', 'Connected %', 'Converted', 'Conversion %',
          'Revenue', 'Payout', 'Profit', 'Avg Duration (connected)']


def _row(r) -> list:
    return [r['name'], r['calls'], r['connected'], _pct(r['connected'], r['calls']), r['converted'],
            _pct(r['converted'], r['calls']), float(r['revenue']), float(r['payout']), float(r['profit']),
            _mmss(r['avg_duration'])]


def build(organization, start, end) -> dict:
    qs = _calls(organization, start, end)
    total = _figures(qs)
    return {
        'total': total,
        'campaigns': _by(qs, 'campaign_name'),
        'publishers': _by(qs, 'publisher_name'),
        'buyers': _by(qs, 'buyer_name'),
    }


def workbook(data: dict, title: str) -> bytes:
    from buyers.xlsx import write_xlsx_sheets
    t = data['total']
    summary = [
        ['Period', title],
        ['Calls', t['calls']],
        ['Connected', t['connected']],
        ['Connected %', _pct(t['connected'], t['calls'])],
        ['Converted', t['converted']],
        ['Conversion %', _pct(t['converted'], t['calls'])],
        ['Revenue', float(t['revenue'])],
        ['Payout', float(t['payout'])],
        ['Profit', float(t['profit'])],
        ['Avg Duration (connected)', _mmss(t['avg_duration'])],
    ]
    return write_xlsx_sheets([
        ('Summary', ['Item', 'Value'], summary),
        ('By Campaign', ['Campaign'] + HEADER[1:], [_row(r) for r in data['campaigns']]),
        ('By Publisher', ['Publisher'] + HEADER[1:], [_row(r) for r in data['publishers']]),
        ('By Buyer', ['Buyer'] + HEADER[1:], [_row(r) for r in data['buyers']]),
    ])


def message(organization, data: dict, title: str, test=False) -> str:
    t = data['total']
    e = html.escape
    bar = '▍'
    lines = []
    if test:
        lines.append('<i>Test - sent now on request</i>')
    lines += [f'<b>{bar} AVORTYX | Team Report</b>', f'<b>{e(organization.name)}</b> · {e(title)}', '']
    if not t['calls']:
        lines.append('No calls in this period.')
        return '\n'.join(lines)
    lines += [
        f'<b>{t["calls"]:,}</b> calls · <b>{t["connected"]:,}</b> connected ({_pct(t["connected"], t["calls"])})',
        f'<b>{t["converted"]:,}</b> converted ({_pct(t["converted"], t["calls"])})',
        f'Avg duration (connected): {_mmss(t["avg_duration"])}',
        '',
        f'Revenue: <b>{_money(t["revenue"])}</b>',
        f'Payout: <b>{_money(t["payout"])}</b>',
        f'Profit: <b>{_money(t["profit"])}</b>',
    ]
    top = data['campaigns'][:TOP_CAMPAIGNS]
    if top:
        lines += ['', f'<b>{bar} Top campaigns</b>']
        for r in top:
            lines.append(f'{e(r["name"])}: {r["calls"]:,} calls, {r["connected"]:,} connected, {_money(r["revenue"])}')
    lines += ['', 'Full breakdown by campaign, publisher and buyer in the Excel file below.']
    return '\n'.join(lines)


def send(chat, now=None, test=False) -> str:
    """Build and send the report to one TeamReportChat. Raises TeamReportError."""
    now = now or timezone.now()
    if test:
        day, start, end = today_so_far(now)
        title = f'Today so far, {day:%a %d %b %Y} until {end.astimezone(EASTERN):%-I:%M %p} EST'
    else:
        day, start, end = previous_day(now)
        title = f'{day:%a %d %b %Y} (EST)'
    data = build(chat.organization, start, end)
    telegram('sendMessage', {'chat_id': chat.chat_id, 'text': message(chat.organization, data, title, test),
                             'parse_mode': 'HTML', 'disable_web_page_preview': 'true'})
    if data['total']['calls']:
        telegram('sendDocument', {'chat_id': chat.chat_id, 'caption': f'Team report · {day:%d %b %Y}'},
                 files={'document': (f'Avortyx Team Report {day:%Y-%m-%d}.xlsx', workbook(data, title),
                                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    return f'Sent {"test " if test else ""}report for {day:%d %b}: {data["total"]["calls"]} calls'


def send_due(now=None) -> dict:
    """Send every chat's report whose time has come today and has not gone yet."""
    from analytics.models import TeamReportChat
    done = {'sent': 0, 'failed': 0}
    if not configured():
        return done
    now = now or timezone.now()
    local = now.astimezone(EASTERN)
    today = local.date()
    due = (TeamReportChat.objects.select_related('organization')
           .filter(enabled=True, send_time__lte=local.time().replace(tzinfo=None))
           .exclude(last_sent_on=today))
    for chat in due:
        if chat.attempts_on == today and chat.attempts >= MAX_ATTEMPTS:
            continue
        try:
            chat.last_result = send(chat, now)[:255]
            chat.last_sent_on, chat.last_sent_at = today, now
            chat.attempts_on, chat.attempts = today, 0
            done['sent'] += 1
        except Exception as e:
            if chat.attempts_on != today:
                chat.attempts_on, chat.attempts = today, 0
            chat.attempts += 1
            chat.last_result = f'Not sent: {e}'[:255]
            done['failed'] += 1
            logger.warning('team report to %s not sent: %s', chat.chat_id, e)
        chat.save(update_fields=['last_sent_on', 'last_sent_at', 'last_result', 'attempts_on', 'attempts'])
    return done


# ── what the bot says ────────────────────────────────────────────────────────

WELCOME = (
    '\U0001F44B <b>Avortyx Reports</b>\n\n'
    'I send the Avortyx team report here automatically.\n\n'
    'To connect this chat, open the Avortyx portal → <b>Automated Report</b> → '
    '<b>Connect Telegram</b>, then tap the link.'
)
CONNECTED_TEXT = (
    '✅ <b>Connected.</b> The Avortyx team report for <b>{org}</b> will arrive here every day at '
    '{time} EST.\n\nSend /stop to stop it.'
)
STOPPED_TEXT = '⏹ The team report is stopped for this chat. Connect it again from the portal any time.'
BAD_CODE_TEXT = ('This link has expired or was already used. Open the Avortyx portal → '
                 '<b>Automated Report</b> and press <b>Connect Telegram</b> for a new one.')


def _command(word: str) -> str:
    """'/start@AvortyxReportsBot' -> '/start' (Telegram adds the bot's name in groups)."""
    return word.split('@', 1)[0].lower()


def handle_update(data: dict) -> None:
    """One update from Telegram for the reports bot."""
    member = data.get('my_chat_member') or {}
    if member:
        chat = member.get('chat') or {}
        status = (member.get('new_chat_member') or {}).get('status', '')
        if status in ('left', 'kicked'):
            disconnect_chat(chat.get('id', ''))
        return
    msg = data.get('message') or data.get('channel_post') or {}
    text = (msg.get('text') or '').strip()
    chat = msg.get('chat') or {}
    if not text or not chat.get('id'):
        return
    parts = text.split()
    word = _command(parts[0])
    if word == '/start':
        payload = parts[1] if len(parts) > 1 else ''
        if payload.startswith(PREFIX):
            row = connect(payload, chat)
            if row is None:
                reply(chat['id'], BAD_CODE_TEXT)
            else:
                reply(chat['id'], CONNECTED_TEXT.format(org=html.escape(row.organization.name),
                                                        time=row.send_time.strftime('%-I:%M %p')))
            return
        reply(chat['id'], WELCOME)
    elif word == '/stop':
        disconnect_chat(chat['id'])
        reply(chat['id'], STOPPED_TEXT)
    elif word in ('/help', '/id'):
        reply(chat['id'], WELCOME + f"\n\nThis chat's ID: <code>{html.escape(str(chat['id']))}</code>")

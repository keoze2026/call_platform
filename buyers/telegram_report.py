"""A buyer's daily report on Telegram (CH-258, Sir 24 Oct).

At the buyer's send time (Eastern) the platform builds one Excel file per
destination of the buyer - the calls forwarded to that destination's TFN -
and sends each file to the buyer's Telegram chat, after a short summary. Sent
once a day; a failed send is tried again on the next run, three times at most.

The buyer's own settings are applied to every file:
  no_recording_link   the Recording column is left out
  connected_only      only calls the buyer answered
  hide_fail_reason    the Fail Reason column is left out
  column_names        its own column headings
  comment_rewrites    its own wording for Fail Reason comments

A comment rewrite changes words, never what happened: a comment may not be
rewritten to say a call connected, completed or converted (see
`check_rewrites`), and Status always reads what the call did. The report goes
to the buyer's own client, who may pay by it.
"""
import logging
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from analytics.models import CallRecord
from buyers.xlsx import write_xlsx
from routing.recordings import public_recording_url

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo('America/New_York')
# A send time before this reports the whole previous day; later, the day so far.
MORNING_CUTOFF = time(6, 0)
MAX_ATTEMPTS = 3

# Column key -> default heading, in file order: every export column a buyer's
# client may see (CH-267). Our own figures - Payout, Profit, Cost, Billed
# Minutes - are never offered.
COLUMNS = (
    ('date', 'Date (EST)'),
    ('call_id', 'Call ID'),
    ('caller', 'Caller ID'),
    ('state', 'State'),
    ('carrier', 'Carrier'),
    ('called_number', 'Called Number'),
    ('campaign', 'Campaign'),
    ('destination_name', 'Destination Name'),
    ('destination', 'Destination'),
    ('publisher', 'Publisher'),
    ('status', 'Status'),
    ('duration', 'Duration'),
    # No TTC (CH-325, Sir): these files go to the buyer, and a buyer never sees
    # TTC - it differs from their own by the 2-5 s a call takes to connect.
    ('fail_reason', 'Fail Reason'),
    ('qualified', 'Qualified'),
    ('converted', 'Converted'),
    ('repeated', 'Repeated'),
    ('revenue', 'Revenue'),
    ('recording', 'Recording'),
)
# Ticked for a new report: the report as it was first built.
DEFAULT_COLUMNS = ('date', 'caller', 'destination', 'status', 'duration', 'fail_reason', 'recording')
COLUMN_KEYS = {k for k, _ in COLUMNS}

ANSWERED = ('completed', 'in_progress')
STATUS_LABEL = {
    'completed': 'Completed',
    'in_progress': 'Live',
    'no_answer': 'No Answer',
    'busy': 'Busy',
    'failed': 'Failed',
    'voicemail': 'Voicemail',
}

# A rewrite may not claim a connection the call did not have.
_CLAIMS = re.compile(r'\b(connected|completed|converted|answered|live|success(ful)?)\b', re.I)
_NEGATED = re.compile(r"\b(not|no|un\w*|never|didn'?t|wasn'?t)\b", re.I)


class ReportError(ValueError):
    pass


def check_rewrites(rewrites) -> dict:
    """Clean a {comment: new wording} map. Raises ReportError for a rewrite
    that would make a comment say the call connected."""
    if not isinstance(rewrites, dict):
        raise ReportError('Comment wording must be a list of "from" and "to" pairs.')
    out = {}
    for src, dst in rewrites.items():
        src, dst = str(src or '').strip(), str(dst or '').strip()
        if not src:
            continue
        if len(src) > 100 or len(dst) > 100:
            raise ReportError('A comment can be at most 100 characters.')
        if _CLAIMS.search(dst) and not _NEGATED.search(dst):
            raise ReportError(
                f'"{dst}" would say the call connected. Comment wording can change the words, '
                f'not what happened - a missed call stays missed in the report.'
            )
        out[src] = dst
    return out


def check_column_names(names) -> dict:
    if not isinstance(names, dict):
        raise ReportError('Column names must be a list of column and heading pairs.')
    out = {}
    for key, label in names.items():
        label = str(label or '').strip()
        if key not in COLUMN_KEYS:
            raise ReportError(f'Unknown column "{key}".')
        if len(label) > 40:
            raise ReportError('A column heading can be at most 40 characters.')
        if label:
            out[key] = label
    return out


def _digits10(number: str) -> str:
    return ''.join(c for c in str(number or '') if c.isdigit())[-10:]


def _caller(number: str) -> str:
    from phone_numbers.caller_word import caller_word
    word = caller_word(number)   # "TFN Test" (CH-344)
    if word:
        return word
    d = ''.join(c for c in str(number or '') if c.isdigit())
    return '1' + d if len(d) == 10 else d


def _hms(seconds) -> str:
    s = max(0, int(seconds or 0))
    return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'


def fail_reason_label(reason: str) -> str:
    """The comment as the portal shows it (lib/fail-reason.ts)."""
    raw = (reason or '').strip()
    if not raw:
        return ''
    low = raw.lower()
    if 'duplicate' in low:
        return 'Duplicate Call'
    from routing.comments import CALLER_ID_BLOCKED, is_caller_block
    if is_caller_block(raw):          # CH-349: every blocked caller ID
        return CALLER_ID_BLOCKED
    if 'cap reached' in low or 'overflow' in low:
        return 'Overflow Missed'
    if 'destination missed' in low or 'no destination' in low:
        return 'No Destination'
    if low in ('stale_timeout', 'asterisk_gone') or 'not connected' in low:
        return 'Not Connected'
    if 'insufficient' in low:
        return 'Insufficient Funds'
    return raw[:1].upper() + raw[1:]


def report_window(send_time, now=None):
    """(day, start, end) the report covers when sent at `now`: the day so far,
    or the whole previous day for a send time before 06:00 (Eastern)."""
    now = (now or timezone.now()).astimezone(EASTERN)
    if send_time < MORNING_CUTOFF:
        day = now.date() - timedelta(days=1)
        start = datetime.combine(day, time.min, tzinfo=EASTERN)
        return day, start, start + timedelta(days=1)
    day = now.date()
    return day, datetime.combine(day, time.min, tzinfo=EASTERN), now


def _status(r) -> str:
    if r.is_converted and r.status not in ANSWERED:
        return 'Missed With Conversion'
    return STATUS_LABEL.get(r.status, (r.status or '').replace('_', ' ').title())


def build_reports(report, start, end):
    """[(destination, header, rows, stats)] for each destination of the buyer
    that took calls between start and end."""
    buyer = report.buyer
    picked = set(report.included_columns or DEFAULT_COLUMNS)
    cols = [(k, report.column_names.get(k) or label) for k, label in COLUMNS
            if k in picked
            and not (k == 'recording' and report.no_recording_link)
            and not (k == 'fail_reason' and report.hide_fail_reason)]
    header = [label for _, label in cols]
    rewrites = report.comment_rewrites or {}
    rewrites_ci = {k.lower(): v for k, v in rewrites.items()}

    base = (
        CallRecord.objects
        .filter(organization_id=buyer.organization_id)
        .annotate(call_time=Coalesce('started_at', 'created_at'))
        .filter(call_time__gte=start, call_time__lt=end)
        .filter(Q(buyer_id=buyer.id) | Q(buyer_id__isnull=True))
        .order_by('call_time')
    )

    out = []
    for dest in buyer.destinations.all().order_by('name'):
        d10 = _digits10(dest.tfn)
        if not d10:
            continue
        calls = list(base.filter(destination_number__endswith=d10))
        if report.connected_only:
            calls = [c for c in calls if c.status in ANSWERED]
        if not calls:
            continue
        rows = []
        for c in calls:
            reason = '' if (c.is_converted or c.status in ANSWERED) else fail_reason_label(c.block_reason)
            reason = rewrites_ci.get(reason.lower(), reason) if reason else reason
            if c.ring_seconds is not None:
                ttc = _hms(c.ring_seconds)
            elif c.answered_at:
                ttc = _hms((c.answered_at - c.call_time).total_seconds())
            else:
                ttc = ''
            values = {
                'date': c.call_time.astimezone(EASTERN).strftime('%Y-%m-%d %H:%M:%S'),
                'call_id': str(c.id),
                'caller': _caller(c.caller_number),
                'state': c.caller_state or '',
                'carrier': c.carrier or c.carrier_name or '',
                'called_number': _caller(c.called_number),
                'campaign': c.campaign_name or '',
                'destination_name': dest.name or '',
                'destination': _caller(dest.tfn),
                'publisher': c.publisher_name or '',
                'status': _status(c),
                'duration': _hms(c.duration_seconds) if c.status in ANSWERED else '00:00:00',
                'ttc': ttc,
                'fail_reason': reason,
                'qualified': 'Yes' if c.is_qualified else 'No',
                'converted': 'Yes' if c.is_converted else 'No',
                'repeated': 'Yes' if c.is_duplicate else 'No',
                'revenue': float(c.dynamic_revenue or 0),
                'recording': public_recording_url(c.recording_url) or '',
            }
            rows.append([values[k] for k, _ in cols])
        stats = {
            'calls': len(calls),
            'connected': sum(1 for c in calls if c.status in ANSWERED),
        }
        out.append((dest, header, rows, stats))
    return out


def _filename(dest, day) -> str:
    name = re.sub(r'[^A-Za-z0-9 _.|-]+', ' ', dest.name or dest.tfn).strip() or _digits10(dest.tfn)
    name = name.replace('|', '-')
    return f'{name} {day:%Y-%m-%d}.xlsx'


def _telegram(method: str, data: dict, files=None):
    import requests

    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        raise ReportError('The Telegram bot is not set up on the server (TELEGRAM_BOT_TOKEN).')
    r = requests.post(f'https://api.telegram.org/bot{token}/{method}', data=data, files=files, timeout=30)
    if r.status_code != 200:
        try:
            why = r.json().get('description') or r.text
        except Exception:
            why = r.text
        if 'chat not found' in why.lower():
            why = 'Telegram cannot find this chat. The buyer must press Start on our bot, or add it to their group.'
        elif 'blocked' in why.lower():
            why = 'The buyer has blocked our bot on Telegram.'
        raise ReportError(why[:200])
    return r


def send_report(report, now=None, test_chat=None) -> str:
    """Build and send the report now. Returns what was sent; raises
    ReportError when it could not be sent.

    `test_chat` (CH-310): send it to that chat instead - a staff member's own
    Telegram, to see the report exactly as the buyer would. Nothing goes to
    the buyer, so the buyer's approval is not needed for it."""
    if test_chat is None and not report.chat_id.strip():
        raise ReportError('No Telegram chat ID is set for this buyer.')
    if test_chat is None and not report.buyer_approved:
        raise ReportError('The buyer has not approved Telegram reports yet. They turn it on in their '
                          'own Settings > Scheduled Reports.')
    now = now or timezone.now()
    day, start, end = report_window(report.send_time, now)
    files = build_reports(report, start, end)

    lines = [f'AVORTYX | Daily Report', f'{report.buyer.name} · {day:%a %d %b %Y} (EST)', '']
    if test_chat is not None:
        lines.insert(0, 'TEST - sent to you only, not to the buyer')
    if files:
        for dest, _, _, st in files:
            lines.append(f'{dest.name}: {st["calls"]} calls, {st["connected"]} connected')
        lines.append('')
        lines.append(f'Total: {sum(f[3]["calls"] for f in files)} calls, '
                     f'{sum(f[3]["connected"] for f in files)} connected')
    else:
        lines.append('No calls on your destinations for this period.')
    chat = str(test_chat).strip() if test_chat is not None else report.chat_id.strip()
    _telegram('sendMessage', {'chat_id': chat, 'text': '\n'.join(lines), 'disable_web_page_preview': 'true'})

    for dest, header, rows, st in files:
        body = write_xlsx(header, rows, sheet=dest.name or dest.tfn)
        _telegram('sendDocument', {'chat_id': chat, 'caption': f'{dest.name} · {st["calls"]} calls'},
                  files={'document': (_filename(dest, day), body,
                                      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    return f'Sent {len(files)} file(s) for {day:%d %b}'


def bot_username() -> str:
    """Our bot's @name, for the buyer's screen ("press Start on @AVSPTBOT").
    Asked from Telegram once a day; empty when it cannot be read."""
    from django.core.cache import cache
    name = cache.get('telegram_bot_username')
    if name is None:
        name = ''
        try:
            r = _telegram('getMe', {})
            name = '@' + r.json()['result']['username']
        except Exception:
            logger.warning('could not read the bot name from Telegram')
        cache.set('telegram_bot_username', name, 86400 if name else 600)
    return name


def send_due_reports(now=None) -> dict:
    """Send every buyer report whose time has come today and has not gone yet."""
    from buyers.models import BuyerTelegramReport

    now = now or timezone.now()
    local = now.astimezone(EASTERN)
    today = local.date()
    done = {'sent': 0, 'failed': 0}
    due = (BuyerTelegramReport.objects.select_related('buyer')
           .filter(enabled=True, buyer_approved=True, send_time__lte=local.time().replace(tzinfo=None))
           .exclude(chat_id='')
           .exclude(last_sent_on=today))
    for report in due:
        if report.attempts_on == today and report.attempts >= MAX_ATTEMPTS:
            continue
        try:
            result = send_report(report, now)
            report.last_sent_on, report.last_sent_at, report.last_result = today, now, result
            report.attempts_on, report.attempts = today, 0
            done['sent'] += 1
        except Exception as e:
            if report.attempts_on != today:
                report.attempts_on, report.attempts = today, 0
            report.attempts += 1
            report.last_result = f'Not sent: {e}'[:255]
            done['failed'] += 1
            logger.warning('buyer report not sent: buyer=%s %s', report.buyer_id, e)
        report.save(update_fields=['last_sent_on', 'last_sent_at', 'last_result',
                                   'attempts_on', 'attempts', 'updated_at'])
    return done

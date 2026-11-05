"""CH-604: the "Non Priority Notifications" topic of the support group.

Sir, 5 Nov: "Shift these details [portal changes: routing, priority, caps,
users] in non priority group as well. Under Notification we just need
important server details." "When Vercel was gone we lost everything - we
needed report and Excel ... add feature of export raw xlsx file every 15 min
and send in non priority group, so in case anything happens to the portal we
have our Excel data." "And set non priority auto delete within 48 hours."

- post(text): a message to the Non Priority topic (portal changes, the EOD
  report; SSH logins come from scripts/host_watch.py). Noted as kind
  "nonpriority", so support/telegram_autodelete.py deletes it after
  TELEGRAM_NONPRIORITY_DELETE_HOURS (47). Without TELEGRAM_NONPRIORITY_THREAD_ID
  it goes where it always went (the status chat / Notifications topic).
- send_call_backup(): every 15 minutes (celery beat), today's calls so far as
  an Excel file - the Call Log (every call, the portal's export columns) and
  the team report's Summary / By Campaign / By Publisher / By Buyer sheets -
  built on the server from the database, so it never needs the portal. After
  midnight Eastern, once, the whole day that ended. Main workspace only.
  Sent only when the Non Priority topic is set (never into Notifications).

.env (read on every use, no restart):
  TELEGRAM_NONPRIORITY_THREAD_ID     the topic (CH-577)
  TELEGRAM_NONPRIORITY_DELETE_HOURS  47 (0 = the normal TELEGRAM_AUTODELETE_DAYS)
  NONPRIORITY_CALL_BACKUP            on (off = no Excel files)
  EOD_TO_NONPRIORITY                 on (off = the EOD report stays in Notifications)
"""
import csv
import io
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from support.telegram_autodelete import _setting, remember

logger = logging.getLogger(__name__)

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NUMERIC = {'Duration (s)', 'TTC (s)', 'Revenue', 'Payout', 'Profit', 'Billed Minutes', 'Cost'}
OFF = ('0', 'off', 'false', 'no')


def _on(name, default='on') -> bool:
    return str(_setting(name, default)).strip().lower() not in OFF


def thread() -> str:
    return str(_setting('TELEGRAM_NONPRIORITY_THREAD_ID', '') or '').strip()


def target():
    """(chat, thread, is Non Priority). The topic exists only in the support
    group, so only when the status chat is that group."""
    from support.system_status import status_chat
    chat, notify = status_chat()
    support = str(getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '') or '')
    t = thread()
    if t and support and chat == support:
        return chat, t, True
    return chat, notify, False


def post(text) -> bool:
    """A message for the Non Priority topic (else where status messages go)."""
    from support.system_status import post as status_post
    chat, t, np = target()
    return status_post(text, chat=chat, thread=t, kind='nonpriority' if np else 'status')


def eod_thread(chat):
    """CH-604: the EOD report's topic and kind - Non Priority unless
    EOD_TO_NONPRIORITY is off; None when it should go as before."""
    c, t, np = target()
    if np and str(chat) == c and _on('EOD_TO_NONPRIORITY'):
        return t, 'nonpriority'
    return None


# ── the Excel backup ──────────────────────────────────────────────────────────

def _owner(org):
    """A login that sees every column of the export (no partner mask)."""
    from accounts.models import User
    qs = User.objects.filter(organization=org, is_active=True).exclude(role__in=['buyer', 'publisher'])
    return qs.filter(is_superuser=True).first() or qs.filter(role='admin').first()


def _number(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() and '.' not in str(v) else f


def call_log(org, day):
    """(header, rows) - the portal's Call Log export for one Eastern day."""
    from analytics.schemas import AnalyticsFilterSchema
    from analytics.services import AnalyticsService
    user = _owner(org)
    if user is None:
        return [], []
    filters = AnalyticsFilterSchema(date_from=day.isoformat(), date_to=day.isoformat(),
                                    timezone='America/New_York')
    header, rows = [], []
    for line in AnalyticsService.export_csv(user, filters):
        cells = next(csv.reader(io.StringIO(line)), [])
        if not header:
            header = cells
            continue
        rows.append([_number(v) if header[i] in NUMERIC and v != '' else v for i, v in enumerate(cells)])
    return header, rows


def workbook(org, day, start, end, title):
    """(xlsx bytes, calls in the file)."""
    from analytics import team_report
    from buyers.xlsx import write_xlsx_sheets
    header, rows = call_log(org, day)
    data = team_report.build(org, start, end)
    return write_xlsx_sheets([('Call Log', header, rows)] + team_report.sheets(data, title)), len(rows)


def _send_file(chat, t, name, content, caption) -> bool:
    import requests
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return False
    data = {'chat_id': chat, 'caption': caption, 'parse_mode': 'HTML'}
    if t:
        data['message_thread_id'] = t
    r = requests.post(f'https://api.telegram.org/bot{token}/sendDocument', data=data,
                      files={'document': (name, content, XLSX)}, timeout=60)
    if r.ok:
        remember(chat, r.content, 'nonpriority')
        return True
    logger.warning('call backup not sent: %s %s', r.status_code, r.text[:200])
    return False


def send_call_backup(now=None) -> str:
    from accounts.models import Organization
    from accounts.tenancy import main_organization_id
    from analytics.team_report import EASTERN
    if not _on('NONPRIORITY_CALL_BACKUP'):
        return 'off (NONPRIORITY_CALL_BACKUP)'
    chat, t, np = target()
    if not np:
        return 'not sent: no Non Priority topic (TELEGRAM_NONPRIORITY_THREAD_ID)'
    org = Organization.objects.filter(id=main_organization_id()).first()
    if org is None:
        return 'not sent: no main workspace (MAIN_ORGANIZATION_SLUG)'
    now = now or timezone.now()
    east = now.astimezone(EASTERN)
    done = []
    # Once after midnight: the whole day that ended (its last 15 minutes too).
    yday = east.date() - timedelta(days=1)
    if east.hour == 0 and cache.add(f'np_call_backup_final:{org.id}:{yday}', 1, 3 * 86400):
        start = datetime.combine(yday, time.min, tzinfo=EASTERN)
        content, n = workbook(org, yday, start, start + timedelta(days=1), f'{yday:%a %d %b %Y} (EST)')
        if _send_file(chat, t, f'Avortyx Calls {yday:%Y-%m-%d} FINAL.xlsx', content,
                      f'📦 <b>Final call data</b> · {yday:%a %d %b %Y} (whole day, EST) · {n:,} calls'):
            done.append(f'final {yday}: {n}')
    day = east.date()
    start = datetime.combine(day, time.min, tzinfo=EASTERN)
    content, n = workbook(org, day, start, now, f'{day:%a %d %b %Y} until {east:%-I:%M %p} EST')
    if n and _send_file(chat, t, f'Avortyx Calls {day:%Y-%m-%d} {east:%H%M} EST.xlsx', content,
                        f'📦 <b>Call data backup</b> · {day:%a %d %b %Y} until {east:%-I:%M %p} EST · {n:,} calls'):
        done.append(f'today: {n}')
    return 'sent ' + ', '.join(done) if done else 'nothing to send'

"""Scheduled Reports: the emailed digest, really sent (CH-266, Sir 25 Oct).

Settings > Scheduled Reports saved a schedule and nothing ever sent it: there
was no sender, and "Send a test now" said sent. This is the sender.

Each digest belongs to the login that made it and carries only what that
login may see - a publisher's own calls, a buyer's own calls, without the
columns its role or partner settings hide (the same rules as the call export,
`AnalyticsService.export_dropped_columns`). It covers the day so far, in the
digest's time zone, at the time and on the days chosen, as an email with a
short summary and the calls attached as Excel, CSV, or both, with the
columns the user ticked.
"""
import csv
import io
import logging
from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.utils import timezone

logger = logging.getLogger(__name__)

WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
MAX_ATTEMPTS_A_DAY = 3

# Export column -> the heading in the digest file and on the settings screen.
LABELS = {'Caller': 'Caller ID', 'Duration (s)': 'Duration', 'TTC (s)': 'TTC'}
# Our own figures: offered to staff only (a partner's role masks them anyway).
INTERNAL = ('Payout', 'Profit', 'Billed Minutes', 'Cost')

STATUS = {
    'completed': 'Completed', 'in_progress': 'Live', 'no_answer': 'No Answer',
    'busy': 'Busy', 'failed': 'Failed', 'voicemail': 'Voicemail',
}


class DigestError(Exception):
    pass


def available_columns(user) -> list:
    """[(key, label)] - every export column this login may have."""
    from analytics.services import AnalyticsService, EXPORT_HEADER
    drop = AnalyticsService.export_dropped_columns(user)
    if getattr(user, 'role', '') in ('buyer', 'publisher'):
        drop = set(drop) | set(INTERNAL)
    return [(c, LABELS.get(c, c)) for c in EXPORT_HEADER if c not in drop]


def _zone(report) -> ZoneInfo:
    from accounts.partner_timezone import portal_timezone
    name = (report.filters or {}).get('timezone') or portal_timezone()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(portal_timezone())


def _hms(value) -> str:
    try:
        s = max(0, int(float(value)))
    except (TypeError, ValueError):
        return value or ''
    return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'


def _caller(value: str) -> str:
    d = ''.join(c for c in str(value or '') if c.isdigit())
    return '1' + d if len(d) == 10 else d


def _pretty(col: str, value: str) -> str:
    if col == 'Status':
        return STATUS.get(value, value)
    if col in ('Duration (s)', 'TTC (s)'):
        return _hms(value) if value != '' else ''
    if col in ('Caller', 'Called Number', 'Destination Number'):
        return _caller(value)
    return value


def build(report, now=None):
    """(day, header, rows, summary) for the digest as it would go out now."""
    from analytics.services import AnalyticsService

    user = report.created_by
    tz = _zone(report)
    now = (now or timezone.now()).astimezone(tz)
    day = now.date()
    flt = SimpleNamespace(
        date_from=day.isoformat(), date_to=day.isoformat(), timezone=str(tz.key),
        campaign_id=None, buyer_id=None, publisher_id=None, status=None, destination=None,
        is_qualified=None, is_converted=None, is_duplicate=None, is_spam=None, search=None,
    )
    raw = ''.join(AnalyticsService.export_csv(user, flt))
    table = list(csv.reader(io.StringIO(raw)))
    head, body = (table[0], table[1:]) if table else ([], [])

    allowed = [c for c, _ in available_columns(user)]
    chosen = (report.filters or {}).get('columns')
    cols = [c for c in (chosen if isinstance(chosen, list) and chosen else allowed) if c in allowed and c in head]
    idx = [head.index(c) for c in cols]
    rows = [[_pretty(c, r[i] if i < len(r) else '') for c, i in zip(cols, idx)] for r in body]

    def count(col, test):
        if col not in head:
            return None
        i = head.index(col)
        return sum(1 for r in body if i < len(r) and test(r[i]))

    summary = {
        'calls': len(body),
        'connected': count('Status', lambda v: v in ('completed', 'in_progress')),
        'converted': count('Converted', lambda v: v == 'Yes'),
    }
    return day, [LABELS.get(c, c) for c in cols], rows, summary


def _files(report, day, header, rows):
    from buyers.xlsx import write_xlsx
    choice = (report.filters or {}).get('formatChoice') or report.format or 'xlsx'
    stem = f'Avortyx report {day:%Y-%m-%d}'
    out = []
    if choice in ('csv', 'both'):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        out.append((f'{stem}.csv', buf.getvalue().encode('utf-8-sig'), 'text/csv'))
    if choice in ('xlsx', 'both', 'pdf'):   # PDF is not made yet: old PDF choices get Excel
        out.append((f'{stem}.xlsx', write_xlsx(header, rows, sheet='Calls'),
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
    return out


def send(report, now=None) -> str:
    """Build and email the digest now. Returns what was sent; raises
    DigestError when it cannot be sent."""
    recipients = [r for r in (report.recipients or []) if r]
    if not recipients:
        raise DigestError('Add an email address to send the report to.')
    if not getattr(report.created_by, 'is_active', False):
        raise DigestError('The login that owns this report is no longer active.')
    day, header, rows, summary = build(report, now)
    if not header:
        raise DigestError('Tick at least one column to include.')

    lines = [f'Avortyx daily report · {day:%a %d %b %Y}', '']
    lines.append(f'Calls: {summary["calls"]}')
    if summary['connected'] is not None:
        lines.append(f'Connected: {summary["connected"]}')
        lines.append(f'Not connected: {summary["calls"] - summary["connected"]}')
    if summary['converted'] is not None:
        lines.append(f'Converted: {summary["converted"]}')
    lines += ['', 'The call details are attached.' if rows else 'No calls so far today.']

    msg = EmailMessage(
        subject=f'Avortyx daily report · {day:%a %d %b %Y}',
        body='\n'.join(lines),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=recipients,
    )
    files = _files(report, day, header, rows) if rows else []
    for name, content, mime in files:
        msg.attach(name, content, mime)
    try:
        msg.send(fail_silently=False)
    except Exception as e:
        logger.warning('digest not sent: report=%s %s', report.id, e)
        raise DigestError(f'The email could not be sent: {e}'[:200])
    return f'Sent to {", ".join(recipients)} with {len(rows)} calls'


def is_due(report, now=None) -> bool:
    f = report.filters or {}
    tz = _zone(report)
    local = (now or timezone.now()).astimezone(tz)
    days = [d for d in (f.get('days') or list(WEEKDAYS)) if d in WEEKDAYS]
    if WEEKDAYS[local.weekday()] not in days:
        return False
    try:
        at = time(int(f.get('hour', 18)), int(f.get('minute', 0)))
    except (TypeError, ValueError):
        at = time(18, 0)
    if local.time() < at:
        return False
    if report.last_sent_at and report.last_sent_at.astimezone(tz).date() == local.date():
        return False
    return True


def send_due(now=None) -> dict:
    """Send every active digest whose time has come today and has not gone."""
    from analytics.scheduled_reports import ScheduledReport

    now = now or timezone.now()
    done = {'sent': 0, 'failed': 0}
    for report in ScheduledReport.objects.select_related('created_by').filter(status='active'):
        if not is_due(report, now):
            continue
        key = f'digest_attempts_{report.id}_{now.astimezone(_zone(report)).date()}'
        tries = cache.get(key, 0)
        if tries >= MAX_ATTEMPTS_A_DAY:
            continue
        try:
            send(report, now)
            report.last_sent_at = now
            report.save(update_fields=['last_sent_at', 'updated_at'])
            done['sent'] += 1
        except Exception:
            cache.set(key, tries + 1, 86400)
            done['failed'] += 1
    return done

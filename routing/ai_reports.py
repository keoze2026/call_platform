"""CH-546: AI audit reports on Telegram - Part 3 of Sir's upgrade (4 Nov).

"Telegram: instant alert per no-voice call with a listen link (keep current),
an alert when a publisher's no-voice rate exceeds 15% in the last hour, and a
daily summary per label and publisher."

Sent by our reports bot (CH-347) to the team chat(s) connected on the
Automated Report page - never a buyer's or publisher's chat - with the same
switch as the per-call alert (VOICE_TELEGRAM_ALERTS).

  rate alert     every 10 minutes (tasks.ai_publisher_rate_check): per
                 publisher, the calls the AI checked in the last 60 minutes;
                 over AI_RATE_ALERT_PERCENT (15) No Voice, with at least
                 AI_RATE_ALERT_MIN_CALLS (10) checked calls, is sent - once per
                 publisher per clock hour.
  daily summary  yesterday (EST), once, from AI_DAILY_SUMMARY_HOUR (9 AM EST):
                 each label's count, then each publisher's checked calls, No
                 Voice and No Voice % (tasks.ai_daily_summary, every 15 min).
"""
import html
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from routing import ai_labels

logger = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')
CHECKED = ai_labels.LABELS + (ai_labels.MIXED,)


def _on() -> bool:
    return bool(getattr(settings, 'VOICE_TELEGRAM_ALERTS', False))


def _send(org_id, text) -> int:
    from analytics import team_report
    from analytics.models import TeamReportChat
    if not team_report.configured():
        return 0
    sent = 0
    for chat in TeamReportChat.objects.filter(organization_id=org_id, enabled=True):
        try:
            team_report.telegram('sendMessage', {'chat_id': chat.chat_id, 'text': text, 'parse_mode': 'HTML',
                                                 'disable_web_page_preview': 'true'})
            sent += 1
        except Exception:
            logger.exception('AI reports: could not send to %s', chat.chat_id)
    return sent


def _by_publisher(qs):
    """[(org_id, publisher name, checked, no voice, {label: n})] from CallLog rows."""
    rows = (qs.values('organization_id', 'publisher__name')
            .annotate(checked=Count('id'),
                      no_voice=Count('id', filter=Q(ai_label__in=ai_labels.NO_VOICE)),
                      dead=Count('id', filter=Q(ai_label=ai_labels.DEAD)),
                      only=Count('id', filter=Q(ai_label=ai_labels.AGENT_ONLY)),
                      beep=Count('id', filter=Q(ai_label=ai_labels.AGENT_BEEP))))
    return [(r['organization_id'], r['publisher__name'] or '(no publisher)', r['checked'], r['no_voice'],
             {ai_labels.DEAD: r['dead'], ai_labels.AGENT_ONLY: r['only'], ai_labels.AGENT_BEEP: r['beep']})
            for r in rows]


def _checked(start, end):
    from routing.models import CallLog
    return (CallLog.objects.filter(created_at__gte=start, created_at__lt=end, retry_hidden=False,
                                   ai_label__in=CHECKED)
            .exclude(twilio_call_sid__startswith='tfntest-'))


def _labels_text(per) -> str:
    return ', '.join(f'{ai_labels.NAMES[k].split("| ")[-1]} {v}' for k, v in per.items() if v)


def rate_alerts(now=None) -> int:
    """Publishers over the No Voice rate in the last hour. Returns messages sent."""
    if not _on():
        return 0
    now = now or timezone.now()
    limit = float(getattr(settings, 'AI_RATE_ALERT_PERCENT', 15) or 15)
    min_calls = int(getattr(settings, 'AI_RATE_ALERT_MIN_CALLS', 10) or 10)
    hour = now.astimezone(ET).strftime('%Y%m%d%H')
    over = defaultdict(list)
    for org_id, name, checked, no_voice, per in _by_publisher(_checked(now - timedelta(hours=1), now)):
        if checked < min_calls or 100.0 * no_voice / checked <= limit:
            continue
        if not cache.add(f'ai_rate_alert:{org_id}:{name}:{hour}', 1, 2 * 3600):
            continue                                   # already sent this hour
        over[org_id].append((name, checked, no_voice, per))
    sent = 0
    e = html.escape
    for org_id, rows in over.items():
        lines = [f'<b>No Voice rate over {limit:g}% - last hour</b> (AI audit)']
        for name, checked, no_voice, per in sorted(rows, key=lambda r: -r[2] / r[1]):
            lines.append(f'<b>{e(name)}</b>: {no_voice} of {checked} checked calls '
                         f'({100.0 * no_voice / checked:.0f}%) - {e(_labels_text(per))}')
        lines.append('Calls: AI Audit page, filter All No Voice.')
        sent += _send(org_id, '\n'.join(lines))
    return sent


def daily_summary(now=None, force=False) -> int:
    """Yesterday's AI audit per label and per publisher, once a day."""
    if not _on():
        return 0
    now = (now or timezone.now()).astimezone(ET)
    if not force and now.hour < int(getattr(settings, 'AI_DAILY_SUMMARY_HOUR', 9) or 9):
        return 0
    day = now.date() - timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=ET)
    qs = _checked(start, start + timedelta(days=1))
    sent = 0
    e = html.escape
    for org_id in qs.values_list('organization_id', flat=True).distinct():
        if not force and not cache.add(f'ai_daily_summary:{org_id}:{day}', 1, 3 * 86400):
            continue
        mine = qs.filter(organization_id=org_id)
        labels = dict(mine.values_list('ai_label').annotate(n=Count('id')).values_list('ai_label', 'n'))
        total = sum(labels.values())
        no_voice = sum(labels.get(k, 0) for k in ai_labels.NO_VOICE)
        lines = [f'<b>AI audit - {day:%a %d %b %Y} (EST)</b>',
                 f'{total} calls checked · No Voice {no_voice} ({100.0 * no_voice / total:.1f}%)' if total
                 else 'No calls checked.']
        for k in (ai_labels.VALID, ai_labels.DEAD, ai_labels.AGENT_ONLY, ai_labels.AGENT_BEEP, ai_labels.MIXED):
            if labels.get(k):
                lines.append(f'{e(ai_labels.NAMES[k])}: {labels[k]}')
        pubs = sorted(_by_publisher(mine), key=lambda r: (-r[3], r[1]))
        if pubs:
            lines.append('')
            lines.append('<b>By publisher</b> (No Voice / checked)')
            for _, name, checked, nv, per in pubs[:20]:
                lines.append(f'{e(name)}: {nv} / {checked} ({100.0 * nv / checked:.0f}%)'
                             + (f' - {e(_labels_text(per))}' if nv else ''))
            if len(pubs) > 20:
                lines.append(f'+{len(pubs) - 20} more publishers: AI Audit page')
        sent += _send(org_id, '\n'.join(lines))
    return sent

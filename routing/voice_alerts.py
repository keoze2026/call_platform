"""CH-536 / CH-543: an instant Telegram report of every call the voice check found
with no voice - for our team only.

Sir, 4 Nov: "AI ... which can listen to call recordings in the background and
automatically filter No voice calls ... and it can send us instant reports."

voice_results calls this with the calls it has just labelled "no_voice" (a
minute or two after each call ends). One message per workspace per run, from
our own reports bot (CH-347) to the team chat(s) connected on the Automated
Report page - never a buyer's or publisher's chat:

    No voice - 2 calls (AI voice check)
    08:51:25 AM EST | C-13 | ADS-1 > A49 +18882156557 | talk 9s, speech 0.0s | Listen
    ...

Switch: VOICE_TELEGRAM_ALERTS (default off). A send that fails is logged and
never stops the results being saved.
"""
import html
import logging
from collections import defaultdict
from zoneinfo import ZoneInfo

from django.conf import settings

logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')
MAX_LINES = 25


def _line(c) -> str:
    from routing.recordings import public_recording_url
    e = html.escape
    publisher = c.publisher.name if c.publisher_id else '-'
    buyer = c.buyer.name if c.buyer_id else '-'
    from routing.ai_labels import FAULT_NOTE, NAMES
    if c.voice_channels == 2:
        heard = (f'caller {float(c.voice_caller_seconds or 0):.1f}s, agent {float(c.voice_agent_seconds or 0):.1f}s'
                 + (f', beep {float(c.voice_beep_seconds or 0):.1f}s' if (c.voice_beep_seconds or 0) > 0 else ''))
    else:
        heard = f'speech {float(c.voice_seconds or 0):.1f}s'
    label = NAMES.get(c.ai_label, 'No voice')
    if c.voice_audio_fault:
        label += f' ({FAULT_NOTE})'
    parts = [
        c.created_at.astimezone(ET).strftime('%I:%M:%S %p EST'),
        f'<b>{e(label)}</b>',
        e(c.campaign.name if c.campaign_id else '-'),
        f'{e(publisher)} &gt; {e(buyer)} {e(c.destination_number or "")}'.strip(),
        f'talk {int(c.duration or 0)}s, {heard}',
    ]
    url = public_recording_url(c.recording_url or '')
    if url:
        parts.append(f'<a href="{html.escape(url, quote=True)}">Listen</a>')
    line = ' | '.join(parts)
    # CH-594: the LLM's one-line summary when it gave the final label (what
    # happened on the call, in words); otherwise the rule step's comment.
    summary = getattr(c, 'ai_summary', '') or ''
    if summary:
        # CH-600: with how sure it is - under 60% the team should listen first.
        sure = f' ({int(c.ai_confidence)}% sure)' if c.ai_confidence is not None else ''
        line += f'\nAI{sure}: <i>{e(summary)}</i>'
    # CH-546: the AI's comment on the call, under it.
    elif c.ai_comment:
        line += f'\nComment: <i>{e(c.ai_comment)}</i>'
    return line


def message(calls) -> str:
    n = len(calls)
    head = f"<b>No voice - {n} call{'s' if n != 1 else ''}</b> (AI voice check)"
    lines = [_line(c) for c in calls[:MAX_LINES]]
    if n > MAX_LINES:
        lines.append(f'+{n - MAX_LINES} more: AI Audit page')
    return '\n'.join([head, *lines])


def send_no_voice(call_ids) -> int:
    """Send the no-voice calls to each workspace's team chat(s). Returns the
    number of messages sent."""
    if not call_ids or not getattr(settings, 'VOICE_TELEGRAM_ALERTS', False):
        return 0
    from analytics import team_report
    from analytics.models import TeamReportChat
    from routing.models import CallLog

    if not team_report.configured():
        logger.warning('voice alerts: the reports bot is not set up (REPORTS_BOT_TOKEN); %d not sent', len(call_ids))
        return 0
    from routing.ai_labels import NO_VOICE
    # CH-543: Sir's three No Voice labels (the agent may have spoken).
    calls = (CallLog.objects.filter(id__in=list(call_ids), ai_label__in=NO_VOICE)
             .exclude(twilio_call_sid__startswith='tfntest-')
             .select_related('campaign', 'publisher', 'buyer').order_by('created_at'))
    by_org = defaultdict(list)
    for c in calls:
        by_org[c.organization_id].append(c)
    sent = 0
    for org_id, rows in by_org.items():
        chats = list(TeamReportChat.objects.filter(organization_id=org_id, enabled=True))
        if not chats:
            logger.warning('voice alerts: no team chat connected for workspace %s; %d not sent', org_id, len(rows))
            continue
        text = message(rows)
        for chat in chats:
            try:
                team_report.telegram('sendMessage', {'chat_id': chat.chat_id, 'text': text, 'parse_mode': 'HTML',
                                                     'disable_web_page_preview': 'true'})
                sent += 1
            except Exception:
                logger.exception('voice alerts: could not send to chat %s', chat.chat_id)
    return sent

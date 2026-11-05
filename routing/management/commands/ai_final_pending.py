"""CH-555: answered calls the AI audit has checked (Silero / PANNs) whose
final label - transcript + LLM - is not done yet.

Asked by scripts/ai_final/ai_final.py on Server B. One JSON object per line:
id, recording links (mixed and, where Server A records them apart, caller /
agent), talk seconds, and what the rule step measured. Read-only.

    docker compose exec -T web python manage.py ai_final_pending --limit 20
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from routing.models import CallLog
from routing.recordings import is_recording_link, public_recording_url


class Command(BaseCommand):
    help = 'List checked calls waiting for the transcript + LLM final label (JSON lines).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--days', type=int, default=2)
        parser.add_argument('--min-age-seconds', type=int, default=5)
        parser.add_argument('--retry-failed', action='store_true', help='calls whose last try failed')
        parser.add_argument('--id', action='append', default=[], help='these calls only (again)')
        # CH-594: after a change to the prompt or the voice check, give the
        # calls of the last --days their final label again: those finalised
        # before this moment (unix seconds - the start of the re-run, so each
        # call is done once and the re-run moves on).
        parser.add_argument('--redo-before', type=float, default=None)

    def handle(self, *args, **o):
        from datetime import datetime, timezone as dt_timezone

        from django.db.models import Q

        from routing import ai_labels

        now = timezone.now()
        qs = CallLog.objects.filter(status='completed', retry_hidden=False, ai_checked_at__isnull=False,
                                    voice_audio_fault=False,
                                    created_at__gte=now - timedelta(days=max(1, o['days'])))
        if o['id']:
            qs = qs.filter(id__in=o['id'])
        elif o['retry_failed']:
            qs = qs.filter(ai_final_by__startswith='error')
        elif o['redo_before'] is not None:
            before = datetime.fromtimestamp(o['redo_before'], tz=dt_timezone.utc)
            # only calls that HAVE a final label: new ones stay with the
            # every-minute run, so the two never take the same call.
            qs = qs.filter(ai_final_at__lt=before)
        else:
            qs = qs.filter(ai_final_at__isnull=True,
                           ai_checked_at__lte=now - timedelta(seconds=max(0, o['min_age_seconds'])))
        # CH-594: a real conversation (the rules say Valid and the caller spoke
        # for AI_FINAL_SKIP_CALLER_SECONDS or more) is not sent - the same rule
        # as ai_labels.needs_final. Named calls (--id) always go.
        skip = ai_labels.final_skip_seconds()
        if skip and not o['id']:
            sure = Q(voice_caller_seconds__gte=skip) & (
                Q(ai_rule_label=ai_labels.VALID) | (Q(ai_rule_label='') & Q(ai_label=ai_labels.VALID)))
            qs = qs.exclude(sure)
        qs = (qs.exclude(recording_url='').exclude(ai_label='')
              .exclude(twilio_call_sid__startswith='tfntest-').order_by('-ended_at')[:max(1, o['limit'])])
        for c in qs:
            if not is_recording_link(c.recording_url):
                continue
            public = public_recording_url(c.recording_url)
            item = {
                'id': str(c.id), 'url': public, 'file': c.recording_url.rstrip('/').rsplit('/', 1)[-1],
                'talk': int(c.duration or 0),
                'rule': {'label': c.ai_rule_label or c.ai_label, 'comment': c.ai_comment or '',
                         'channels': c.voice_channels, 'caller_speech': c.voice_caller_seconds,
                         'agent_speech': c.voice_agent_seconds, 'speech': c.voice_seconds,
                         'beep': c.voice_beep_seconds},
            }
            if '/recordings/' in public:
                base = public.split('/recordings/')[0] + '/recordings/split/'
                item['split'] = {'caller': f'{base}{c.id}-caller.wav', 'agent': f'{base}{c.id}-agent.wav'}
            self.stdout.write(json.dumps(item))

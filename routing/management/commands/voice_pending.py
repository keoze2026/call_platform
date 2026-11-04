"""CH-531: the answered calls whose recording has not been voice-checked yet.

Asked by scripts/voice_check/voice_check.py (on the host, where the recording
files are). Prints one JSON object per line: {"id", "url", "file"} - "file" is
the name the recording has on disk (<id>.mp3 or <id>.wav), "url" the link it
is served from, for when the file is no longer on this disk. CH-536: "talk" -
the seconds the call was answered for (completed calls), so the check listens
only to the conversation and not to the ringing recorded before the answer
(CH-535 records from the moment the buyer is dialled). "age" - minutes since
the call ended: a file not readable yet is tried again for 15 minutes before
it is called "missing".

    docker compose exec -T web python manage.py voice_pending --limit 200

Read-only.
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from routing.models import CallLog
from routing.recordings import is_recording_link, public_recording_url

FINISHED = ('completed', 'no_answer', 'busy', 'failed', 'canceled')


class Command(BaseCommand):
    help = 'List answered calls whose recording has not been voice-checked (JSON lines).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=200)
        parser.add_argument('--days', type=int, default=2, help='only calls from the last N days')
        parser.add_argument('--min-age', type=int, default=1, help='minutes since the call ended (CH-536: 1)')
        # CH-547: seconds instead, for the voice check's --loop mode.
        parser.add_argument('--min-age-seconds', type=int, default=None)
        parser.add_argument('--retry-missing', action='store_true', help='try "missing" ones again')

    def handle(self, *args, **o):
        now = timezone.now()
        labels = ['', 'missing'] if o['retry_missing'] else ['']
        qs = (CallLog.objects
              .filter(voice_label__in=labels, retry_hidden=False, status__in=FINISHED,
                      created_at__gte=now - timedelta(days=max(1, o['days'])),
                      ended_at__lte=now - (timedelta(seconds=max(0, o['min_age_seconds']))
                                           if o['min_age_seconds'] is not None
                                           else timedelta(minutes=max(1, o['min_age']))))
              .exclude(recording_url='')
              .order_by('-ended_at')
              .values_list('id', 'recording_url', 'status', 'duration', 'ended_at')[:max(1, o['limit'])])
        for call_id, url, status, duration, ended_at in qs:
            if not is_recording_link(url):
                continue
            name = url.rstrip('/').rsplit('/', 1)[-1]
            talk = int(duration or 0) if status == 'completed' else None
            age = int((now - ended_at).total_seconds() // 60) if ended_at else None
            public = public_recording_url(url)
            item = {'id': str(call_id), 'url': public, 'file': name, 'talk': talk, 'age': age}
            # CH-543: caller and agent apart, where Server A records them so
            # (deploy/split_channels.py) - same host, /recordings/split/.
            if '/recordings/' in public:
                base = public.split('/recordings/')[0] + '/recordings/split/'
                item['split'] = {'caller': f'{base}{call_id}-caller.wav', 'agent': f'{base}{call_id}-agent.wav'}
            self.stdout.write(json.dumps(item))

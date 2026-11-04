"""CH-531: the answered calls whose recording has not been voice-checked yet.

Asked by scripts/voice_check/voice_check.py (on the host, where the recording
files are). Prints one JSON object per line: {"id", "url", "file"} - "file" is
the name the recording has on disk (<id>.mp3 or <id>.wav), "url" the link it
is served from, for when the file is no longer on this disk.

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
        parser.add_argument('--min-age', type=int, default=10, help='minutes since the call ended')
        parser.add_argument('--retry-missing', action='store_true', help='try "missing" ones again')

    def handle(self, *args, **o):
        now = timezone.now()
        labels = ['', 'missing'] if o['retry_missing'] else ['']
        qs = (CallLog.objects
              .filter(voice_label__in=labels, retry_hidden=False, status__in=FINISHED,
                      created_at__gte=now - timedelta(days=max(1, o['days'])),
                      ended_at__lte=now - timedelta(minutes=max(1, o['min_age'])))
              .exclude(recording_url='')
              .order_by('-ended_at')
              .values_list('id', 'recording_url')[:max(1, o['limit'])])
        for call_id, url in qs:
            if not is_recording_link(url):
                continue
            name = url.rstrip('/').rsplit('/', 1)[-1]
            self.stdout.write(json.dumps({'id': str(call_id), 'url': public_recording_url(url), 'file': name}))

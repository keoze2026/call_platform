"""CH-546: give an AI label to calls the voice check heard before CH-543.

On 4 Nov the AI Audit page showed 3,396 calls "Pending AI check": they had
been listened to (voice_label set) before the labels existed, and the voice
check does not listen to a call twice. Their label is worked out from what was
already measured - one mixed recording: no speech = No Voice | Dead Caller,
speech = Voice Found. No Telegram alert for these (they are old).

    docker compose exec -T web python manage.py ai_backfill            # dry run: counts
    docker compose exec -T web python manage.py ai_backfill --apply
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.models import CallRecord
from routing import ai_labels
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Label calls checked before the AI labels existed (CH-543).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **o):
        qs = (CallLog.objects.filter(voice_label__in=['voice', 'no_voice'], ai_checked_at__isnull=True,
                                     created_at__gte=timezone.now() - timedelta(days=max(1, o['days'])))
              .only('id', 'voice_seconds', 'voice_beep_seconds'))
        counts, now = {}, timezone.now()
        for c in qs.iterator(chunk_size=500):
            ai = ai_labels.decide({'id': str(c.id), 'channels': 1, 'speech': c.voice_seconds or 0,
                                   'beep': c.voice_beep_seconds or 0})
            counts[ai['label']] = counts.get(ai['label'], 0) + 1
            if o['apply']:
                CallLog.objects.filter(pk=c.pk).update(
                    ai_label=ai['label'], ai_confidence=ai['confidence'], ai_comment=ai['comment'][:300],
                    ai_checked_at=now, voice_channels=1)
                CallRecord.objects.filter(pk=c.pk).update(ai_label=ai['label'])
        total = sum(counts.values())
        parts = ', '.join(f'{ai_labels.NAMES.get(k, k)} {v}' for k, v in sorted(counts.items()))
        self.stdout.write(f"{'labelled' if o['apply'] else 'would label'} {total}: {parts or 'nothing to do'}"
                          + ('' if o['apply'] else '  (dry run - add --apply)'))

"""CH-531: the answered calls with no voice on a day - the audit team's list.

    docker compose exec -T web python manage.py voice_report --date 2026-11-04
    docker compose exec -T web python manage.py voice_report --date 2026-11-04 --label voice

Eastern day. Read-only. One line per call: time (EST), caller, campaign,
publisher, buyer, line, talk time, seconds of speech heard, call id.
"""
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from routing.models import CallLog

ET = ZoneInfo('America/New_York')


class Command(BaseCommand):
    help = 'List the calls of a day by voice-check result (default: no voice).'

    def add_arguments(self, parser):
        parser.add_argument('--date', required=True, help='Eastern date, YYYY-MM-DD')
        parser.add_argument('--label', default='no_voice', choices=['no_voice', 'voice', 'missing', 'unchecked'])

    def handle(self, *args, **o):
        try:
            day = datetime.strptime(o['date'], '%Y-%m-%d').date()
        except ValueError:
            raise CommandError('--date must be YYYY-MM-DD')
        lo = datetime.combine(day, time.min, tzinfo=ET)
        day_qs = CallLog.objects.filter(created_at__gte=lo, created_at__lt=lo + timedelta(days=1),
                                        retry_hidden=False).exclude(recording_url='')
        totals = Counter(day_qs.values_list('voice_label', flat=True))
        self.stdout.write(f"{day} (Eastern), calls with a recording: {sum(totals.values())} | "
                          f"voice {totals.get('voice', 0)} | no voice {totals.get('no_voice', 0)} | "
                          f"missing {totals.get('missing', 0)} | not checked yet {totals.get('', 0)}")
        label = '' if o['label'] == 'unchecked' else o['label']
        rows = day_qs.filter(voice_label=label).select_related('campaign', 'publisher', 'buyer').order_by('created_at')
        for r in rows:
            self.stdout.write(
                f"  {r.created_at.astimezone(ET):%I:%M:%S %p}  {r.caller_number:14} "
                f"{r.campaign.name if r.campaign_id else '-':8} {r.publisher.name if r.publisher_id else '-':10} "
                f"{r.buyer.name if r.buyer_id else '-':8} {r.destination_number or '-':14} "
                f"talk {r.duration or 0:>5}s  speech {r.voice_seconds if r.voice_seconds is not None else '-':>5}  {r.id}")

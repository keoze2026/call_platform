"""Mark duplicate calls on existing records.

is_duplicate was never written by anything — it was only read, so the DUPE
column has always shown 0 even when the same caller appeared several times.

A call counts as a duplicate when the same caller number already reached the
same campaign inside that campaign's duplicate_call_block_hours (default 24).
The first call in a run is not a duplicate; every repeat after it is.

    python manage.py backfill_duplicates --dry-run
    python manage.py backfill_duplicates
"""
from datetime import timedelta

from django.core.management.base import BaseCommand

from analytics.models import CallRecord
from routing.models import CallLog


class Command(BaseCommand):
    help = "Recompute is_duplicate on CallLog and mirror it to CallRecord"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change, change nothing')

    def handle(self, *args, **options):
        dry = options['dry_run']

        logs = list(
            CallLog.objects.select_related('campaign')
            .order_by('campaign_id', 'caller_number', 'created_at')
        )
        self.stdout.write(f'CallLog rows: {len(logs)}')

        duplicates = []
        # Walk each (campaign, caller) run in time order. A call is a duplicate
        # when the previous call from that caller falls inside the window.
        previous_key = None
        previous_time = None

        for log in logs:
            key = (log.campaign_id, log.caller_number)
            hours = (
                getattr(log.campaign, 'duplicate_call_block_hours', 24) or 24
            ) if log.campaign_id else 24

            if key == previous_key and previous_time is not None:
                if log.created_at - previous_time <= timedelta(hours=hours):
                    duplicates.append(log.id)

            previous_key = key
            previous_time = log.created_at

        current = CallLog.objects.filter(is_duplicate=True).count()
        self.stdout.write(f'currently marked duplicate: {current}')
        self.stdout.write(f'should be duplicate       : {len(duplicates)}')
        self.stdout.write('-' * 55)

        by_campaign = {}
        for log in logs:
            if log.id in set(duplicates):
                name = log.campaign.name if log.campaign_id else '(no campaign)'
                by_campaign[name] = by_campaign.get(name, 0) + 1
        for name, n in sorted(by_campaign.items()):
            self.stdout.write(f'  {name}: {n}')

        if dry:
            self.stdout.write(self.style.WARNING('\nDry run — nothing written.'))
            return

        CallLog.objects.filter(is_duplicate=True).exclude(id__in=duplicates).update(is_duplicate=False)
        marked = CallLog.objects.filter(id__in=duplicates).update(is_duplicate=True)

        # CallRecord shares the CallLog id, which is how the sync signal keys it
        CallRecord.objects.filter(id__in=duplicates).update(is_duplicate=True)
        CallRecord.objects.exclude(id__in=duplicates).filter(is_duplicate=True).update(is_duplicate=False)

        self.stdout.write(self.style.SUCCESS(f'\nMarked {marked} CallLog rows as duplicate.'))
        self.stdout.write(
            f'CallRecord duplicates now: '
            f'{CallRecord.objects.filter(is_duplicate=True).count()}'
        )

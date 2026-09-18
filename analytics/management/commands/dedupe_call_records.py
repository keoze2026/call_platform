"""Remove duplicate CallRecord rows.

Two writers used to create CallRecord: the post_save signal keyed on the CallLog
id, and call_ended keyed on twilio_call_sid. Each terminal call therefore got two
rows, doubling every analytics total. The second writer is gone; this clears the
rows it left behind.

    python manage.py dedupe_call_records --dry-run
    python manage.py dedupe_call_records

Within each duplicate group the row whose id matches its CallLog is kept, since
that is the one the signal maintains. Failing that, the oldest row wins.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count

from analytics.models import CallRecord
from routing.models import CallLog


class Command(BaseCommand):
    help = "Remove duplicate CallRecord rows sharing a twilio_call_sid"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be deleted, change nothing')

    def handle(self, *args, **options):
        dry = options['dry_run']

        dupe_sids = (
            CallRecord.objects
            .exclude(twilio_call_sid='')
            .values('twilio_call_sid', 'organization_id')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
        )

        groups = [(d['twilio_call_sid'], d['organization_id']) for d in dupe_sids]
        if not groups:
            self.stdout.write(self.style.SUCCESS('No duplicate CallRecords found.'))
            return

        self.stdout.write(f'Duplicate groups: {len(groups)}')

        # Which CallLog ids exist, so the signal-maintained row can be preferred
        sids = [g[0] for g in groups]
        log_ids = dict(
            CallLog.objects.filter(twilio_call_sid__in=sids)
            .values_list('twilio_call_sid', 'id')
        )

        to_delete = []
        kept_by_reason = defaultdict(int)

        for sid, org_id in groups:
            rows = list(
                CallRecord.objects
                .filter(twilio_call_sid=sid, organization_id=org_id)
                .order_by('created_at')
            )
            preferred = next((r for r in rows if r.id == log_ids.get(sid)), None)
            if preferred is not None:
                kept_by_reason['matches CallLog id'] += 1
            else:
                preferred = rows[0]
                kept_by_reason['oldest row'] += 1

            to_delete.extend(r.id for r in rows if r.id != preferred.id)

        self.stdout.write(f'Rows to delete : {len(to_delete)}')
        for reason, n in kept_by_reason.items():
            self.stdout.write(f'  kept because it {reason}: {n}')

        if dry:
            self.stdout.write(self.style.WARNING('Dry run — nothing deleted.'))
            return

        deleted, _ = CallRecord.objects.filter(id__in=to_delete).delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} duplicate rows.'))
        self.stdout.write(f'CallRecord total now: {CallRecord.objects.count()}')

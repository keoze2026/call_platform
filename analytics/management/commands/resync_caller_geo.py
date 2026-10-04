"""Re-copy caller geo from the call log onto the reporting mirror.

Migration 0013 does this once at deploy. This is the same job on demand, for
when enrichment lands late, a provider is re-run, or the mirror is suspected
of being behind.

    python manage.py resync_caller_geo                # last 30 days
    python manage.py resync_caller_geo --days 90
    python manage.py resync_caller_geo --all
    python manage.py resync_caller_geo --dry-run
    python manage.py resync_caller_geo --force        # overwrite non-blanks
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.models import CallRecord
from routing.models import CallLog

FIELDS = ['caller_country', 'caller_city', 'caller_zip', 'caller_timezone']
BATCH = 500


class Command(BaseCommand):
    help = 'Copy caller country/city/zip/timezone from CallLog onto CallRecord.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30)
        parser.add_argument('--all', action='store_true', help='Every record, ignoring --days.')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--force', action='store_true',
            help='Overwrite values already on the mirror. Off by default so a '
                 'blank from the call log can never erase a good value.',
        )

    def handle(self, *args, **o):
        records = CallRecord.objects.all()
        if not o['all']:
            cutoff = timezone.now() - timedelta(days=o['days'])
            records = records.filter(created_at__gte=cutoff)
            scope = f"last {o['days']} days"
        else:
            scope = 'all time'

        total = records.count()
        self.stdout.write(f'Scope: {scope} — {total} call records')
        if not total:
            return

        source = {str(r['id']): r for r in CallLog.objects.values('id', *FIELDS)}
        self.stdout.write(f'Call log rows available: {len(source)}')

        batch, written, no_source, already = [], 0, 0, 0
        for rec in records.only('id', *FIELDS).iterator(chunk_size=BATCH):
            src = source.get(str(rec.id))
            if not src:
                no_source += 1
                continue
            changed = False
            for f in FIELDS:
                val = src.get(f) or ''
                if not val:
                    continue
                if getattr(rec, f, '') and not o['force']:
                    continue
                if getattr(rec, f, '') != val:
                    setattr(rec, f, val)
                    changed = True
            if changed:
                batch.append(rec)
            else:
                already += 1
            if len(batch) >= BATCH:
                if not o['dry_run']:
                    CallRecord.objects.bulk_update(batch, FIELDS)
                written += len(batch)
                batch = []
        if batch:
            if not o['dry_run']:
                CallRecord.objects.bulk_update(batch, FIELDS)
            written += len(batch)

        verb = 'would update' if o['dry_run'] else 'updated'
        self.stdout.write(self.style.SUCCESS(f'{verb} {written} records'))
        self.stdout.write(f'  already correct:        {already}')
        self.stdout.write(f'  no matching call log:   {no_source}')

        # Report the result against the thing being claimed, not a proxy.
        if not o['dry_run']:
            self.stdout.write('\nCoverage on the mirror now:')
            for f in FIELDS:
                filled = records.exclude(**{f: ''}).count()
                pct = (filled / total * 100) if total else 0
                self.stdout.write(f'  {f:<18} {filled:>6} / {total}  ({pct:.0f}%)')

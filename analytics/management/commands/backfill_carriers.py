"""Populate the normalised carrier field from the raw Telnyx value.

carrier_name holds what Telnyx returned — 'Verizon Wireless:6006 - SVR/2',
'CELLCO PARTNERSHIP DBA VERIZON WIRELESS - OH' and so on. carrier holds the
family those map to. This fills it in for calls captured before the mapping
existed, and can be re-run after any change to routing/carriers.py.

    python manage.py backfill_carriers --dry-run
    python manage.py backfill_carriers
"""
from collections import Counter

from django.core.management.base import BaseCommand

from analytics.models import CallRecord
from routing.carriers import normalise_carrier
from routing.models import CallLog


class Command(BaseCommand):
    help = "Derive the carrier family from carrier_name on CallLog and CallRecord"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report the mapping, change nothing')

    def handle(self, *args, **options):
        dry = options['dry_run']

        logs = list(
            CallLog.objects.exclude(carrier_name='')
            .values_list('id', 'carrier_name', 'carrier')
        )
        self.stdout.write(f'calls with a raw carrier: {len(logs)}')

        mapping = {}
        families = Counter()
        for _id, raw, current in logs:
            family = normalise_carrier(raw)
            families[family] += 1
            if family != current:
                mapping.setdefault(family, []).append(_id)

        self.stdout.write('-' * 50)
        for family, n in families.most_common():
            self.stdout.write(f'{n:>5}  {family}')
        self.stdout.write('-' * 50)

        blank = CallLog.objects.filter(carrier_name='').count()
        self.stdout.write(f'calls with no carrier at all: {blank} (grouped as Unknown)')
        self.stdout.write(f'rows needing an update: {sum(len(v) for v in mapping.values())}')

        if dry:
            self.stdout.write(self.style.WARNING('\nDry run — nothing written.'))
            return

        updated = 0
        for family, ids in mapping.items():
            updated += CallLog.objects.filter(id__in=ids).update(carrier=family)
            # CallRecord shares the CallLog id, which is how the signal keys it
            CallRecord.objects.filter(id__in=ids).update(carrier=family)

        self.stdout.write(self.style.SUCCESS(f'\nUpdated {updated} calls.'))
        self.stdout.write(
            'CallRecord rows with a carrier: '
            f'{CallRecord.objects.exclude(carrier="").count()}'
        )

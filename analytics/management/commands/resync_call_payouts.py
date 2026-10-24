"""CH-250: past calls show the payout they were paid, not the campaign's.

Reports used to write the campaign's payout on every call, so a number with
its own payout (Yahoo Native $5) showed the campaign rate. New calls are right
from CH-250; this puts the paid figure on calls already in Reports.

    docker compose exec -T web python manage.py resync_call_payouts
    docker compose exec -T web python manage.py resync_call_payouts --apply

Only the report copy changes (Payout and Profit). Nothing is charged or paid
again - the call log already holds the paid figure.
"""
from collections import Counter
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from analytics.models import CallRecord
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Put each call\'s paid payout on its report row (CH-250).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='fix them (default: list only)')

    def handle(self, *args, **opts):
        paid = dict(
            CallLog.objects.filter(publisher_payout__gt=0).values_list('id', 'publisher_payout')
        )
        wrong = []
        per_number = Counter()
        rows = CallRecord.objects.filter(id__in=list(paid)).values_list(
            'id', 'called_number', 'payout', 'revenue')
        for rid, number, payout, revenue in rows.iterator(chunk_size=2000):
            should = Decimal(paid[rid])
            if Decimal(payout or 0) != should:
                wrong.append((rid, should, Decimal(revenue or 0)))
                per_number[(number, Decimal(payout or 0), should)] += 1

        for (number, was, should), n in sorted(per_number.items()):
            self.stdout.write(f'{number}  {n} calls  shown ${was}  paid ${should}')
        self.stdout.write(f'\n{len(wrong)} calls show a payout other than the one paid')
        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to fix them in Reports.')
            return
        with transaction.atomic():
            for rid, should, revenue in wrong:
                CallRecord.objects.filter(id=rid).update(payout=should, profit=revenue - should)
        self.stdout.write(self.style.SUCCESS(f'Fixed {len(wrong)} calls in Reports.'))

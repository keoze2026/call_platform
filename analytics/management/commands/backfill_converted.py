"""Recompute is_converted on existing CallRecord rows.

The mirroring signal never set is_converted, so it is False on nearly every
record regardless of how the call actually went. Reporting could not use it, and
had to gate earnings on status == completed instead — which ignores each
campaign's min_call_duration and so counts short answered calls as earning.

This recomputes the flag the same way call_ended does:

    is_converted = status is completed AND duration >= campaign.min_call_duration

Run --dry-run first and check the counts before applying.

    python manage.py backfill_converted --dry-run
    python manage.py backfill_converted
"""
from django.core.management.base import BaseCommand
from django.db.models import F, Q

from analytics.models import CallRecord


class Command(BaseCommand):
    help = "Recompute is_converted on CallRecord from duration and campaign min_call_duration"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change, change nothing')

    def handle(self, *args, **options):
        dry = options['dry_run']

        total = CallRecord.objects.count()
        self.stdout.write(f'CallRecord rows: {total}')
        self.stdout.write(f'currently is_converted=True: '
                          f'{CallRecord.objects.filter(is_converted=True).count()}')
        self.stdout.write('-' * 60)

        # Completed, and at least as long as its campaign requires. A record with
        # no campaign has no threshold, so completed alone qualifies.
        qualifies = Q(status=CallRecord.Status.COMPLETED) & (
            Q(campaign__isnull=True)
            | Q(duration_seconds__gte=F('campaign__min_call_duration'))
        )

        should_be_true = CallRecord.objects.filter(qualifies)
        should_be_false = CallRecord.objects.exclude(qualifies)

        to_set_true = should_be_true.filter(is_converted=False).count()
        to_set_false = should_be_false.filter(is_converted=True).count()

        self.stdout.write(f'would qualify as converted : {should_be_true.count()}')
        self.stdout.write(f'  rows to flip False -> True: {to_set_true}')
        self.stdout.write(f'  rows to flip True -> False: {to_set_false}')

        completed = CallRecord.objects.filter(status=CallRecord.Status.COMPLETED).count()
        short = completed - should_be_true.count()
        self.stdout.write('-' * 60)
        self.stdout.write(f'completed calls          : {completed}')
        self.stdout.write(f'of those, under threshold: {short}')
        if short:
            self.stdout.write(self.style.WARNING(
                f'  -> reporting currently counts these {short} as earning; '
                f'billing does not. That is the gap this closes.'
            ))
        else:
            self.stdout.write('  -> no short calls, so totals will not move.')

        if dry:
            self.stdout.write(self.style.WARNING('\nDry run — nothing written.'))
            return

        n_true = should_be_true.filter(is_converted=False).update(is_converted=True)
        n_false = should_be_false.filter(is_converted=True).update(is_converted=False)

        self.stdout.write(self.style.SUCCESS(
            f'\nUpdated {n_true + n_false} rows '
            f'({n_true} -> True, {n_false} -> False).'
        ))
        self.stdout.write(f'is_converted=True now: '
                          f'{CallRecord.objects.filter(is_converted=True).count()}')

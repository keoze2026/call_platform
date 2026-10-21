"""CH-187: re-mark past "answered, 0 seconds" calls as Not Connected.

The hangup handler now records a call the buyer answered with 0 seconds of
talk as not connected, the way the upstream Revo counts it. This applies the
same rule to calls already stored, so past days' reports match too.

Dry run (lists what would change):
    python manage.py fix_zero_second_answers --start 2026-10-20 --end 2026-10-20
Apply:
    python manage.py fix_zero_second_answers --start 2026-10-20 --end 2026-10-20 --apply

Dates are Eastern days, inclusive. A call that was charged (platform_cost > 0)
is left alone and listed, because its charge would need a refund by hand.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.asterisk_handler import NOT_CONNECTED_LABEL
from routing.models import CallLog
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')


def _day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f'not a date: {value} (use YYYY-MM-DD)')


class Command(BaseCommand):
    help = 'Mark stored answered calls with 0 seconds of talk as Not Connected.'

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True, help='first Eastern day, YYYY-MM-DD')
        parser.add_argument('--end', required=True, help='last Eastern day, YYYY-MM-DD')
        parser.add_argument('--apply', action='store_true', help='write the change')

    def handle(self, *args, **opts):
        start, end = _day(opts['start']), _day(opts['end'])
        if end < start:
            raise CommandError('--end is before --start')
        lo = datetime.combine(start, time.min, tzinfo=ET)
        hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=ET)

        rows = list(
            CallLog.objects.filter(
                created_at__gte=lo, created_at__lt=hi,
                status=CallLog.Status.COMPLETED, duration__lte=0,
            )
            .exclude(destination_number='')
            .select_related('publisher')
            .order_by('created_at')
        )
        charged = [r for r in rows if (r.platform_cost or 0) > 0]
        todo = [r for r in rows if (r.platform_cost or 0) <= 0]

        for r in rows:
            self.stdout.write('{}  {}  {:<14} {:<12} {}{}'.format(
                r.created_at.astimezone(ET).strftime('%Y-%m-%d %H:%M:%S'),
                r.caller_number, r.called_number,
                r.publisher.name if r.publisher_id else '-',
                r.id, '  CHARGED, left alone' if r in charged else '',
            ))
        self.stdout.write(f'\n{len(rows)} answered calls with 0 seconds, {start} to {end} ET')
        if charged:
            self.stdout.write(self.style.WARNING(f'{len(charged)} were charged and are left alone'))

        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to mark them Not Connected.')
            return

        for r in todo:
            with transaction.atomic():
                CallLog.objects.filter(pk=r.pk, status=CallLog.Status.COMPLETED).update(
                    status=CallLog.Status.NO_ANSWER,
                    block_reason=r.block_reason or NOT_CONNECTED_LABEL,
                    revenue=0,
                    publisher_payout=0,
                )
            mirror_call_log(r.id)
        self.stdout.write(self.style.SUCCESS(f'Marked {len(todo)} calls Not Connected.'))

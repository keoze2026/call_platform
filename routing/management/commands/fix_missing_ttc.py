"""CH-188: fill TTC and the fail reason on past calls where the caller hung
up before the destination could ring.

Those calls were routed, then the caller left in the same second, before
Dial() ran, so the switch sent no dial result and the Call Log showed a dash
for TTC and for the fail reason. New calls are recorded correctly by the
hangup handler; this fixes the ones already stored.

Dry run:  python manage.py fix_missing_ttc --start 2026-10-01 --end 2026-10-21
Apply:    python manage.py fix_missing_ttc --start 2026-10-01 --end 2026-10-21 --apply

Dates are Eastern days, inclusive. Only unanswered calls that went to a
destination and carry no fail reason at all are touched.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

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
    help = 'Fill TTC and "Not Connected" on calls the caller left before Dial() ran.'

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
                status=CallLog.Status.NO_ANSWER, ring_seconds__isnull=True,
                block_reason='', ended_at__isnull=False,
            )
            .exclude(destination_number='')
            .order_by('created_at')
        )
        for r in rows:
            wait = max(0, int((r.ended_at - r.created_at).total_seconds()))
            self.stdout.write('{}  {}  {}  -> {}  TTC {}s'.format(
                r.created_at.astimezone(ET).strftime('%Y-%m-%d %H:%M:%S'),
                r.caller_number, r.called_number, r.destination_number, wait,
            ))
        self.stdout.write(f'\n{len(rows)} calls with no TTC and no fail reason, {start} to {end} ET')

        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to fill them in.')
            return

        for r in rows:
            wait = max(0, int((r.ended_at - r.created_at).total_seconds()))
            CallLog.objects.filter(pk=r.pk, ring_seconds__isnull=True, block_reason='').update(
                ring_seconds=wait, block_reason=NOT_CONNECTED_LABEL,
            )
            mirror_call_log(r.id)
        self.stdout.write(self.style.SUCCESS(f'Filled {len(rows)} calls.'))

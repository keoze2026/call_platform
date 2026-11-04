"""CH-519: take the refused line and buyer off calls that no line received.

Until CH-519 a call refused on the destination side ("No Destination": line
off, buyer paused, cap full, not on the Destinations page) was written with the
first refused line's TFN and buyer, so it counted under that buyer and
destination even though nothing was dialled. On 4 Nov 77 test calls made with
every destination off showed under Q04|5984|, YNA, PDSO and BUB|2828|.

This clears destination_number, buyer and rule_destination on those rows, so
they show as "Not forwarded" and count against no buyer or destination. Nothing
is deleted: the old line and buyer are kept on the row in
routing_trace["ch519_cleared"], and the routing trace still says which line
refused the call and why. The reporting copy (CallRecord) is rewritten.

Only rows that were never dialled are touched: block_reason "No Destination",
status missed, no talk time, no ring time, no revenue or buyer payout. Any row
with money on it is listed and left alone.

Dry run (lists what would change):
    python manage.py clear_refused_lines --start 2026-11-04T00:00 --end 2026-11-04T06:00
Apply:
    python manage.py clear_refused_lines --start 2026-11-04T00:00 --end 2026-11-04T06:00 --apply

Times are Eastern. A plain date (YYYY-MM-DD) means the whole day; --end is
inclusive for a date and exclusive for a date and time.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import CallLog
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')
NO_DESTINATION = 'No Destination'


def _when(value, end=False):
    try:
        if 'T' in value or ' ' in value.strip():
            return datetime.fromisoformat(value.strip().replace(' ', 'T')).replace(tzinfo=ET)
        d = date.fromisoformat(value.strip())
    except ValueError:
        raise CommandError(f'not a date or time: {value} (use YYYY-MM-DD or YYYY-MM-DDTHH:MM)')
    return datetime.combine(d + timedelta(days=1) if end else d, time.min, tzinfo=ET)


def _money(r):
    return (r.revenue or 0) > 0 or (r.buyer_payout or 0) > 0


class Command(BaseCommand):
    help = 'Show calls no line received as Not forwarded (clears the refused line and buyer).'

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True, help='Eastern start, YYYY-MM-DD or YYYY-MM-DDTHH:MM')
        parser.add_argument('--end', required=True, help='Eastern end, YYYY-MM-DD or YYYY-MM-DDTHH:MM')
        parser.add_argument('--apply', action='store_true', help='write the change')

    def handle(self, *args, **opts):
        lo, hi = _when(opts['start']), _when(opts['end'], end=True)
        if hi <= lo:
            raise CommandError('--end is not after --start')

        rows = list(
            CallLog.objects.filter(
                created_at__gte=lo, created_at__lt=hi,
                block_reason=NO_DESTINATION, status=CallLog.Status.NO_ANSWER,
                retry_hidden=False, duration__lte=0,
            )
            .exclude(destination_number='', buyer__isnull=True)
            .select_related('buyer')
            .order_by('created_at')
        )
        rows = [r for r in rows if not (r.ring_seconds or 0)]
        money = [r for r in rows if _money(r)]
        todo = [r for r in rows if not _money(r)]

        per_line = Counter((r.buyer.name if r.buyer_id else '-', r.destination_number or '-') for r in rows)
        for (buyer, number), n in sorted(per_line.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'{n:>5}  {buyer:<12} {number}')
        for r in money:
            self.stdout.write(self.style.WARNING(
                f'LEFT ALONE (has revenue/payout): {r.id} {r.caller_number} {r.destination_number}'))
        self.stdout.write(
            f'\n{len(rows)} calls never dialled but written under a line, '
            f'{lo:%Y-%m-%d %H:%M} to {hi:%Y-%m-%d %H:%M} ET; {len(todo)} to clear')

        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to show them as Not forwarded.')
            return

        done = 0
        for r in todo:
            with transaction.atomic():
                row = CallLog.objects.select_for_update().get(pk=r.pk)
                if row.block_reason != NO_DESTINATION or row.status != CallLog.Status.NO_ANSWER:
                    continue
                trace = dict(row.routing_trace or {})
                trace['ch519_cleared'] = {
                    'destination_number': row.destination_number,
                    'buyer_id': str(row.buyer_id) if row.buyer_id else None,
                    'buyer_name': row.buyer.name if row.buyer_id else None,
                    'rule_destination_id': str(row.rule_destination_id) if row.rule_destination_id else None,
                }
                CallLog.objects.filter(pk=row.pk).update(
                    destination_number='', buyer=None, rule_destination=None, routing_trace=trace)
                done += 1
            mirror_call_log(r.id)
        self.stdout.write(self.style.SUCCESS(f'Cleared {done} calls; they now show as Not forwarded.'))

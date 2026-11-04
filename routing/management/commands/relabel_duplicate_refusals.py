"""CH-526: past calls refused as "Duplicate call" (a repeat caller on Different
with no line left) get the comment "No Destination", as new ones do.

    docker compose exec -T web python manage.py relabel_duplicate_refusals --start 2026-11-04 --end 2026-11-04
    ... --apply

Eastern dates (or YYYY-MM-DDTHH:MM). Dry run by default. Only calls that were
never sent to a line are touched; nothing is deleted. The old comment is kept
on the call in routing_trace["ch526_relabelled"], and Repeated stays as it was.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import CallLog
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')
OLD, NEW = 'Duplicate call', 'No Destination'


def _when(value, end=False):
    try:
        if 'T' in value or ' ' in value.strip():
            return datetime.fromisoformat(value.strip().replace(' ', 'T')).replace(tzinfo=ET)
        d = date.fromisoformat(value.strip())
    except ValueError:
        raise CommandError(f'not a date or time: {value}')
    return datetime.combine(d + timedelta(days=1) if end else d, time.min, tzinfo=ET)


class Command(BaseCommand):
    help = 'Write "No Destination" on past calls refused as "Duplicate call".'

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True)
        parser.add_argument('--end', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **o):
        lo, hi = _when(o['start']), _when(o['end'], end=True)
        if hi <= lo:
            raise CommandError('--end is not after --start')
        rows = list(CallLog.objects.filter(created_at__gte=lo, created_at__lt=hi, block_reason=OLD,
                                           status=CallLog.Status.NO_ANSWER, destination_number='')
                    .select_related('campaign').order_by('created_at'))
        shown = [r for r in rows if not r.retry_hidden]
        per = Counter(r.campaign.name if r.campaign_id else '-' for r in shown)
        for name, n in per.most_common():
            self.stdout.write(f'  {n:5}  {name}')
        self.stdout.write(f'\n{len(rows)} calls "{OLD}" never sent to a line, {lo:%Y-%m-%d %H:%M} to '
                          f'{hi:%Y-%m-%d %H:%M} ET ({len(shown)} shown, {len(rows) - len(shown)} grouped redials)')
        if not o['apply']:
            self.stdout.write(f'Dry run. Add --apply to write "{NEW}" on them.')
            return
        done = 0
        for r in rows:
            with transaction.atomic():
                row = CallLog.objects.select_for_update().get(pk=r.pk)
                if row.block_reason != OLD or row.destination_number:
                    continue
                trace = dict(row.routing_trace or {})
                trace['ch526_relabelled'] = OLD
                CallLog.objects.filter(pk=row.pk).update(block_reason=NEW, routing_trace=trace)
                done += 1
            if not r.retry_hidden:
                mirror_call_log(r.id)
        self.stdout.write(self.style.SUCCESS(f'Relabelled {done} calls "{NEW}".'))

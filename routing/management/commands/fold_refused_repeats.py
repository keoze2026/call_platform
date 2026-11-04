"""CH-527: on a day already taken, a caller refused "No Destination" again and
again in one campaign (no line could take him) shows as one row.

    docker compose exec -T web python manage.py fold_refused_repeats --date 2026-11-04
    docker compose exec -T web python manage.py fold_refused_repeats --date 2026-11-04 --apply

Eastern day. Dry run by default. Nothing is deleted: the earlier refusals stay
in the call log, hidden (retry_hidden), as every grouped try does.
"""
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from routing.models import CallLog
from routing.retry_group import link
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')


class Command(BaseCommand):
    help = 'Show a caller refused again and again (No Destination) as one row.'

    def add_arguments(self, parser):
        parser.add_argument('--date', required=True, help='Eastern date, YYYY-MM-DD')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **o):
        try:
            day = datetime.strptime(o['date'], '%Y-%m-%d').date()
        except ValueError:
            raise CommandError('--date must be YYYY-MM-DD')
        lo = datetime.combine(day, time.min, tzinfo=ET)
        window = timedelta(minutes=max(1, int(getattr(settings, 'REFUSED_REPEAT_MINUTES', 30) or 30)))
        rows = list(CallLog.objects.filter(created_at__gte=lo, created_at__lt=lo + timedelta(days=1),
                                           retry_hidden=False, destination_number='',
                                           block_reason='No Destination')
                    .order_by('created_at'))
        last, pairs = {}, []
        for r in rows:
            key = (r.organization_id, r.caller_number, r.campaign_id)
            prev = last.get(key)
            if prev is not None and r.created_at - prev.created_at <= window:
                pairs.append((r, prev))
            last[key] = r
        callers = Counter(r.caller_number for r, _ in pairs)
        self.stdout.write(f'{day} (Eastern): {len(rows)} "No Destination" rows shown; '
                          f'{len(pairs)} are the same caller refused again -> {len(rows) - len(pairs)} rows after')
        for caller, n in callers.most_common(10):
            self.stdout.write(f'  {caller}  {n + 1} rows -> 1')
        if not o['apply']:
            self.stdout.write('Dry run. Add --apply to fold them.')
            return
        for r, prev in pairs:
            prev.refresh_from_db()
            link(r, prev)
            mirror_call_log(r.pk)
        self.stdout.write(self.style.SUCCESS(f'Folded {len(pairs)} refusals; nothing deleted.'))

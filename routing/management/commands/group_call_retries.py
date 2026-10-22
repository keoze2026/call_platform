"""Group a sender's retries into one row for calls already taken (CH-215).

New calls are grouped as they arrive. This applies the same rule to a day that
is already in the database, so the red rows from before the change go too.

    docker compose exec -T web python manage.py group_call_retries
    docker compose exec -T web python manage.py group_call_retries --date 2026-10-22
    docker compose exec -T web python manage.py group_call_retries --date 2026-10-22 --apply

Without --apply it only reports what it would do. Nothing is ever deleted
from the call log: a grouped try is marked hidden and leaves the reports
mirror, and can be brought back by clearing retry_hidden and re-mirroring.
"""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import CallLog
from routing.retry_group import is_retry


class Command(BaseCommand):
    help = "Group a sending platform's retries of one call into one row (as Revo shows it)."

    def add_arguments(self, parser):
        parser.add_argument('--date', default='', help='Eastern date, YYYY-MM-DD (default today)')
        parser.add_argument('--apply', action='store_true', help='Write the grouping (default: dry run)')

    def handle(self, *args, **o):
        ny = ZoneInfo('America/New_York')
        day = (datetime.strptime(o['date'], '%Y-%m-%d').date() if o['date']
               else datetime.now(ny).date())
        start = datetime.combine(day, time.min, tzinfo=ny)
        end = start + timedelta(days=1)

        calls = list(CallLog.objects.filter(created_at__gte=start, created_at__lt=end)
                     .select_related('buyer').order_by('created_at'))
        last = {}      # (org, caller, called) -> the latest try still shown
        before = {}    # call id -> the try ahead of it in the same call
        root_of = {}   # call id -> first try of its call
        hide, link = [], []

        for c in calls:
            if c.retry_hidden:
                continue
            key = (c.organization_id, c.caller_number, c.called_number)
            prev = last.get(key)
            if prev is not None and is_retry(prev, before.get(prev.pk), c.created_at):
                root_of[c.pk] = root_of.get(prev.pk, prev.retry_root_id or prev.pk)
                before[c.pk] = prev
                hide.append(prev)
                link.append(c)
            last[key] = c

        shown_before = sum(1 for c in calls if not c.retry_hidden)
        missed = Counter((c.buyer.name if c.buyer_id else '-') for c in calls
                         if c.status == CallLog.Status.NO_ANSWER and not c.retry_hidden)
        hidden_missed = Counter((c.buyer.name if c.buyer_id else '-') for c in hide
                                if c.status == CallLog.Status.NO_ANSWER)

        self.stdout.write(f'{day} (Eastern): {len(calls)} calls, {len(hide)} are a sender retrying the next one')
        self.stdout.write(f'rows shown: {shown_before} now -> {shown_before - len(hide)} after grouping')
        self.stdout.write('\nno-answer rows per buyer: now -> after')
        for name, n in missed.most_common():
            self.stdout.write(f'  {name:<24} {n:>4} -> {n - hidden_missed.get(name, 0)}')

        groups = defaultdict(list)
        for c in link:
            groups[root_of[c.pk]].append(c)
        if groups:
            self.stdout.write('\nlargest groups (first try, caller, tries):')
            by_id = {c.pk: c for c in calls}
            for root_id, tail in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:10]:
                first = by_id.get(root_id)
                when = first.created_at.astimezone(ny).strftime('%I:%M:%S %p') if first else '?'
                self.stdout.write(f'  {when}  {tail[-1].caller_number}  {len(tail) + 1} tries -> 1 row')

        if not o['apply']:
            self.stdout.write('\nDry run. Add --apply to group them.')
            return

        from analytics.models import CallRecord
        from routing.signals import mirror_call_log
        with transaction.atomic():
            for c in link:
                CallLog.objects.filter(pk=c.pk).update(retry_root_id=root_of[c.pk])
            CallLog.objects.filter(pk__in=[c.pk for c in hide]).update(retry_hidden=True)
            CallRecord.objects.filter(id__in=[c.pk for c in hide]).delete()
        # The visible try of each call is re-mirrored: dated from its first try.
        tails = {c.pk for c in link} - {c.pk for c in hide}
        for pk in tails:
            mirror_call_log(pk)
        self.stdout.write(self.style.SUCCESS(f'\nGrouped: {len(hide)} tries hidden, {len(tails)} rows updated.'))

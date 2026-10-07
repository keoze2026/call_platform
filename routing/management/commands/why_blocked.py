"""Say which rule actually refused each call, and whether it could have been the
duplicate rule.

The boss read "Caller is blacklisted" on rows he believes were dropped as
duplicates. Both are possible for one call - the engine checks the blacklist
first and returns on the first rule that refuses - so the label alone cannot
settle it. This prints the campaign's duplicate setting, the reason recorded,
and for the most frequent blocked callers whether the number is actually on the
blacklist.

    docker compose exec -T web python manage.py why_blocked
    docker compose exec -T web python manage.py why_blocked --date 2026-10-07
"""
from collections import Counter
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from routing.models import CallLog


class Command(BaseCommand):
    help = 'Break refused calls down by the rule that refused them.'

    def add_arguments(self, parser):
        parser.add_argument('--date', default='')
        parser.add_argument('--tz', default='America/New_York')
        parser.add_argument('--top', default=10, type=int)

    def handle(self, *args, **o):
        import zoneinfo
        tz = zoneinfo.ZoneInfo(o['tz'])

        if o['date']:
            day = datetime.strptime(o['date'], '%Y-%m-%d').date()
        else:
            day = timezone.now().astimezone(tz).date()
        start = datetime.combine(day, time.min, tzinfo=tz)
        end = start + timedelta(days=1)

        calls = CallLog.objects.filter(created_at__gte=start, created_at__lt=end)
        blocked = calls.exclude(block_reason='')

        self.stdout.write(f'{day} in {o["tz"]}   {calls.count()} calls, {blocked.count()} refused\n')

        self.stdout.write('reason recorded')
        for reason, n in Counter(blocked.values_list('block_reason', flat=True)).most_common():
            self.stdout.write(f'  {n:>5}  {reason}')

        # Is the duplicate rule even switched on? If it is off, no call on this
        # campaign can have been refused as a duplicate, whatever the row says.
        self.stdout.write('')
        self.stdout.write('campaign settings')
        seen = set()
        for call in calls.select_related('campaign').exclude(campaign=None)[:2000]:
            c = call.campaign
            if c.id in seen:
                continue
            seen.add(c.id)
            state = 'ON' if c.duplicate_call_block else 'OFF'
            hours = c.duplicate_call_block_hours
            self.stdout.write(f'  {c.name}: duplicate blocking {state}, window {hours}h')

        # The callers the boss is looking at: the same number, over and over.
        from spam_protection.models import Blacklist
        self.stdout.write('')
        self.stdout.write(f'top {o["top"]} refused callers')
        self.stdout.write('calls  number        on blacklist?  reason recorded')
        counts = Counter(blocked.values_list('caller_number', flat=True))
        for number, n in counts.most_common(o['top']):
            entry = Blacklist.objects.filter(phone_number=number, is_active=True).first()
            if entry:
                scope = 'campaign' if entry.campaign_id else 'org-wide'
                mark = f'YES ({scope}, added {entry.created_at:%Y-%m-%d})'
            else:
                mark = 'no'
            reason = (blocked.filter(caller_number=number)
                      .values_list('block_reason', flat=True).first() or '')
            self.stdout.write(f'{n:>5}  {number:<13} {mark:<30} {reason}')

        self.stdout.write('')
        self.stdout.write(
            'A number refused repeatedly with "no" in the blacklist column means '
            'the label is wrong. "YES" means the blacklist really is what stopped '
            'it, and the duplicate rule never got a turn.')

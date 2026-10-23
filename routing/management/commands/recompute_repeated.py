"""CH-230: recompute the Repeated column for a day with the new rule.

Repeated now counts ANY earlier call from the caller to the campaign inside the
campaign's window, answered or not (as Revo's Dupe). Calls stored before CH-230
were marked only when the earlier call was answered or live. This rewrites a
past day the new way and re-mirrors the changed rows into Reports.

    docker compose exec -T web python manage.py recompute_repeated --start 2026-10-23 --end 2026-10-23
    ... same with --apply to write it.

A sender's retry hidden into its call (CH-215) is never counted. Routing and
Qualified are not affected.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from routing.models import CallLog

ET = ZoneInfo('America/New_York')


def _day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f'not a date: {value} (use YYYY-MM-DD)')


class Command(BaseCommand):
    help = 'Recompute Repeated for a range of Eastern days (CH-230).'

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True)
        parser.add_argument('--end', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        start, end = _day(opts['start']), _day(opts['end'])
        lo = datetime.combine(start, time.min, tzinfo=ET)
        hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=ET)
        # Earlier calls up to the widest window before the range count too.
        widest = 24 * 7
        calls = list(CallLog.objects
                     .filter(created_at__gte=lo - timedelta(hours=widest), created_at__lt=hi,
                             retry_hidden=False)
                     .select_related('campaign').order_by('created_at'))
        last = {}
        changes = []
        for c in calls:
            key = (c.campaign_id, c.caller_number)
            hours = (getattr(c.campaign, 'duplicate_call_block_hours', 24) or 24) if c.campaign_id else 24
            prev = last.get(key)
            repeat = bool(c.campaign_id) and prev is not None and c.created_at - prev <= timedelta(hours=hours)
            last[key] = c.created_at
            if c.created_at < lo:
                continue
            if repeat != c.is_duplicate:
                changes.append((c, repeat))
        to_yes = sum(1 for _, r in changes if r)
        self.stdout.write(f'{start} to {end} ET: {len(changes)} rows change - '
                          f'{to_yes} to Repeated Yes, {len(changes) - to_yes} to No')
        for c, r in changes[:15]:
            self.stdout.write(f'  {c.created_at.astimezone(ET):%m-%d %I:%M:%S %p}  {c.caller_number}  -> '
                              f'{"Yes" if r else "No"}')
        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to write it.')
            return
        from routing.signals import mirror_call_log
        for c, r in changes:
            CallLog.objects.filter(pk=c.pk).update(is_duplicate=r)
            mirror_call_log(c.pk)
        self.stdout.write(self.style.SUCCESS(f'Updated {len(changes)} rows.'))

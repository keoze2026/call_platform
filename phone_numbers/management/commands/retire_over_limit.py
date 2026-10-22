"""Run the nightly "over the daily limit" rule for one Eastern day (CH-197).

The 1:30 AM sweep runs this by itself every night. Use it by hand to catch up a
day the sweep missed, e.g. TFNs that were paused when it ran before CH-197.

Dry run (lists the numbers, changes nothing):
    python manage.py retire_over_limit --day 2026-10-21
Retire them (each sends the "TFN Deleted" message):
    python manage.py retire_over_limit --day 2026-10-21 --apply
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from phone_numbers.lifecycle import sweep_over_limit


class Command(BaseCommand):
    help = 'Retire numbers that took more answered calls than their limit on one Eastern day.'

    def add_arguments(self, parser):
        parser.add_argument('--day', required=True, help='Eastern day, YYYY-MM-DD')
        parser.add_argument('--apply', action='store_true', help='retire them and send the messages')

    def handle(self, *args, **opts):
        try:
            day = date.fromisoformat(opts['day'])
        except ValueError:
            raise CommandError(f"not a date: {opts['day']} (use YYYY-MM-DD)")
        # Read the list before retiring: retiring clears the campaign and publisher.
        rows = [(n.number, n.status, n.carrier.code, n.campaign.name if n.campaign_id else '-',
                 n.publisher.name if n.publisher_id else '-', answered, limit)
                for n, answered, limit in sweep_over_limit(day, apply=False)]
        for number, status, code, camp, pub, answered, limit in rows:
            self.stdout.write(f'{number}  {status:<8} {code:<5} {camp:<12} {pub:<12} '
                              f'{answered} answered, limit {limit}')
        self.stdout.write(f'\n{len(rows)} numbers over their limit on {day} ET')
        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to retire them and send the TFN Deleted messages.')
            return
        done = sweep_over_limit(day, apply=True)
        self.stdout.write(self.style.SUCCESS(f'Retired {len(done)} numbers.'))

"""CH-290: match our calls with RevoCalls and correct the crossings.

Reads Revo's call log for the last few minutes through Revo's API and, for a
call Revo shows Completed that we missed only because the buyer's answer
crossed the caller hanging up, makes ours Completed with Revo's duration
(see routing/revo_sync.py for the exact rule). Everything else is left alone;
anything that disagrees in another way is listed as "check".

    docker compose exec -T web python manage.py sync_revo            # dry run, last 20 minutes
    docker compose exec -T web python manage.py sync_revo --apply
    docker compose exec -T web python manage.py sync_revo --minutes 600   # look further back

Run by host cron every minute once Sir has approved (--apply). Running it
again changes nothing: a corrected call carries "revo:<id>" and is skipped.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from routing import revo_sync


class Command(BaseCommand):
    help = 'Match Avortyx calls with RevoCalls and correct crossed answers (CH-290).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='write the corrections')
        parser.add_argument('--minutes', type=int, default=20, help='how far back to look (default 20)')
        parser.add_argument('--quiet', action='store_true', help='print only changes, checks and the summary')

    def handle(self, *args, **o):
        since = timezone.now() - timedelta(minutes=max(1, o['minutes']))
        try:
            revo_calls = revo_sync.fetch(since)
        except Exception as exc:
            raise CommandError(f'Revo not read: {exc}')
        counts = {}
        for rc in sorted(revo_calls, key=lambda c: c.started):
            call = revo_sync.find_missed(rc)
            kind, why = revo_sync.verdict(rc, call)
            if kind == 'fix':
                if o['apply']:
                    kind = 'fixed' if revo_sync.apply_fix(call, rc) else 'skipped'
                else:
                    kind = 'would fix'
            counts[kind] = counts.get(kind, 0) + 1
            if o['quiet'] and kind in ('ok', 'none', 'done', 'wait'):
                continue
            when = timezone.localtime(rc.started, revo_sync.ET).strftime('%I:%M:%S %p ET')
            self.stdout.write(f'{kind:9} {when} {rc.caller} -> {rc.called}  Revo "{rc.status}" '
                              f'{rc.duration}s TTC {rc.ttc}s  | {why}')
        summary = ', '.join(f'{v} {k}' for k, v in sorted(counts.items())) or 'no calls'
        self.stdout.write(f'{timezone.now():%Y-%m-%d %H:%M:%S} UTC  Revo calls read: {len(revo_calls)}; {summary}'
                          + ('' if o['apply'] else '  [dry run]'))

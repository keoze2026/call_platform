"""CH-290: match our calls with RevoCalls and correct the crossings.

Reads Revo's call log for the last few minutes through Revo's API and, for a
call Revo shows Completed that we missed only because the buyer's answer
crossed the caller hanging up, makes ours Completed with Revo's duration
(see routing/revo_sync.py for the exact rule). Everything else is left alone;
anything that disagrees in another way is listed as "check".

    docker compose exec -T web python manage.py sync_revo            # dry run, last 20 minutes
    docker compose exec -T web python manage.py sync_revo --apply
    docker compose exec -T web python manage.py sync_revo --minutes 600   # look further back

CH-291 - a whole day, or only the callers Sir lists (with our own rows of the
day for each, so "Revo 1, Avortyx 2" can be seen side by side):

    ... sync_revo --date 2026-10-26
    ... sync_revo --date 2026-10-26 --caller 18704239446,18622367681

CH-316 (Sir): Revo Completed <= 2 s + ours missed + same caller and TFN +
answer within 2 s of our hang-up -> "Answered at Hangup", counted as matched.
The last line gives "Matched N of M".

Run by host cron every minute once Sir has approved (--apply). Running it
again changes nothing: a corrected call carries "revo:<id>" and is skipped.
"""
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from routing import revo_sync
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Match Avortyx calls with RevoCalls and correct crossed answers (CH-290).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='write the corrections')
        parser.add_argument('--minutes', type=int, default=20, help='how far back to look (default 20)')
        parser.add_argument('--quiet', action='store_true', help='print only changes, checks and the summary')
        parser.add_argument('--date', help='a whole Eastern day, YYYY-MM-DD (instead of --minutes)')
        parser.add_argument('--caller', help='only these callers, comma separated (Revo search)')

    def handle(self, *args, **o):
        until = None
        if o['date']:
            try:
                day = datetime.strptime(o['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--date must be YYYY-MM-DD')
            since = datetime.combine(day, time.min, revo_sync.ET)
            until = since + timedelta(days=1)
        else:
            since = timezone.now() - timedelta(minutes=max(1, o['minutes']))
        callers = [revo_sync._last10(c) for c in (o['caller'] or '').split(',') if revo_sync._last10(c)]
        try:
            if callers:
                revo_calls = []
                seen = set()
                for c in callers:
                    # Revo's search also matches other columns: keep this caller's rows, once.
                    for rc in revo_sync.fetch(since, search=c, until=until, max_pages=5):
                        if rc.caller == c and rc.id not in seen:
                            seen.add(rc.id)
                            revo_calls.append(rc)
            else:
                revo_calls = revo_sync.fetch(since, until=until, max_pages=200 if o['date'] else 40)
        except Exception as exc:
            _remember(error=str(exc))
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
        for c in callers:
            # Our own rows of the window for this caller, to compare counts.
            ours = (CallLog.objects.filter(caller_number__endswith=c, created_at__gte=since,
                                           created_at__lt=until or timezone.now())
                    .order_by('created_at'))
            revo_n = sum(1 for rc in revo_calls if rc.caller == c)
            self.stdout.write(f'  {c}: Revo {revo_n} call(s), Avortyx {ours.count()}')
            for row in ours:
                when = timezone.localtime(row.created_at, revo_sync.ET).strftime('%I:%M:%S %p ET')
                self.stdout.write(f'    ours {when} -> {row.destination_number}  {row.status} '
                                  f'{row.duration or 0}s "{row.block_reason}"'
                                  + ('  (hidden retry)' if getattr(row, 'retry_hidden', False) else ''))
        if o['apply'] and not o['date'] and not callers:
            _remember(read=len(revo_calls), fixed=counts.get('fixed', 0), check=counts.get('check', 0))
        summary = ', '.join(f'{v} {k}' for k, v in sorted(counts.items())) or 'no calls'
        # CH-316 (Sir): an Answered at Hangup call counts as matched.
        matched = sum(counts.get(k, 0) for k in ('ok', 'done', 'fixed', 'would fix'))
        hangup = sum(counts.get(k, 0) for k in ('done', 'fixed', 'would fix'))
        compared = matched + counts.get('check', 0) + counts.get('skipped', 0)
        if compared:
            self.stdout.write(f'Matched {matched} of {compared} ({100 * matched / compared:.1f}%), '
                              f'{hangup} of them Answered at Hangup; {counts.get("check", 0)} to check'
                              + (f'; {counts["none"]} Revo call(s) never reached Avortyx' if counts.get('none') else ''))
        self.stdout.write(f'{timezone.now():%Y-%m-%d %H:%M:%S} UTC  Revo calls read: {len(revo_calls)}; {summary}'
                          + ('' if o['apply'] else '  [dry run]'))


def _remember(**kw):
    """The last automatic run, for the status bot (CH-314)."""
    import time
    from django.core.cache import cache
    try:
        cache.set('status:revo_sync', {'at': time.time(), **kw}, 86400)
    except Exception:
        pass

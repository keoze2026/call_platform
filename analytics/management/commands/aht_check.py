"""Show every number behind TCL and AHT, and where they disagree.

The boss's AHT and the portal's ACL did not match. ACL is TCL / Connected, so
only three things can cause it: what goes into the numerator, what goes into
the denominator, and the window. This prints all three for one day so the
difference can be named rather than guessed.

    docker compose exec -T web python manage.py aht_check
    docker compose exec -T web python manage.py aht_check --date 2026-10-07
"""
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum, Q
from django.utils import timezone

from analytics.models import CallRecord

CONNECTED = ['completed', 'in_progress']


def mmss(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 60}:{seconds % 60:02d}'


def hhmmss(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'


class Command(BaseCommand):
    help = 'Break down TCL and AHT by status for one day.'

    def add_arguments(self, parser):
        parser.add_argument('--date', default='', help='YYYY-MM-DD, default today')
        parser.add_argument('--tz', default='America/New_York',
                            help='The zone the portal is showing, default Eastern')
        parser.add_argument('--org', default='', help='Organization name, default every one')

    def handle(self, *args, **o):
        import zoneinfo
        tz = zoneinfo.ZoneInfo(o['tz'])

        if o['date']:
            day = datetime.strptime(o['date'], '%Y-%m-%d').date()
        else:
            day = timezone.now().astimezone(tz).date()

        start = datetime.combine(day, time.min, tzinfo=tz)
        end = start + timedelta(days=1)

        qs = CallRecord.objects.filter(started_at__gte=start, started_at__lt=end)
        if not qs.exists():
            # Older rows predate started_at being filled.
            qs = CallRecord.objects.filter(created_at__gte=start, created_at__lt=end)
            self.stdout.write(self.style.WARNING('no rows on started_at, fell back to created_at'))
        if o['org']:
            qs = qs.filter(organization__name=o['org'])

        self.stdout.write(f'{day} in {o["tz"]}   {qs.count()} calls\n')

        self.stdout.write('status        calls   total time   avg')
        for row in (qs.values('status')
                      .annotate(n=Count('id'), secs=Sum('duration_seconds'))
                      .order_by('-n')):
            secs = row['secs'] or 0
            avg = secs / row['n'] if row['n'] else 0
            mark = '  <- counted as Connected' if row['status'] in CONNECTED else ''
            self.stdout.write(
                f'{row["status"]:<13} {row["n"]:>5}   {hhmmss(secs):>10}   {mmss(avg):>6}{mark}'
            )

        agg = qs.aggregate(
            all_secs=Sum('duration_seconds'),
            conn_secs=Sum('duration_seconds', filter=Q(status__in=CONNECTED)),
            conn=Count('id', filter=Q(status__in=CONNECTED)),
            notconn=Count('id', filter=~Q(status__in=CONNECTED)),
        )
        total = qs.count()
        conn = agg['conn'] or 0
        all_secs = agg['all_secs'] or 0
        conn_secs = agg['conn_secs'] or 0

        self.stdout.write('')
        self.stdout.write(f'Incoming            {total}')
        self.stdout.write(f'Connected           {conn}')
        self.stdout.write(f'Not connected       {agg["notconn"]}')
        gap = total - conn - (agg['notconn'] or 0)
        if gap:
            self.stdout.write(self.style.ERROR(f'unaccounted          {gap}'))

        self.stdout.write('')
        self.stdout.write(f'TCL, every call     {hhmmss(all_secs)}   (shown as {all_secs // 60}:{all_secs % 60:02d})')
        self.stdout.write(f'TCL, connected only {hhmmss(conn_secs)}')
        if all_secs != conn_secs:
            self.stdout.write(self.style.WARNING(
                f'  {hhmmss(all_secs - conn_secs)} belongs to calls that are NOT in the '
                f'Connected count, so it inflated AHT'))

        self.stdout.write('')
        if conn:
            self.stdout.write(f'AHT was   TCL(all) / Connected        {mmss(all_secs / conn)}')
            self.stdout.write(self.style.SUCCESS(
                f'AHT now   TCL(connected) / Connected  {mmss(conn_secs / conn)}'))
        if total:
            self.stdout.write(f'          TCL(all) / Incoming         {mmss(all_secs / total)}')

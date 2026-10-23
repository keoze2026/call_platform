"""CH-222: put back calls the buyer answered for under a second.

With ZERO_SECOND_ANSWER_IS_NOT_CONNECTED=False new calls are counted the way
Revo counts them. This does the same for calls already stored as Not Connected
by the old rule. Proof comes from the switch's own record (the CDR): only a
call the switch logged as ANSWERED with 0 billable seconds is changed - a call
that was never answered (the caller gave up while it rang) also reads
"Not Connected" in our log, and is left alone.

The CDR lives on the host, so it is piped in:

    docker compose exec -T web python manage.py restore_short_answers \\
        --start 2026-10-23 --end 2026-10-23 < /var/log/asterisk/cdr-csv/Master.csv
    ... same with --apply to write it.

Dates are Eastern days, inclusive. A restored call is Completed, 1 second, with
its ring from the CDR. Money is not touched: a campaign that converts at 1
second would also charge the call, and that is listed for a person to decide.
"""
import csv
import sys
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.asterisk_handler import NOT_CONNECTED_LABEL
from routing.models import CallLog

ET = ZoneInfo('America/New_York')
# Master.csv columns (Asterisk cdr_csv): src 1, lastapp 7, start 9, answer 10,
# end 11, duration 12, billsec 13, disposition 14.
SRC, LASTAPP, START, ANSWER, END, BILLSEC, DISPOSITION = 1, 7, 9, 10, 11, 13, 14
MATCH_SECONDS = 3


def _day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f'not a date: {value} (use YYYY-MM-DD)')


def _utc(value):
    """CDR times are written in UTC on this server (checked 23 Oct against the
    call log to the second)."""
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


def _digits(value):
    return ''.join(ch for ch in value or '' if ch.isdigit())[-10:]


class Command(BaseCommand):
    help = 'Restore calls answered for under a second (CDR proof) from Not Connected to Completed, 1s.'

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True, help='first Eastern day, YYYY-MM-DD')
        parser.add_argument('--end', required=True, help='last Eastern day, YYYY-MM-DD')
        parser.add_argument('--cdr', default='-', help='Master.csv path, or - for stdin (default)')
        parser.add_argument('--apply', action='store_true', help='write the change')

    def handle(self, *args, **opts):
        start, end = _day(opts['start']), _day(opts['end'])
        if end < start:
            raise CommandError('--end is before --start')
        lo = datetime.combine(start, time.min, tzinfo=ET)
        hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=ET)

        if opts['cdr'] == '-':
            if sys.stdin is None or sys.stdin.isatty():
                raise CommandError('pipe the CDR in: ... < /var/log/asterisk/cdr-csv/Master.csv')
            rows = list(csv.reader(sys.stdin))
        else:
            with open(opts['cdr'], newline='') as fh:
                rows = list(csv.reader(fh))

        answered = []
        for r in rows:
            if len(r) <= DISPOSITION:
                continue
            if r[DISPOSITION] != 'ANSWERED' or r[LASTAPP] != 'Dial' or r[BILLSEC].strip() != '0':
                continue
            began, picked = _utc(r[START]), _utc(r[ANSWER])
            if began is None or picked is None or not (lo <= began < hi):
                continue
            answered.append((began, picked, _utc(r[END]), _digits(r[SRC])))

        found, missing = [], 0
        for began, picked, ended, caller in answered:
            row = (CallLog.objects
                   .filter(created_at__gte=began - timedelta(seconds=MATCH_SECONDS),
                           created_at__lte=began + timedelta(seconds=MATCH_SECONDS),
                           caller_number__endswith=caller,
                           status=CallLog.Status.NO_ANSWER,
                           block_reason=NOT_CONNECTED_LABEL)
                   .exclude(destination_number='')
                   .select_related('campaign').order_by('created_at').first())
            if row is None:
                missing += 1
                continue
            found.append((row, began, picked, ended))

        converts = []
        for row, began, picked, _ in found:
            min_dur = getattr(row.campaign, 'min_call_duration', 0) if row.campaign else 0
            flag = '  converts at 1s: money left for review' if 1 >= (min_dur or 0) else ''
            if flag:
                converts.append(row)
            self.stdout.write('{}  {:<14} -> {:<14} rang {:>3}s{}'.format(
                began.astimezone(ET).strftime('%Y-%m-%d %I:%M:%S %p'),
                row.caller_number, row.destination_number,
                int((picked - began).total_seconds()), flag))

        self.stdout.write(f'\n{len(answered)} calls the switch logged as answered with 0 seconds, '
                          f'{start} to {end} ET')
        self.stdout.write(f'{len(found)} of them are Not Connected in the call log'
                          + (f'; {missing} already fine or not found' if missing else ''))
        if converts:
            self.stdout.write(self.style.WARNING(
                f'{len(converts)} are on campaigns that convert at 1 second; their revenue, '
                f'payout and charge are NOT added by this command'))

        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to restore them.')
            return

        from routing.signals import mirror_call_log
        for row, began, picked, ended in found:
            with transaction.atomic():
                CallLog.objects.filter(pk=row.pk, status=CallLog.Status.NO_ANSWER).update(
                    status=CallLog.Status.COMPLETED,
                    duration=1,
                    block_reason='',
                    answered_at=picked,
                    ring_seconds=max(0, int((picked - began).total_seconds())),
                )
            mirror_call_log(row.id)
        self.stdout.write(self.style.SUCCESS(f'Restored {len(found)} calls to Completed, 1 second.'))

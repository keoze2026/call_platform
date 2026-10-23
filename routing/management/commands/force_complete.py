"""CH-226: mark a missed call Completed because Revo shows it completed.

Some calls are answered on Revo's side but Revo never sends the answer back to
us (18032426735, 19368279603, 17089457151 on 23 Oct): our server sees only the
caller hanging up, exactly like a call nobody answered, so no rule can find them
on our side. Sir (23 Oct): "if it is complete on Revo it must be complete on our
end". This takes the callers Sir names, with Revo's duration, and makes that
call Completed - paid and charged like any answered call.

    docker compose exec -T web python manage.py force_complete --caller 18032426735:2,19368279603:1
    ... same with --apply to write it.

caller:seconds = Revo's DURATION for that call (1 when left out). The latest
missed call from that caller on the day (Eastern, default today) is the one
changed; others from the same caller that day are listed, not touched.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from routing.models import CallLog

ET = ZoneInfo('America/New_York')


def _pairs(raw: str):
    out = []
    for part in (raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        number, _, secs = part.partition(':')
        digits = ''.join(ch for ch in number if ch.isdigit())[-10:]
        if len(digits) != 10:
            raise CommandError(f'not a phone number: {number}')
        try:
            seconds = int(secs) if secs else 1
        except ValueError:
            raise CommandError(f'not a number of seconds: {secs}')
        if seconds < 1:
            raise CommandError('seconds must be 1 or more')
        out.append((digits, seconds))
    if not out:
        raise CommandError('give --caller, e.g. --caller 18032426735:2')
    return out


class Command(BaseCommand):
    help = "Mark a caller's missed call Completed with Revo's duration (CH-226)."

    def add_arguments(self, parser):
        parser.add_argument('--caller', required=True,
                            help='caller:seconds, comma separated, e.g. 18032426735:2,19368279603:1')
        parser.add_argument('--date', default='', help='Eastern day YYYY-MM-DD (default today)')
        parser.add_argument('--apply', action='store_true', help='write the change')

    def handle(self, *args, **opts):
        from routing.asterisk_handler import settle_call_money

        try:
            day = date.fromisoformat(opts['date']) if opts['date'] else timezone.now().astimezone(ET).date()
        except ValueError:
            raise CommandError(f'not a date: {opts["date"]} (use YYYY-MM-DD)')
        lo = datetime.combine(day, time.min, tzinfo=ET)
        hi = lo + timedelta(days=1)

        done = 0
        for digits, seconds in _pairs(opts['caller']):
            missed = list(CallLog.objects
                          .filter(caller_number__endswith=digits, created_at__gte=lo, created_at__lt=hi,
                                  status=CallLog.Status.NO_ANSWER, retry_hidden=False)
                          .exclude(destination_number='')
                          .select_related('campaign', 'organization', 'buyer', 'publisher')
                          .order_by('-created_at'))
            if not missed:
                self.stdout.write(f'{digits}: no missed call on {day} - nothing to change')
                continue
            call = missed[0]
            when = call.created_at.astimezone(ET).strftime('%I:%M:%S %p')
            line = (f'{digits}: {when} ET -> {call.destination_number}  '
                    f'{call.status} {call.block_reason!r} -> Completed, {seconds}s')
            for other in missed[1:]:
                self.stdout.write(f'   (also missed at {other.created_at.astimezone(ET):%I:%M:%S %p} ET - not touched)')
            if not opts['apply']:
                self.stdout.write(line + '  [dry run]')
                continue
            ended = call.ended_at or timezone.now()
            call.status = CallLog.Status.COMPLETED
            call.duration = seconds
            call.block_reason = ''
            call.answered_at = ended - timedelta(seconds=seconds)
            if call.ring_seconds is None:
                call.ring_seconds = max(0, int((call.answered_at - call.created_at).total_seconds()))
            settle_call_money(call, True, seconds)
            try:
                from tasks import retire_used_number
                retire_used_number.delay(call.called_number, str(call.id))
            except Exception:
                pass
            done += 1
            self.stdout.write(line + '  done')
        if opts['apply']:
            self.stdout.write(self.style.SUCCESS(f'{done} calls marked Completed.'))
        else:
            self.stdout.write('Dry run. Add --apply to mark them.')

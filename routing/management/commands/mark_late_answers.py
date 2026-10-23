"""CH-225: count buyer answers that reached us just after the caller hung up.

Reads the lines docs/asterisk/late_answers.py prints from the SIP capture, on
stdin, and marks each matching missed call Completed, 1 second - as Revo shows
the same call. Run by cron on the host every 2 minutes:

    python3 /root/late_answers.py /root/sipcap \\
      | docker compose exec -T web python manage.py mark_late_answers --apply

Without --apply it only lists what it would change. A call that is already
counted is skipped, so running it again changes nothing.
"""
import json
import sys

from django.core.management.base import BaseCommand

from routing.late_answer import find_call, mark


class Command(BaseCommand):
    help = 'Count buyer answers that crossed the caller hanging up (CH-225).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='write the change')

    def handle(self, *args, **opts):
        seen = found = marked = 0
        for line in sys.stdin:
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            seen += 1
            call = find_call(event)
            if call is None:
                continue
            if call.status != 'no_answer':
                continue  # already counted, or answered the normal way
            found += 1
            label = (f'{call.created_at:%Y-%m-%d %H:%M:%S} UTC  {call.caller_number} -> '
                     f'{call.destination_number}  answer {event["answer_at"] - event.get("cancel_at", event["answer_at"]):+.3f}s '
                     f'after the cancel')
            if call.retry_hidden:
                self.stdout.write(f'{label}  (an earlier try of a grouped call - left alone)')
                continue
            if opts['apply']:
                if mark(call, event):
                    marked += 1
                    self.stdout.write(f'{label}  -> Completed, 1s')
            else:
                self.stdout.write(f'{label}  would be Completed, 1s')
        tail = f'{marked} marked' if opts['apply'] else 'dry run, add --apply to mark them'
        self.stdout.write(f'{seen} late answers in the capture, {found} still missed in the log; {tail}')

"""Point calls at the MP3 copy of their recording (CH-281).

Recordings are saved by Asterisk as WAV, about 1 MB a minute: 63 GB by
26 Oct and 58 GB more every month on a 200 GB disk. The host script
scripts/recordings_to_mp3.sh converts each finished recording to MP3 (about
five times smaller), checks the MP3, then runs this with the call ids it
converted. Only after this succeeds does it delete the WAV.

    docker compose exec -T web python manage.py recordings_to_mp3 <call-id> [<call-id> ...]

Each call whose link ends in "<call-id>.wav" (CallLog and the CallRecord copy
the reports and exports read) now ends in "<call-id>.mp3". Prints one line per
id: "ok <id> <rows>" or "none <id>" (no call holds that link, e.g. a test call
or a row cleared earlier). Changes nothing else. --dry-run reports only.
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from analytics.models import CallRecord
from routing.models import CallLog

CALL_ID = re.compile(r'^[0-9a-fA-F-]{8,64}$')


class Command(BaseCommand):
    help = 'Switch recording links from <id>.wav to <id>.mp3 for the given call ids.'

    def add_arguments(self, parser):
        parser.add_argument('ids', nargs='+', help='call ids whose recording is now an MP3')
        parser.add_argument('--dry-run', action='store_true', help='report only, change nothing')

    def handle(self, *args, **o):
        bad = [i for i in o['ids'] if not CALL_ID.match(i)]
        if bad:
            # Never build a database filter from something that is not a call id.
            self.stderr.write(f'not a call id: {", ".join(bad[:5])}')
        done = 0
        for call_id in (i for i in o['ids'] if CALL_ID.match(i)):
            wav = f'{call_id}.wav'
            rows = 0
            with transaction.atomic():
                for model in (CallLog, CallRecord):
                    for pk, url in model.objects.filter(recording_url__endswith=wav).values_list('pk', 'recording_url'):
                        rows += 1
                        if not o['dry_run']:
                            model.objects.filter(pk=pk).update(recording_url=url[: -len('.wav')] + '.mp3')
            self.stdout.write(f'{"ok" if rows else "none"} {call_id} {rows}')
            done += bool(rows)
        self.stderr.write(f'{done} calls now point at their MP3' + (' (dry run)' if o['dry_run'] else ''))

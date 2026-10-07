"""Rebuild recording links from the files that are already on disk.

Asterisk sends the recording URL truncated at the first colon, so 334 calls
stored the literal string `https` and every one of them produced a dead link.
The audio was never lost: Asterisk writes <call-id>.wav, so the file name is
the call id and every broken link can be rebuilt from the file beside it.

    python manage.py repair_recordings --dry-run
    python manage.py repair_recordings
    python manage.py repair_recordings --dir /var/spool/asterisk/recordings

Runs on the HOST or in a container that can see the recordings directory.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import CallLog
from routing.recordings import is_recording_link

DEFAULT_DIR = '/var/spool/asterisk/recordings'


class Command(BaseCommand):
    help = 'Rebuild recording_url from the .wav files on disk.'

    def add_arguments(self, parser):
        parser.add_argument('--dir', default=DEFAULT_DIR)
        parser.add_argument(
            '--from-list', default='',
            help=(
                'A text file of recording file names, one per line. The audio '
                'lives on the host and this runs in a container, so listing '
                'the folder into a file the container can see is simpler than '
                'remounting anything: '
                'ls /var/spool/asterisk/recordings > /opt/call_platform/recordings.txt'
            ),
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--base', default='',
            help='URL the files are served from. Defaults to FRONTEND_URL/recordings.',
        )

    def handle(self, *args, **o):
        listing = o['from_list']
        folder = o['dir']

        if listing:
            if not os.path.isfile(listing):
                self.stderr.write(self.style.ERROR(f'{listing} does not exist.'))
                return
            with open(listing) as fh:
                names = [line.strip() for line in fh if line.strip()]
        elif os.path.isdir(folder):
            names = os.listdir(folder)
        else:
            self.stderr.write(self.style.ERROR(
                f'{folder} is not readable from here, and no --from-list was given.\n'
                f'The recordings are on the host and this runs in a container. '
                f'Simplest route:\n'
                f'  ls {folder} > /opt/call_platform/recordings.txt\n'
                f'  docker compose exec -T web python manage.py repair_recordings '
                f'--from-list /app/recordings.txt'
            ))
            return

        base = (o['base'] or f"{settings.FRONTEND_URL.rstrip('/')}/recordings").rstrip('/')

        # The file name is the call id, so one listing answers every row.
        on_disk = {
            os.path.splitext(f)[0]: f
            for f in names
            if f.lower().endswith(('.wav', '.mp3'))
        }
        self.stdout.write(f'{len(on_disk)} recording files found')

        fixed = cleared = kept = missing = 0
        for call in CallLog.objects.exclude(recording_url='').only('id', 'recording_url').iterator(chunk_size=500):
            current = call.recording_url or ''
            if is_recording_link(current):
                kept += 1
                continue

            filename = on_disk.get(str(call.id))
            if filename:
                new = f'{base}/{filename}'
                if not o['dry_run']:
                    CallLog.objects.filter(pk=call.pk).update(recording_url=new)
                fixed += 1
            else:
                # The link was broken and the audio is gone. An empty value
                # reads as "no recording", which is true; leaving `https` there
                # keeps producing a 404 for ever.
                if not o['dry_run']:
                    CallLog.objects.filter(pk=call.pk).update(recording_url='')
                cleared += 1
                missing += 1

        verb = 'would be' if o['dry_run'] else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{fixed} {verb} repaired from a file on disk'))
        self.stdout.write(f'{cleared} {verb} cleared (broken link, no file)')
        self.stdout.write(f'{kept} already had a usable link')
        if o['dry_run']:
            self.stdout.write(self.style.WARNING('dry run - nothing was written'))

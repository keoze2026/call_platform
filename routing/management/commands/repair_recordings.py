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
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand

from analytics.models import CallRecord
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
                # The call log is fine but the mirror may still carry the old
                # broken copy, and the mirror is what the export reads.
                if not o['dry_run']:
                    CallRecord.objects.filter(pk=call.pk).exclude(
                        recording_url=current
                    ).update(recording_url=current)
                continue

            filename = on_disk.get(str(call.id))
            if filename:
                new = f'{base}/{filename}'
                if not o['dry_run']:
                    CallLog.objects.filter(pk=call.pk).update(recording_url=new)
                    # The reports and the export read the mirror, which holds
                    # its own copy taken when the call ended. Repairing only
                    # CallLog left every exported Recording cell empty - the
                    # fix was real and invisible.
                    CallRecord.objects.filter(pk=call.pk).update(recording_url=new)
                fixed += 1
            else:
                # The link was broken and the audio is gone. An empty value
                # reads as "no recording", which is true; leaving `https` there
                # keeps producing a 404 for ever.
                if not o['dry_run']:
                    CallLog.objects.filter(pk=call.pk).update(recording_url='')
                    CallRecord.objects.filter(pk=call.pk).update(recording_url='')
                cleared += 1
                missing += 1

        # A call whose link the hangup handler refused and dropped stored an
        # empty string, so the loop above - which walks rows that still hold a
        # value - never sees it, even with the .wav sitting on disk. Every call
        # since `Stop storing broken recording links` shipped is in that state.
        # The file name is the call id, so the blanks can be filled directly.
        recovered = 0
        ids = []
        for key in on_disk:
            try:
                ids.append(uuid.UUID(key))
            except ValueError:
                continue   # a file whose name is not a call id

        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            blanks = CallLog.objects.filter(
                pk__in=chunk, recording_url='',
            ).values_list('pk', flat=True)
            for pk in blanks:
                new = f'{base}/{on_disk[str(pk)]}'
                if not o['dry_run']:
                    CallLog.objects.filter(pk=pk).update(recording_url=new)
                    CallRecord.objects.filter(pk=pk).update(recording_url=new)
                recovered += 1

        # The main loop walks CallLog rows that still have a value. An earlier
        # run that cleared a row leaves the loop blind to it while its mirror
        # copy still holds the broken link, so the mirror is reconciled against
        # the call log directly.
        stale = 0
        mirror = CallRecord.objects.exclude(recording_url='').only('id', 'recording_url')
        for rec in mirror.iterator(chunk_size=500):
            if is_recording_link(rec.recording_url or ''):
                continue
            truth = CallLog.objects.filter(pk=rec.pk).values_list(
                'recording_url', flat=True,
            ).first() or ''
            if not is_recording_link(truth):
                truth = ''
            if not o['dry_run']:
                CallRecord.objects.filter(pk=rec.pk).update(recording_url=truth)
            stale += 1

        verb = 'would be' if o['dry_run'] else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{fixed} {verb} repaired from a file on disk'))
        self.stdout.write(f'{cleared} {verb} cleared (broken link, no file)')
        self.stdout.write(self.style.SUCCESS(
            f'{recovered} {verb} recovered (link was dropped, file on disk)'))
        self.stdout.write(f'{kept} already had a usable link')
        self.stdout.write(f'{stale} stale mirror rows {verb} reconciled')
        if o['dry_run']:
            self.stdout.write(self.style.WARNING('dry run - nothing was written'))

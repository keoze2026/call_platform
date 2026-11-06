"""CH-635 (customer accounts point 8): where in B2 each recording file goes.

    python manage.py recording_folders <file> [<file> ...]

One line per file: "<file> <folder>", the folder "-" for the top of
recordings/ (our account), "<account id>/" for a customer account. Read only.
Server A's copier asks this before it uploads (scripts/recordings_to_b2.py,
through /usr/local/bin/avx-from-a).
"""
from django.core.management.base import BaseCommand

from routing.recording_links import folder_of_file


class Command(BaseCommand):
    help = 'The B2 folder of each recording file (customer accounts point 8).'

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+')

    def handle(self, *args, **o):
        for name in o['files']:
            self.stdout.write(f'{name} {folder_of_file(name) or "-"}')

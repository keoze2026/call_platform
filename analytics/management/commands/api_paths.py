"""CH-355: print, or write to docs/api-paths.txt, every API path the server answers."""
from django.core.management.base import BaseCommand

from analytics import api_paths


class Command(BaseCommand):
    help = 'Every API path the server answers (Avortyx Guard contract check).'

    def add_arguments(self, parser):
        parser.add_argument('--write', action='store_true', help='update docs/api-paths.txt')

    def handle(self, *args, **opts):
        text = api_paths.text()
        if opts['write']:
            api_paths.FILE.parent.mkdir(exist_ok=True)
            api_paths.FILE.write_text(text)
            self.stdout.write(f'Wrote {len(text.splitlines()) - 2} paths to {api_paths.FILE}')
        else:
            self.stdout.write(text)

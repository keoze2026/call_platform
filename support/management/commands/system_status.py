"""CH-314: every service with its status, as the bot's /status shows it.

    python manage.py system_status              # print it here, send nothing
    python manage.py system_status --send       # post it to the status chat
    python manage.py system_status --watch      # the 15-minute warnings, once
"""
import re

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Every service with its status (CH-314).'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true', help='post it to the status chat')
        parser.add_argument('--watch', action='store_true', help='send the automatic warnings now')

    def handle(self, *args, **o):
        from support import system_status as S
        if o['watch']:
            self.stdout.write(f'{S.watch(S.post)} warning(s) sent')
            return
        text = S.render(S.collect())
        if o['send']:
            self.stdout.write('sent' if S.post(text) else 'NOT sent (no bot token or chat)')
            return
        self.stdout.write(re.sub(r'<[^>]+>', '', text))

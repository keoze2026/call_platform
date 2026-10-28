"""CH-354: Avortyx Guard's numbers checker, by hand. Read only.

    docker compose exec -T web python manage.py guard_check
    docker compose exec -T web python manage.py guard_check --day 2026-10-28 --tabs
    docker compose exec -T web python manage.py guard_check --send

See analytics/guard.py for what each check compares.
"""
import re

from django.core.management.base import BaseCommand

from analytics import guard


class Command(BaseCommand):
    help = "Compare the platform's own numbers with each other (CH-354). Changes nothing."

    def add_arguments(self, parser):
        parser.add_argument('--day', help='Eastern day, YYYY-MM-DD (default today so far)')
        parser.add_argument('--tabs', action='store_true', help='also check the summary tabs (slower)')
        parser.add_argument('--send', action='store_true', help='also send the result to Telegram')

    def handle(self, *args, **opts):
        day, _, _ = guard.et_day(guard.parse_day(opts['day']))
        findings = guard.run(day, tabs=opts['tabs'])
        text = guard.render(day, findings)
        self.stdout.write(re.sub(r'</?b>', '', text).replace('&amp;', '&'))
        if opts['send']:
            from support.system_status import post
            chat, thread = guard.guard_chat()
            ok = post(text, chat=chat, thread=thread)
            self.stdout.write('Sent to Telegram.' if ok else 'Not sent: no bot token or chat.')

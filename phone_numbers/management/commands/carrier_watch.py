"""CH-360: the carrier watch by hand.

    docker compose exec -T web python manage.py carrier_watch            # each carrier's state
    docker compose exec -T web python manage.py carrier_watch --run      # settle / start tests now
    docker compose exec -T web python manage.py carrier_watch --run --send   # and alert on Telegram
"""
from django.core.management.base import BaseCommand

from phone_numbers import carrier_watch as W


class Command(BaseCommand):
    help = 'Is each carrier still delivering our TFNs? (CH-360)'

    def add_arguments(self, parser):
        parser.add_argument('--run', action='store_true', help='settle finished tests and start due ones now')
        parser.add_argument('--send', action='store_true', help='with --run: send alerts to Telegram')

    def handle(self, *args, **o):
        from phone_numbers.models import Carrier
        if o['run']:
            if o['send']:
                from analytics import guard
                from support.system_status import post
                chat, thread = guard.guard_chat()
                send = lambda t: post(t, chat=chat, thread=thread)  # noqa: E731
            else:
                send = lambda t: self.stdout.write('ALERT: ' + t)  # noqa: E731
            for code, what in W.tick(send).items():
                self.stdout.write(f'{code}: {what}')
        self.stdout.write(f'watch {"on" if W.enabled() else "OFF"}, every {int(W.every().total_seconds() // 60)} min, '
                          f'7:30 AM - 9 PM ET')
        for c in Carrier.objects.filter(is_active=True).order_by('code'):
            s = W.state(c.id)
            self.stdout.write(f'{c.code:<8} {(s.get("status") or "not checked yet"):<15} '
                              f'{s.get("number", ""):<14} {s.get("detail", "")}  (at {s.get("at", "-")})')

"""Our own reports bot (CH-347): check it, point Telegram at our server, send.

    python manage.py team_report_bot --check
        the bot's name and where Telegram sends its updates
    python manage.py team_report_bot --set-webhook https://<backend>/api/team-report-bot/webhook
        tell Telegram to send the bot's updates (/start, /stop) to our server,
        with the secret our webhook checks
    python manage.py team_report_bot --list
        the connected chats
    python manage.py team_report_bot --send-test <chat id>
        today so far, to one connected chat, now

The token is read from REPORTS_BOT_TOKEN in .env and never printed.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Check and set up the team reports bot.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')
        parser.add_argument('--set-webhook', default='')
        parser.add_argument('--list', action='store_true')
        parser.add_argument('--send-test', default='')

    def handle(self, *args, **o):
        from analytics import team_report as T
        from analytics.models import TeamReportChat
        if not T.configured():
            self.stderr.write(self.style.ERROR('REPORTS_BOT_TOKEN is not set (or the containers were not '
                                               're-created after adding it to .env).'))
            return
        if o['check']:
            me = T.telegram('getMe', {}).json().get('result', {})
            info = T.telegram('getWebhookInfo', {}).json().get('result', {})
            self.stdout.write(f"bot: @{me.get('username')} ({me.get('first_name')})")
            self.stdout.write(f"webhook: {info.get('url') or '(not set)'}")
            self.stdout.write(f"pending updates: {info.get('pending_update_count', 0)}")
            if info.get('last_error_message'):
                self.stdout.write(self.style.WARNING(f"last error: {info.get('last_error_message')}"))
        if o['set_webhook']:
            url = o['set_webhook'].strip()
            if not url.startswith('https://'):
                self.stderr.write(self.style.ERROR('The webhook must be an https:// address.'))
                return
            T.telegram('setWebhook', {'url': url, 'secret_token': T.webhook_secret(),
                                      'allowed_updates': '["message","my_chat_member"]',
                                      'drop_pending_updates': 'true'})
            self.stdout.write(self.style.SUCCESS(f'webhook set: {url}'))
        if o['list']:
            for c in TeamReportChat.objects.select_related('organization'):
                self.stdout.write(f"{c.chat_id} | {c.title} | {c.organization.name} | "
                                  f"{'on' if c.enabled else 'off'} | {c.send_time:%H:%M} | {c.last_result}")
        if o['send_test']:
            c = TeamReportChat.objects.select_related('organization').filter(chat_id=o['send_test']).first()
            if c is None:
                self.stderr.write(self.style.ERROR('No connected chat with that ID (see --list).'))
                return
            self.stdout.write(T.send(c, test=True))

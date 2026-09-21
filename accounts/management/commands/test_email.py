"""Send a test email, to prove SMTP works before blaming the invite flow.

    python manage.py test_email --to someone@example.com
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.emails import send_account_email


class Command(BaseCommand):
    help = "Send a test email and report the result"

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True, help='Recipient address')

    def handle(self, *args, **options):
        self.stdout.write(f"host      : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        self.stdout.write(f"tls / ssl : {settings.EMAIL_USE_TLS} / {settings.EMAIL_USE_SSL}")
        self.stdout.write(f"user      : {settings.EMAIL_HOST_USER or '(not set)'}")
        self.stdout.write(f"password  : {'set' if settings.EMAIL_HOST_PASSWORD else '(NOT SET)'}")
        self.stdout.write(f"from      : {getattr(settings, 'INVITE_FROM_EMAIL', settings.DEFAULT_FROM_EMAIL)}")
        self.stdout.write(f"frontend  : {getattr(settings, 'FRONTEND_URL', '(not set)')}")
        self.stdout.write('-' * 55)

        sent, error = send_account_email(
            options['to'],
            'Avortyx test email',
            'This is a test from the Avortyx platform. If you are reading it, '
            'outgoing mail is working.',
        )

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Delivered to {options['to']}"))
        else:
            raise CommandError(f"Not delivered: {error}")

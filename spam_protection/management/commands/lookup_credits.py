"""Show how many RealValidito lookups are left, and test the credentials.

Run this before switching DNC checking on. A wrong key and an empty balance both
look like "the check did nothing" from the outside, and this tells them apart.

    python manage.py lookup_credits
    python manage.py lookup_credits --number 4705551234
"""
from django.core.management.base import BaseCommand

from spam_protection.realvalidito import DNCLookup, PhoneLookup, normalise


class Command(BaseCommand):
    help = "Show remaining RealValidito credits, and optionally test a number"

    def add_arguments(self, parser):
        parser.add_argument('--number', help='Look this number up as a live test')

    def handle(self, *args, **options):
        from django.conf import settings

        key = getattr(settings, 'REALVALIDITO_API_KEY', '')
        if not key:
            self.stdout.write(self.style.ERROR(
                'REALVALIDITO_API_KEY is not set - no lookup can run.'
            ))
            return

        self.stdout.write(f'key       : {key[:8]}...{key[-4:]}')
        self.stdout.write(f'phone     : {PhoneLookup.credits()} lookups remaining')
        self.stdout.write(f'DNC       : {DNCLookup.credits()} lookups remaining')
        self.stdout.write(
            f"DNC check : {'on' if getattr(settings, 'DNC_CHECK_ENABLED', False) else 'off'}"
            f"   blocking: {'on' if getattr(settings, 'DNC_BLOCK_LISTED', False) else 'off'}"
        )

        number = options.get('number')
        if not number:
            return

        digits = normalise(number)
        self.stdout.write(f'\n--- {digits} ---')
        p = PhoneLookup.lookup(number)
        if p:
            for k in ('status', 'number_type', 'city', 'state', 'zip',
                      'timezone', 'network_name', 'network_type'):
                self.stdout.write(f'  {k:<13} {p.get(k, "")}')
        else:
            self.stdout.write('  phone lookup returned nothing')

        d = DNCLookup.check(number)
        if d['checked']:
            self.stdout.write(
                f"  DNC           {'LISTED - ' + d['reason'] if d['listed'] else 'clean'}"
            )
        else:
            self.stdout.write('  DNC           not checked (disabled, no credits, or unavailable)')

"""Let the callers the DNC check blacklisted back through.

`spam_protection/dnc.py` added every caller found on a do-not-call register to
the org-wide blacklist, so they were refused on their next call. 161 calls were
refused that way on 2026-10-07. These are inbound calls - the register governs
who you may call, not who may call you - and at the client's cost per call that
is thousands of dollars of bought traffic turned away each day.

Only entries the check added itself (reason `auto_detected`) are touched.
A number somebody blocked by hand stays blocked.

    python manage.py unblock_dnc --dry-run
    python manage.py unblock_dnc
"""
from django.core.management.base import BaseCommand

from spam_protection.models import Blacklist


class Command(BaseCommand):
    help = 'Deactivate the blacklist entries the DNC check added.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **o):
        auto = Blacklist.objects.filter(
            reason=Blacklist.Reason.AUTO, is_active=True,
        )
        manual = Blacklist.objects.filter(is_active=True).exclude(
            reason=Blacklist.Reason.AUTO,
        )

        self.stdout.write(f'{auto.count()} active entries added by the DNC check')
        self.stdout.write(f'{manual.count()} active entries added by a person, left alone')

        for entry in auto.order_by('-created_at')[:10]:
            self.stdout.write(f'  {entry.phone_number}  added {entry.created_at:%Y-%m-%d %H:%M}')
        if auto.count() > 10:
            self.stdout.write(f'  ... and {auto.count() - 10} more')

        if o['dry_run']:
            self.stdout.write(self.style.WARNING('dry run - nothing was written'))
            return

        # Deactivated rather than deleted: the record of what the register said
        # is the evidence that a publisher was sending listed callers.
        n = auto.update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f'{n} deactivated - those callers now get through'))
        self.stdout.write(
            'New ones will not be added unless DNC_BLOCK_LISTED is set true in '
            '/opt/call_platform/.env. The flag on the call is still recorded.')

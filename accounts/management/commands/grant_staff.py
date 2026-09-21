"""Grant or revoke platform-staff access.

Platform staff can use the endpoints that act across every organization —
listing, approving and rejecting access requests. Organization admin is
deliberately not enough: the first user of any organization is an admin of it,
so that role gates nothing platform-wide.

    python manage.py grant_staff --list
    python manage.py grant_staff --email admin@avortyx.io
    python manage.py grant_staff --email someone@example.com --revoke
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Grant or revoke platform staff access"

    def add_arguments(self, parser):
        parser.add_argument('--email', help='User to change')
        parser.add_argument('--revoke', action='store_true', help='Remove staff access')
        parser.add_argument('--list', action='store_true', help='Show who has it')

    def handle(self, *args, **options):
        if options['list'] or not options['email']:
            staff = User.objects.filter(is_staff=True) | User.objects.filter(is_superuser=True)
            staff = staff.distinct().select_related('organization')

            if not staff.exists():
                self.stdout.write(self.style.ERROR(
                    'No platform staff exist. Nobody can approve access requests.\n'
                    'Grant it with: python manage.py grant_staff --email <address>'
                ))
            else:
                self.stdout.write(f'platform staff ({staff.count()}):')
                for u in staff:
                    flags = []
                    if u.is_superuser:
                        flags.append('superuser')
                    if u.is_staff:
                        flags.append('staff')
                    org = u.organization.name if u.organization_id else '(no org)'
                    self.stdout.write(f'  {u.email:<34} {org:<16} {", ".join(flags)}')

            if not options['email']:
                return

        try:
            user = User.objects.get(email__iexact=options['email'])
        except User.DoesNotExist:
            raise CommandError(f"No user with email {options['email']}")

        if options['revoke']:
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=['is_staff', 'is_superuser'])
            self.stdout.write(self.style.SUCCESS(f'Revoked platform staff from {user.email}'))
        else:
            user.is_staff = True
            user.save(update_fields=['is_staff'])
            self.stdout.write(self.style.SUCCESS(f'Granted platform staff to {user.email}'))

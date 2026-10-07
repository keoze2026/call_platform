"""Add or update a carrier and its code.

The codes belong to the people running the platform, not to the source, so
they are rows in a table and this command takes them as arguments. Nothing is
baked in: adding a carrier next month is this command or the API, never an
edit and a deploy.

    python manage.py add_carrier --name Freedom --code KMQ
    python manage.py add_carrier --name XOLO --code HYU
    python manage.py add_carrier --list
"""
from django.core.management.base import BaseCommand

from accounts.models import Organization
from phone_numbers.models import Carrier


class Command(BaseCommand):
    help = 'Add or update a carrier and the code shown on the Numbers page.'

    def add_arguments(self, parser):
        parser.add_argument('--name', default='')
        parser.add_argument('--code', default='')
        parser.add_argument('--org', default='',
                            help='Organization name; the only one is used when omitted')
        parser.add_argument('--list', action='store_true')

    def handle(self, *args, **o):
        if o['org']:
            org = Organization.objects.filter(name=o['org']).first()
            if not org:
                self.stderr.write(self.style.ERROR(f'No organization named {o["org"]}'))
                return
        else:
            orgs = list(Organization.objects.all()[:5])
            if len(orgs) != 1:
                self.stderr.write(self.style.ERROR(
                    'More than one organization exists, so --org is required: '
                    + ', '.join(o.name for o in orgs)))
                return
            org = orgs[0]

        if o['list'] or not (o['name'] and o['code']):
            rows = Carrier.objects.filter(organization=org)
            self.stdout.write(f'{org.name}: {rows.count()} carriers')
            for c in rows:
                state = '' if c.is_active else '  (inactive)'
                self.stdout.write(f'  {c.code:<6} {c.name}  {c.phone_numbers.count()} numbers{state}')
            if not o['list']:
                self.stderr.write(self.style.ERROR('Give both --name and --code to add one.'))
            return

        code = o['code'].strip().upper()
        carrier, created = Carrier.objects.update_or_create(
            organization=org, code=code,
            defaults={'name': o['name'].strip(), 'is_active': True},
        )
        verb = 'added' if created else 'updated'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {carrier.name} ({carrier.code})'))

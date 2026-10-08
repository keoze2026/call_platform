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
from phone_numbers.models import Carrier, PhoneNumber


class Command(BaseCommand):
    help = 'Add or update a carrier and the code shown on the Numbers page.'

    def add_arguments(self, parser):
        parser.add_argument('--name', default='')
        parser.add_argument('--code', default='')
        parser.add_argument('--org', default='',
                            help='Organization name; the only one is used when omitted')
        parser.add_argument('--single-use', action='store_true',
                            help='Retire a number once a call has used it')
        parser.add_argument('--lifetime-hours', default=0, type=int,
                            help='Hours a number stays usable; 0 means no limit')
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
                terms = []
                if c.single_use:
                    terms.append('single use')
                if c.lifetime_hours:
                    terms.append(f'{c.lifetime_hours}h')
                shown = ('  ' + ', '.join(terms)) if terms else ''
                self.stdout.write(
                    f'  {c.code:<6} {c.name}  {c.phone_numbers.count()} numbers'
                    f'{shown}{state}')
            if not o['list']:
                self.stderr.write(self.style.ERROR('Give both --name and --code to add one.'))
            return

        code = o['code'].strip().upper()
        carrier, created = Carrier.objects.update_or_create(
            organization=org, code=code,
            defaults={
                'name': o['name'].strip(),
                'is_active': True,
                'single_use': o['single_use'],
                'lifetime_hours': o['lifetime_hours'],
            },
        )
        verb = 'added' if created else 'updated'
        # Terms set after numbers were already taken on have to reach those
        # numbers too, or a carrier switched to 24 hours leaves everything
        # imported before it running for ever.
        stamped = 0
        if carrier.lifetime_hours:
            from phone_numbers.lifecycle import stamp_lifetime
            for pn in carrier.phone_numbers.filter(
                expires_at__isnull=True, status=PhoneNumber.Status.ACTIVE,
            ):
                stamp_lifetime(pn)
                if pn.expires_at:
                    pn.save(update_fields=['expires_at', 'updated_at'])
                    stamped += 1

        terms = []
        if carrier.single_use:
            terms.append('retired once used')
        if carrier.lifetime_hours:
            terms.append(f'expires after {carrier.lifetime_hours}h')
        shown = (' - ' + ', '.join(terms)) if terms else ''
        self.stdout.write(self.style.SUCCESS(
            f'{verb}: {carrier.name} ({carrier.code}){shown}'))
        if stamped:
            self.stdout.write(
                f'{stamped} numbers already under this carrier now expire too')

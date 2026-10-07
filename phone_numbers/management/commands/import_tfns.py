"""Take on a batch of toll-frees handed over by a carrier.

A carrier sends a list of numbers in a message. They are not bought through
Twilio, so there is nothing to provision - they only have to exist on the
platform, under the right carrier, with that carrier's terms on them.

    python manage.py import_tfns --org Avortyx --carrier KMQ --numbers 18337417216,18337415406
    python manage.py import_tfns --org Avortyx --carrier KMQ --file /opt/call_platform/tfns.txt
    python manage.py import_tfns --org Avortyx --carrier KMQ --file /opt/call_platform/tfns.txt --campaign "23 JUNE"

The expiry comes from the carrier's own `lifetime_hours`, so a 24-hour carrier
needs saying once, on the carrier, not on every number.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Organization
from phone_numbers.lifecycle import stamp_lifetime
from phone_numbers.models import Carrier, PhoneNumber


def normalise(raw: str) -> str:
    digits = ''.join(c for c in raw if c.isdigit())
    if not digits:
        return ''
    if len(digits) == 10:
        digits = '1' + digits
    return '+' + digits


class Command(BaseCommand):
    help = 'Add toll-free numbers handed over by a carrier.'

    def add_arguments(self, parser):
        parser.add_argument('--org', required=True)
        parser.add_argument('--carrier', required=True, help='The carrier code, e.g. KMQ')
        parser.add_argument('--numbers', default='', help='Comma separated')
        parser.add_argument('--file', default='', help='One number per line')
        parser.add_argument('--campaign', default='', help='Put them on this campaign')
        parser.add_argument('--name', default='TFN Non DID')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **o):
        org = Organization.objects.filter(name=o['org']).first()
        if not org:
            self.stderr.write(self.style.ERROR(f'No organization named {o["org"]}'))
            return

        carrier = Carrier.objects.filter(
            organization=org, code=o['carrier'].strip().upper(),
        ).first()
        if not carrier:
            self.stderr.write(self.style.ERROR(
                f'No carrier with code {o["carrier"]}. Add it first:\n'
                f'  python manage.py add_carrier --org {o["org"]} '
                f'--name <name> --code {o["carrier"].upper()}'))
            return

        raw = []
        if o['numbers']:
            raw += o['numbers'].split(',')
        if o['file']:
            with open(o['file']) as fh:
                raw += fh.readlines()
        numbers = [n for n in (normalise(r) for r in raw) if n]
        if not numbers:
            self.stderr.write(self.style.ERROR('No numbers given. Use --numbers or --file.'))
            return

        campaign = None
        if o['campaign']:
            from campaigns.models import Campaign
            campaign = Campaign.objects.filter(
                organization=org, name=o['campaign'],
            ).first()
            if not campaign:
                self.stderr.write(self.style.ERROR(f'No campaign named {o["campaign"]}'))
                return

        self.stdout.write(
            f'{len(numbers)} numbers -> {carrier.name} ({carrier.code}), '
            f'single use: {"yes" if carrier.single_use else "no"}, '
            f'lifetime: {carrier.lifetime_hours or "no limit"}h')
        self.stdout.write('')

        added = existing = 0
        for number in numbers:
            already = PhoneNumber.objects.filter(number=number).first()
            if already:
                self.stdout.write(f'  {number}  already on the platform, skipped')
                existing += 1
                continue

            self.stdout.write(f'  {number}  added')
            if o['dry_run']:
                added += 1
                continue

            pn = PhoneNumber.objects.create(
                organization=org,
                number=number,
                friendly_name=o['name'],
                number_type=PhoneNumber.NumberType.TOLL_FREE,
                # Not bought through a provider; the carrier handed it over.
                vendor=PhoneNumber.Vendor.OTHER,
                # Marks it as carrier-supplied, which release_number already
                # reads to know there is nothing to hand back to Twilio.
                twilio_sid=f'TFN-{number.lstrip("+")}',
                country_code='US',
                carrier=carrier,
                campaign=campaign,
                assigned_at=timezone.now() if campaign else None,
                allocated_capacity=1,
                voice_enabled=True,
                sms_enabled=False,
                status=PhoneNumber.Status.ACTIVE,
            )
            stamp_lifetime(pn)
            if pn.expires_at:
                pn.save(update_fields=['expires_at', 'updated_at'])
            added += 1

        verb = 'would be' if o['dry_run'] else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{added} {verb} added'))
        self.stdout.write(f'{existing} already on the platform')
        if o['dry_run']:
            self.stdout.write(self.style.WARNING('dry run - nothing was written'))

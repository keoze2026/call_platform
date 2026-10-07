"""Set the Name on numbers from a template.

The toll-frees all read DID-3778, DID-3779 and so on, which says they are DIDs
when they are not. The name wanted is "TFN Non DID", and the carrier code and
the date the number was assigned belong beside it.

Nothing about the wording is fixed in here. The template is an argument, so a
different name next month is a different command line, not a code change.

    python manage.py rename_numbers --type toll_free --name "TFN Non DID" --dry-run
    python manage.py rename_numbers --type toll_free --name "TFN Non DID"
    python manage.py rename_numbers --type toll_free --name "TFN Non DID {code} {assigned}"

Placeholders: {code} the carrier code, {assigned} the assigned date,
{number} the full number, {last4} its last four digits.
"""
from django.core.management.base import BaseCommand

from phone_numbers.models import PhoneNumber


class Command(BaseCommand):
    help = 'Set friendly_name on numbers from a template.'

    def add_arguments(self, parser):
        # What the boss asked for: the name, the carrier code, the assigned
        # date. It is still an argument so the wording can change without a
        # deploy, but it does not have to be supplied to do the job asked.
        parser.add_argument('--name', default='TFN Non DID {code} {assigned}',
                            help='The name, with optional {code} {assigned} {number} {last4}')
        parser.add_argument('--type', default='toll_free',
                            help='Only numbers of this type: toll_free, local, mobile')
        parser.add_argument('--vendor', default='',
                            help='Only numbers from this vendor, e.g. Other')
        parser.add_argument('--unassigned-only', action='store_true',
                            help='Skip numbers already on a campaign')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **o):
        qs = PhoneNumber.objects.select_related('carrier', 'campaign')
        if o['type']:
            qs = qs.filter(number_type=o['type'])
        if o['vendor']:
            qs = qs.filter(vendor=o['vendor'])
        if o['unassigned_only']:
            qs = qs.filter(campaign__isnull=True)

        if not qs.exists():
            self.stderr.write(self.style.ERROR('No numbers matched those filters.'))
            return

        changed = same = 0
        for n in qs.order_by('number'):
            name = o['name'].format(
                code=n.carrier.code if n.carrier_id else '',
                assigned=n.assigned_at.strftime('%b %-d') if n.assigned_at else '',
                number=n.number,
                last4=n.number[-4:],
            )
            # A template with an empty placeholder leaves double spaces behind.
            name = ' '.join(name.split())

            if n.friendly_name == name:
                same += 1
                continue
            self.stdout.write(f'  {n.number}  {n.friendly_name or "(blank)"}  ->  {name}')
            if not o['dry_run']:
                PhoneNumber.objects.filter(pk=n.pk).update(friendly_name=name)
            changed += 1

        verb = 'would be' if o['dry_run'] else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{changed} {verb} renamed'))
        self.stdout.write(f'{same} already had that name')
        if o['dry_run']:
            self.stdout.write(self.style.WARNING('dry run - nothing was written'))

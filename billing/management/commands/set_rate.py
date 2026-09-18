"""View or change a client's per-minute call rate and markup.

    python manage.py set_rate --list
    python manage.py set_rate --org "Avortyx" --rate 0.45
    python manage.py set_rate --org "Avortyx" --rate 0.45 --markup 20
    python manage.py set_rate --org "Avortyx" --preview 90

Billing rounds up to the whole minute: a 90-second call bills as 2 minutes.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts.models import Organization
from billing.models import BillingAccount
from billing.services import BillingService


class Command(BaseCommand):
    help = "View or change a client's per-minute call rate and markup"

    def add_arguments(self, parser):
        parser.add_argument('--org', help='Organization name')
        parser.add_argument('--org-id', help='Organization UUID')
        parser.add_argument('--rate', help='New per-minute rate, e.g. 0.45')
        parser.add_argument('--markup', help='New markup percent, e.g. 20 for 20%%')
        parser.add_argument('--tfn-fee', help='New per-number provisioning fee, e.g. 20')
        parser.add_argument('--portal-fee', help='New monthly portal fee, e.g. 49.99. 0 disables it')
        parser.add_argument('--preview', type=int, metavar='SECONDS',
                            help='Show what a call of this length would cost')
        parser.add_argument('--list', action='store_true', help='Show every client rate')

    def handle(self, *args, **options):
        if options['list']:
            self._list()
            return

        org = self._resolve(options)
        account, _ = BillingAccount.objects.get_or_create(organization=org)

        changed = []
        if options['rate'] is not None:
            account.per_minute_rate = self._decimal(options['rate'], '--rate')
            changed.append('rate')
        if options['markup'] is not None:
            account.markup_percent = self._decimal(options['markup'], '--markup')
            changed.append('markup')
        if options['tfn_fee'] is not None:
            account.tfn_purchase_fee = self._decimal(options['tfn_fee'], '--tfn-fee')
            changed.append('tfn fee')
        if options['portal_fee'] is not None:
            account.monthly_portal_fee = self._decimal(options['portal_fee'], '--portal-fee')
            changed.append('portal fee')

        if changed:
            account.save(update_fields=[
                'per_minute_rate', 'markup_percent',
                'tfn_purchase_fee', 'monthly_portal_fee', 'updated_at',
            ])
            self.stdout.write(self.style.SUCCESS(f"Updated {', '.join(changed)} for {org.name}"))

        self.stdout.write(
            f'{org.name}: ${account.per_minute_rate}/min, markup {account.markup_percent}%, '
            f'TFN ${account.tfn_purchase_fee}, portal ${account.monthly_portal_fee}/mo'
        )
        last = account.portal_fee_charged_at
        self.stdout.write(
            f'  portal fee last charged: {last.strftime("%Y-%m-%d") if last else "never"}'
        )

        seconds = options['preview'] if options['preview'] is not None else 90
        cost = BillingService.call_cost(org, seconds)
        minutes = -(-seconds // 60)          # ceiling division
        self.stdout.write(
            f'  a {seconds}s call bills as {minutes} min = ${cost}'
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _list(self):
        accounts = {a.organization_id: a for a in BillingAccount.objects.all()}
        rows = []
        for org in Organization.objects.order_by('name'):
            a = accounts.get(org.id)
            if a is None:
                rows.append((org.name, 'no account', '', ''))
            else:
                rows.append((
                    org.name,
                    f'${a.per_minute_rate}/min',
                    f'TFN ${a.tfn_purchase_fee}',
                    f'portal ${a.monthly_portal_fee}/mo  balance ${a.balance}',
                ))
        if not rows:
            self.stdout.write('No organizations found.')
            return
        width = max(len(r[0]) for r in rows)
        for name, rate, markup, balance in rows:
            self.stdout.write(f'{name:<{width}}  {rate:<14} {markup:<14} {balance}')

    def _resolve(self, options) -> Organization:
        if options.get('org_id'):
            try:
                return Organization.objects.get(id=options['org_id'])
            except Organization.DoesNotExist:
                raise CommandError(f"No organization with id {options['org_id']}")
            except (ValueError, ValidationError):
                raise CommandError(f"{options['org_id']} is not a valid UUID")

        name = options.get('org')
        if not name:
            raise CommandError('Pass --org or --org-id (or --list)')

        matches = list(Organization.objects.filter(
            Q(name__iexact=name) | Q(name__icontains=name)
        )[:10])
        exact = [o for o in matches if o.name.lower() == name.lower()]
        if exact:
            return exact[0]
        if not matches:
            raise CommandError(f"No organization matching '{name}' — try --list")
        if len(matches) > 1:
            names = '\n  '.join(f'{o.name} ({o.id})' for o in matches)
            raise CommandError(f"'{name}' matches several:\n  {names}\nUse --org-id")
        return matches[0]

    def _decimal(self, raw, flag) -> Decimal:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            raise CommandError(f"'{raw}' is not a valid number for {flag}")
        if value < 0:
            raise CommandError(f'{flag} cannot be negative')
        return value

"""Manually credit an organization's billing account.

    python manage.py add_funds --org "Avortyx" --amount 50
    python manage.py add_funds --org-id cdf49649-... --amount 50 --note "wire ref 8891"
    python manage.py add_funds --email user@example.com --amount 50
    python manage.py add_funds --list

Writes a completed DEPOSIT transaction with provider='manual', so a manual
top-up is auditable alongside card and crypto payments.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts.models import Organization, User
from billing.models import BillingAccount
from billing.services import BillingService


class Command(BaseCommand):
    help = "Manually add funds to an organization's billing account"

    def add_arguments(self, parser):
        parser.add_argument('--org', help='Organization name (exact, or unique substring)')
        parser.add_argument('--org-id', help='Organization UUID')
        parser.add_argument('--email', help='Email of a user in the organization')
        parser.add_argument('--amount', help='Amount to credit, e.g. 50 or 49.99')
        parser.add_argument('--note', default='', help='Description stored on the transaction')
        parser.add_argument('--ref', default='', help='External reference id (wire/receipt number)')
        parser.add_argument('--list', action='store_true', help='List organizations and balances, then exit')

    def handle(self, *args, **options):
        if options['list']:
            self._list_balances()
            return

        organization = self._resolve_organization(options)
        amount = self._parse_amount(options.get('amount'))

        before = BillingService.get_balance(organization)

        tx = BillingService.add_funds(
            organization=organization,
            amount=amount,
            description=options['note'],
            reference_id=options['ref'],
        )

        self.stdout.write(self.style.SUCCESS(
            f"Credited ${amount} to {organization.name}\n"
            f"  balance: ${before} -> ${tx.balance_after}\n"
            f"  transaction: {tx.id}"
        ))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _list_balances(self):
        accounts = {
            a.organization_id: a
            for a in BillingAccount.objects.all()
        }
        rows = []
        for org in Organization.objects.order_by('name'):
            account = accounts.get(org.id)
            balance = account.balance if account else None
            rows.append((
                org.name,
                str(org.id),
                'no account' if balance is None else f'${balance}',
            ))

        if not rows:
            self.stdout.write("No organizations found.")
            return

        width = max(len(r[0]) for r in rows)
        for name, org_id, balance in rows:
            self.stdout.write(f"{name:<{width}}  {org_id}  {balance}")

    def _resolve_organization(self, options) -> Organization:
        org_id, org_name, email = options.get('org_id'), options.get('org'), options.get('email')

        if not any([org_id, org_name, email]):
            raise CommandError("Pass one of --org, --org-id or --email (or --list to see them)")

        if org_id:
            try:
                return Organization.objects.get(id=org_id)
            except Organization.DoesNotExist:
                raise CommandError(f"No organization with id {org_id}")
            except (ValueError, ValidationError):
                raise CommandError(f"{org_id} is not a valid UUID")

        if email:
            user = User.objects.filter(email__iexact=email).select_related('organization').first()
            if user is None:
                raise CommandError(f"No user with email {email}")
            if user.organization is None:
                raise CommandError(f"{email} does not belong to an organization")
            return user.organization

        matches = list(Organization.objects.filter(
            Q(name__iexact=org_name) | Q(name__icontains=org_name)
        )[:10])

        exact = [o for o in matches if o.name.lower() == org_name.lower()]
        if exact:
            return exact[0]
        if not matches:
            raise CommandError(f"No organization matching '{org_name}' — try --list")
        if len(matches) > 1:
            names = '\n  '.join(f"{o.name} ({o.id})" for o in matches)
            raise CommandError(f"'{org_name}' matches several organizations:\n  {names}\nUse --org-id")
        return matches[0]

    def _parse_amount(self, raw) -> Decimal:
        if not raw:
            raise CommandError("--amount is required")
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            raise CommandError(f"'{raw}' is not a valid amount")
        if amount <= 0:
            raise CommandError("--amount must be greater than zero")
        return amount.quantize(Decimal('0.01'))

"""Credit a crypto payment that was made but never confirmed by callback.

Every Capitalist payment sat at pending because the webhook looked a payment up
by primary key using the order number, which could never match. That is fixed,
but a callback can still fail to arrive - a provider outage, a misconfigured
callback URL, a customer closing the page. Without this, crediting someone who
has genuinely paid means a shell session on the server.

Check the payment on the provider's side first. This credits the balance on your
say-so; it does not verify anything with them.

    python manage.py confirm_crypto_payment --order 260928-001
    python manage.py confirm_crypto_payment --list
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

from billing.models import BillingAccount, Transaction


class Command(BaseCommand):
    help = "Credit a crypto payment whose confirmation never arrived"

    def add_arguments(self, parser):
        parser.add_argument('--order', help='The order number shown on the payment')
        parser.add_argument('--list', action='store_true',
                            help='List payments still awaiting confirmation')
        parser.add_argument('--fail', action='store_true',
                            help='Mark it failed instead of crediting it')

    def handle(self, *args, **options):
        pending = Transaction.objects.filter(
            provider__in=['capitalist', 'coingate'],
            status=Transaction.Status.PENDING,
        ).select_related('organization').order_by('-created_at')

        if options['list']:
            if not pending:
                self.stdout.write('Nothing awaiting confirmation.')
                return
            self.stdout.write(f'{pending.count()} payment(s) awaiting confirmation:\n')
            for t in pending:
                self.stdout.write(
                    f"  {t.created_at:%Y-%m-%d %H:%M}  {t.provider:<11}"
                    f"  order {t.capitalist_payment_id or '(none)':<12}"
                    f"  ${t.amount:>10}  {t.organization.name}"
                )
            return

        order = options['order']
        if not order:
            raise CommandError('Pass --order, or --list to see what is waiting.')

        try:
            txn = pending.get(capitalist_payment_id=order)
        except Transaction.DoesNotExist:
            raise CommandError(
                f'No pending payment with order number {order!r}. '
                f'Use --list to see what is waiting.'
            )
        except Transaction.MultipleObjectsReturned:
            raise CommandError(f'Several pending payments carry order {order!r}.')

        if options['fail']:
            txn.status = Transaction.Status.FAILED
            txn.save(update_fields=['status', 'updated_at'])
            self.stdout.write(self.style.SUCCESS(f'Marked {order} as failed.'))
            return

        with db_transaction.atomic():
            account = BillingAccount.objects.select_for_update().get(pk=txn.billing_account_id)
            locked = Transaction.objects.select_for_update().get(pk=txn.pk)
            if locked.status == Transaction.Status.COMPLETED:
                self.stdout.write('Already credited - doing nothing.')
                return
            before = Decimal(account.balance or 0)
            account.balance = before + locked.amount
            account.save(update_fields=['balance', 'updated_at'])
            locked.balance_before = before
            locked.balance_after = account.balance
            locked.status = Transaction.Status.COMPLETED
            locked.save(update_fields=['balance_before', 'balance_after', 'status', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'Credited ${txn.amount} to {txn.organization.name}. '
            f'Balance {before} -> {account.balance}'
        ))

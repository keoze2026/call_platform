import uuid
import math
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from django.db import transaction
from .models import BillingAccount, Transaction, Invoice
from accounts.models import User, Organization


class BillingService:

    @staticmethod
    def get_or_create(user: User) -> BillingAccount:
        if not user.organization:
            raise ValueError("User has no organization")

        account, created = BillingAccount.objects.get_or_create(
            organization=user.organization,
            defaults={
                'created_by': user,
                'status': BillingAccount.Status.ACTIVE,
            }
        )
        return account

    @staticmethod
    def get(user: User) -> BillingAccount:
        try:
            return BillingAccount.objects.get(organization=user.organization)
        except BillingAccount.DoesNotExist:
            return BillingService.get_or_create(user)

    @staticmethod
    def update(data, user: User) -> BillingAccount:
        account = BillingService.get(user)

        if data.low_balance_threshold is not None:
            account.low_balance_threshold = data.low_balance_threshold
        if data.auto_recharge is not None:
            account.auto_recharge = data.auto_recharge
        if data.auto_recharge_amount is not None:
            account.auto_recharge_amount = data.auto_recharge_amount
        if data.auto_recharge_threshold is not None:
            account.auto_recharge_threshold = data.auto_recharge_threshold

        account.save()
        return account

    @staticmethod
    @transaction.atomic
    def deposit(amount: Decimal, user: User, payment_method_id: str = None, stripe_payment_intent_id: str = '') -> Transaction:
        account = BillingService.get(user)

        if amount <= 0:
            raise ValueError("Deposit amount must be greater than zero")

        balance_before = account.balance
        account.balance += amount
        account.save(update_fields=['balance', 'updated_at'])

        tx = Transaction.objects.create(
            organization=user.organization,
            billing_account=account,
            transaction_type=Transaction.Type.DEPOSIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=account.balance,
            description=f"Deposit of ${amount}",
            stripe_payment_intent_id=stripe_payment_intent_id or '',
            status=Transaction.Status.COMPLETED
        )

        return tx

    @staticmethod
    def get_balance(organization) -> Decimal:
        """Current credit for an organization. Missing account reads as zero."""
        try:
            account = BillingAccount.objects.only('balance').get(organization=organization)
        except BillingAccount.DoesNotExist:
            return Decimal('0.00')
        return account.balance

    @staticmethod
    def has_sufficient_balance(organization, amount: Decimal) -> bool:
        """True when the organization can cover `amount`, credit limit included.

        An organization with no BillingAccount has no credit and returns False.
        """
        try:
            account = BillingAccount.objects.only(
                'balance', 'credit_limit', 'status'
            ).get(organization=organization)
        except BillingAccount.DoesNotExist:
            return False

        if account.status != BillingAccount.Status.ACTIVE:
            return False

        return (account.balance + account.credit_limit) >= amount

    @staticmethod
    @transaction.atomic
    def add_funds(organization, amount: Decimal, description: str = '',
                  reference_id: str = '', created_by: User = None) -> Transaction:
        """Manually credit an organization — no payment provider involved.

        Used by the `add_funds` management command and any admin-side top-up.
        Unlike deposit(), this keys off an Organization rather than a User, so it
        can run without a request context. Creates the BillingAccount if absent.
        """
        if amount <= 0:
            raise ValueError("Amount must be greater than zero")

        account, _ = BillingAccount.objects.select_for_update().get_or_create(
            organization=organization,
            defaults={
                'created_by': created_by,
                'status': BillingAccount.Status.ACTIVE,
            }
        )

        # A freshly created account carries the model's float default in memory,
        # and float + Decimal raises. Coerce before arithmetic.
        balance_before = Decimal(account.balance or 0)
        account.balance = balance_before + amount
        account.save(update_fields=['balance', 'updated_at'])

        return Transaction.objects.create(
            organization=organization,
            billing_account=account,
            transaction_type=Transaction.Type.DEPOSIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=account.balance,
            description=description or f"Manual credit of ${amount}",
            reference_id=reference_id or '',
            provider='manual',
            status=Transaction.Status.COMPLETED,
        )

    @staticmethod
    def call_cost(organization, duration_seconds: int) -> Decimal:
        """What this client is charged for a call of this length.

            ceil(duration / 60) x per_minute_rate x (1 + markup)

        Rounded up to the whole minute, the standard telecom convention: a
        90-second call bills as 2 minutes. A zero-length call (missed, no answer)
        costs nothing.

        Rate and markup are per-client fields on the billing account, so pricing
        changes without a deploy.
        """
        duration_seconds = int(duration_seconds or 0)
        if duration_seconds <= 0:
            return Decimal('0.00')

        try:
            account = BillingAccount.objects.only(
                'per_minute_rate', 'markup_percent'
            ).get(organization=organization)
        except BillingAccount.DoesNotExist:
            return Decimal('0.00')

        minutes = Decimal(math.ceil(duration_seconds / 60))
        rate = Decimal(account.per_minute_rate or 0)
        markup = Decimal(account.markup_percent or 0) / Decimal('100')

        cost = minutes * rate * (Decimal('1') + markup)
        return cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @staticmethod
    @transaction.atomic
    def charge_fee(organization, amount: Decimal, description: str,
                   reference_id: str = '') -> Transaction:
        """Take a one-off fee. Returns None when the balance cannot cover it.

        Idempotency is the caller's business: unlike charge_call there is no
        call_sid to key on, so pass a reference_id and check for it first if the
        fee must only be taken once.
        """
        if amount <= 0:
            return None

        try:
            account = BillingAccount.objects.select_for_update().get(
                organization=organization
            )
        except BillingAccount.DoesNotExist:
            return None

        balance_before = Decimal(account.balance or 0)
        if balance_before + Decimal(account.credit_limit or 0) < amount:
            return None

        account.balance = balance_before - amount
        account.save(update_fields=['balance', 'updated_at'])

        return Transaction.objects.create(
            organization=organization,
            billing_account=account,
            transaction_type=Transaction.Type.CHARGE,
            amount=amount,
            balance_before=balance_before,
            balance_after=account.balance,
            description=description,
            reference_id=reference_id or '',
            provider='manual',
            status=Transaction.Status.COMPLETED,
        )

    @staticmethod
    def tfn_fee(organization) -> Decimal:
        """What this client pays to provision one tracking number."""
        try:
            account = BillingAccount.objects.only('tfn_purchase_fee').get(
                organization=organization
            )
        except BillingAccount.DoesNotExist:
            return Decimal('0.00')
        return Decimal(account.tfn_purchase_fee or 0)

    @staticmethod
    @transaction.atomic
    def charge_call(organization, campaign, buyer, publisher, amount: Decimal, call_sid: str = '') -> Transaction:
        # Carriers retry end-of-call webhooks. Without this guard a retry would
        # charge the same call twice.
        if call_sid:
            existing = Transaction.objects.filter(
                organization=organization,
                call_sid=call_sid,
                transaction_type=Transaction.Type.CHARGE,
                status=Transaction.Status.COMPLETED,
            ).first()
            if existing:
                return existing

        try:
            account = BillingAccount.objects.select_for_update().get(
                organization=organization
            )
        except BillingAccount.DoesNotExist:
            return None

        if Decimal(account.balance or 0) < amount:
            return None

        balance_before = Decimal(account.balance or 0)
        account.balance = balance_before - amount
        account.save(update_fields=['balance', 'updated_at'])

        tx = Transaction.objects.create(
            organization=organization,
            billing_account=account,
            transaction_type=Transaction.Type.CHARGE,
            amount=amount,
            balance_before=balance_before,
            balance_after=account.balance,
            description=f"Call charge",
            call_sid=call_sid,
            campaign_id=campaign.id if campaign else None,
            campaign_name=campaign.name if campaign else '',
            buyer_id=buyer.id if buyer else None,
            buyer_name=buyer.name if buyer else '',
            publisher_id=publisher.id if publisher else None,
            publisher_name=publisher.name if publisher else '',
            status=Transaction.Status.COMPLETED
        )

        return tx

    @staticmethod
    @transaction.atomic
    def payout(organization, publisher, amount: Decimal, call_sid: str = '') -> Transaction:
        try:
            account = BillingAccount.objects.get(organization=organization)
        except BillingAccount.DoesNotExist:
            return None

        balance_before = account.balance
        account.balance -= amount
        account.save(update_fields=['balance', 'updated_at'])

        tx = Transaction.objects.create(
            organization=organization,
            billing_account=account,
            transaction_type=Transaction.Type.PAYOUT,
            amount=amount,
            balance_before=balance_before,
            balance_after=account.balance,
            description=f"Publisher payout",
            call_sid=call_sid,
            publisher_id=publisher.id if publisher else None,
            publisher_name=publisher.name if publisher else '',
            status=Transaction.Status.COMPLETED
        )

        return tx

    @staticmethod
    def list_transactions(user: User):
        return Transaction.objects.filter(
            organization=user.organization
        ).order_by('-created_at')[:100]

    @staticmethod
    def list_invoices(user: User):
        return Invoice.objects.filter(
            organization=user.organization
        ).order_by('-created_at')

    @staticmethod
    def create_stripe_payment_intent(amount: Decimal, user: User) -> dict:
        try:
            import stripe
            from django.conf import settings
            stripe.api_key = settings.STRIPE_SECRET_KEY

            account = BillingService.get(user)

            if not account.stripe_customer_id:
                customer = stripe.Customer.create(
                    email=user.email,
                    name=user.organization.name,
                )
                account.stripe_customer_id = customer.id
                account.save(update_fields=['stripe_customer_id'])

            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),
                currency='usd',
                customer=account.stripe_customer_id,
                metadata={
                    'organization_id': str(user.organization_id),
                    'user_id': str(user.id),
                }
            )

            return {
                'client_secret': intent.client_secret,
                'payment_intent_id': intent.id,
                'amount': str(amount),
            }

        except Exception as e:
            raise ValueError(f"Stripe error: {str(e)}")

    @staticmethod
    def format_account(account: BillingAccount) -> dict:
        return {
            'id': str(account.id),
            'balance': account.balance,
            'credit_limit': account.credit_limit,
            'low_balance_threshold': account.low_balance_threshold,
            'auto_recharge': account.auto_recharge,
            'auto_recharge_amount': account.auto_recharge_amount,
            'auto_recharge_threshold': account.auto_recharge_threshold,
            'stripe_customer_id': account.stripe_customer_id,
            'currency': account.currency,
            'status': account.status,
            'organization_id': str(account.organization_id),
            'created_at': account.created_at.isoformat(),
            'updated_at': account.updated_at.isoformat(),
        }



    @staticmethod
    def save_payment_method(payment_method_id: str, user: User) -> dict:
        try:
            import stripe
            from django.conf import settings
            stripe.api_key = settings.STRIPE_SECRET_KEY

            account = BillingService.get(user)

            if not account.stripe_customer_id:
                customer = stripe.Customer.create(
                    email=user.email,
                    name=user.organization.name,
                )
                account.stripe_customer_id = customer.id
                account.save(update_fields=['stripe_customer_id'])

            # Attach payment method to customer
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=account.stripe_customer_id,
            )

            # Set as default
            stripe.Customer.modify(
                account.stripe_customer_id,
                invoice_settings={'default_payment_method': payment_method_id},
            )

            account.stripe_payment_method_id = payment_method_id
            account.save(update_fields=['stripe_payment_method_id'])

            return {'message': 'Payment method saved successfully', 'success': True}

        except Exception as e:
            raise ValueError(f"Stripe error: {str(e)}")


    @staticmethod
    def list_payment_methods(user: User) -> list:
        try:
            import stripe
            from django.conf import settings
            stripe_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
            if not stripe_key or stripe_key.startswith('<'):
                return []
            stripe.api_key = stripe_key

            account = BillingService.get(user)

            if not account.stripe_customer_id:
                return []

            methods = stripe.PaymentMethod.list(
                customer=account.stripe_customer_id,
                type='card',
            )

            return [
                {
                    'id': pm.id,
                    'brand': pm.card.brand,
                    'last4': pm.card.last4,
                    'exp_month': pm.card.exp_month,
                    'exp_year': pm.card.exp_year,
                    'is_default': pm.id == account.stripe_payment_method_id,
                }
                for pm in methods.data
            ]

        except Exception as e:
            raise ValueError(f"Stripe error: {str(e)}")


    @staticmethod
    def delete_payment_method(payment_method_id: str, user: User):
        try:
            import stripe
            from django.conf import settings
            stripe.api_key = settings.STRIPE_SECRET_KEY

            stripe.PaymentMethod.detach(payment_method_id)

            account = BillingService.get(user)
            if account.stripe_payment_method_id == payment_method_id:
                account.stripe_payment_method_id = ''
                account.save(update_fields=['stripe_payment_method_id'])

        except Exception as e:
            raise ValueError(f"Stripe error: {str(e)}")
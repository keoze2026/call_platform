import uuid
from decimal import Decimal
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
    @transaction.atomic
    def charge_call(organization, campaign, buyer, publisher, amount: Decimal, call_sid: str = '') -> Transaction:
        try:
            account = BillingAccount.objects.select_for_update().get(
                organization=organization
            )
        except BillingAccount.DoesNotExist:
            return None

        if account.balance < amount:
            return None

        balance_before = account.balance
        account.balance -= amount
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
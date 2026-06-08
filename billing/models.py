import uuid
from django.db import models
from accounts.models import Organization, User


class BillingAccount(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        SUSPENDED = 'suspended', 'Suspended'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='billing_account')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_billing_accounts')

    # Stripe
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_payment_method_id = models.CharField(max_length=255, blank=True)

    # Balance
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    credit_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    low_balance_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)
    auto_recharge = models.BooleanField(default=False)
    auto_recharge_amount = models.DecimalField(max_digits=10, decimal_places=2, default=50.00)
    auto_recharge_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)


    currency = models.CharField(max_length=3, default='USD', help_text='ISO 4217 currency code (USD, EUR, GBP, etc)')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_accounts'

    def __str__(self):
        return f"{self.organization} — ${self.balance}"


class Transaction(models.Model):

    class Type(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        CHARGE = 'charge', 'Charge'
        PAYOUT = 'payout', 'Payout'
        REFUND = 'refund', 'Refund'
        ADJUSTMENT = 'adjustment', 'Adjustment'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='transactions')
    billing_account = models.ForeignKey(BillingAccount, on_delete=models.CASCADE, related_name='transactions')


    currency = models.CharField(max_length=3, default='USD')

    transaction_type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_before = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Reference
    description = models.CharField(max_length=255, blank=True)
    reference_id = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)

    # Call reference
    call_sid = models.CharField(max_length=100, blank=True)
    campaign_id = models.UUIDField(null=True, blank=True)
    campaign_name = models.CharField(max_length=255, blank=True)
    buyer_id = models.UUIDField(null=True, blank=True)
    buyer_name = models.CharField(max_length=255, blank=True)
    publisher_id = models.UUIDField(null=True, blank=True)
    publisher_name = models.CharField(max_length=255, blank=True)

    #coingate intergration 
    # Provider
    provider = models.CharField(max_length=20, default='stripe', blank=True)
    coingate_order_id = models.CharField(max_length=255, blank=True)
    coingate_payment_url = models.URLField(blank=True)

    #capitalist.net 
    capitalist_payment_id = models.CharField(max_length=255, blank=True)
    capitalist_payment_url = models.URLField(blank=True, max_length=500)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'transactions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['organization', 'transaction_type']),
        ]

    def __str__(self):
        return f"{self.transaction_type} ${self.amount} ({self.status})"


class Invoice(models.Model):

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        PAID = 'paid', 'Paid'
        OVERDUE = 'overdue', 'Overdue'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='invoices')
    billing_account = models.ForeignKey(BillingAccount, on_delete=models.CASCADE, related_name='invoices')


    currency = models.CharField(max_length=3, default='USD')
    
    invoice_number = models.CharField(max_length=50, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()

    total_calls = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_payout = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    stripe_invoice_id = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'
        ordering = ['-created_at']

    def __str__(self):
        return f"Invoice {self.invoice_number} ({self.status})"
from .plans import Plan, OrganizationPlan

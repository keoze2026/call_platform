from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import BillingAccount, Transaction, Invoice


class TransactionInline(TabularInline):
    model = Transaction
    extra = 0
    readonly_fields = ['id', 'transaction_type', 'amount', 'status', 'created_at']


class InvoiceInline(TabularInline):
    model = Invoice
    extra = 0
    readonly_fields = ['id', 'invoice_number', 'total_amount', 'status', 'created_at']


@admin.register(BillingAccount)
class BillingAccountAdmin(ModelAdmin):
    list_display = [
        'organization', 'status', 'balance', 'credit_limit',
        'currency', 'auto_recharge', 'auto_recharge_amount',
        'low_balance_threshold', 'created_at'
    ]
    list_filter = ['status', 'currency', 'auto_recharge']
    search_fields = ['organization__name', 'stripe_customer_id']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [TransactionInline, InvoiceInline]
    fieldsets = (
        ('Account', {
            'fields': ('id', 'organization', 'created_by', 'status', 'currency')
        }),
        ('Balance', {
            'fields': (
                'balance', 'credit_limit',
                'low_balance_threshold', 'auto_recharge',
                'auto_recharge_amount', 'auto_recharge_threshold'
            )
        }),
        ('Stripe', {
            'fields': ('stripe_customer_id', 'stripe_payment_method_id')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    list_display = [
        'billing_account', 'transaction_type', 'amount',
        'currency', 'status', 'provider', 'description', 'created_at'
    ]
    list_filter = ['transaction_type', 'status', 'currency', 'provider']
    search_fields = [
        'billing_account__organization__name',
        'description', 'stripe_payment_intent_id',
        'coingate_order_id', 'capitalist_payment_id',
        'campaign_name', 'buyer_name', 'publisher_name'
    ]
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'created_at', 'updated_at',
        'stripe_payment_intent_id',
        'coingate_order_id', 'coingate_payment_url',
        'capitalist_payment_id', 'capitalist_payment_url',
        'balance_before', 'balance_after'
    ]
    fieldsets = (
        ('Transaction Info', {
            'fields': (
                'id', 'organization', 'billing_account',
                'transaction_type', 'amount', 'currency',
                'status', 'description', 'reference_id'
            )
        }),
        ('Balance Snapshot', {
            'fields': ('balance_before', 'balance_after')
        }),
        ('Call Reference', {
            'fields': (
                'call_sid', 'campaign_id', 'campaign_name',
                'buyer_id', 'buyer_name',
                'publisher_id', 'publisher_name'
            )
        }),
        ('Payment Provider', {
            'fields': (
                'provider',
                'stripe_payment_intent_id',
                'coingate_order_id', 'coingate_payment_url',
                'capitalist_payment_id', 'capitalist_payment_url'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = [
        'invoice_number', 'billing_account', 'status',
        'total_amount', 'currency', 'period_start',
        'period_end', 'paid_at', 'created_at'
    ]
    list_filter = ['status', 'currency']
    search_fields = [
        'invoice_number',
        'billing_account__organization__name'
    ]
    ordering = ['-created_at']
    readonly_fields = [
        'id', 'invoice_number', 'created_at',
        'updated_at', 'paid_at', 'stripe_invoice_id'
    ]
    fieldsets = (
        ('Invoice Info', {
            'fields': (
                'id', 'organization', 'billing_account',
                'invoice_number', 'status', 'currency'
            )
        }),
        ('Period', {
            'fields': ('period_start', 'period_end')
        }),
        ('Totals', {
            'fields': (
                'total_calls', 'total_revenue',
                'total_payout', 'total_amount'
            )
        }),
        ('Payment', {
            'fields': ('stripe_invoice_id', 'paid_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
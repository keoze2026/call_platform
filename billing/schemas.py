from ninja import Schema
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, date


class DepositSchema(Schema):
    amount: Decimal
    payment_method_id: Optional[str] = None


class UpdateBillingSchema(Schema):
    low_balance_threshold: Optional[Decimal] = None
    auto_recharge: Optional[bool] = None
    auto_recharge_amount: Optional[Decimal] = None
    auto_recharge_threshold: Optional[Decimal] = None


class BillingAccountOutSchema(Schema):
    id: str
    balance: Decimal
    credit_limit: Decimal
    low_balance_threshold: Decimal
    auto_recharge: bool
    auto_recharge_amount: Decimal
    auto_recharge_threshold: Decimal
    stripe_customer_id: str
    currency: str
    status: str
    organization_id: str
    per_minute_rate: Decimal = Decimal('0')
    markup_percent: Decimal = Decimal('0')
    tfn_purchase_fee: Decimal = Decimal('0')
    monthly_portal_fee: Decimal = Decimal('0')
    portal_fee_charged_at: Optional[str] = None
    portal_fee_next_due: Optional[str] = None
    created_at: str
    updated_at: str


class TransactionOutSchema(Schema):
    id: str
    transaction_type: str
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    description: str
    reference_id: str
    call_sid: str
    campaign_name: str
    buyer_name: str
    publisher_name: str
    status: str
    created_at: str


class InvoiceOutSchema(Schema):
    id: str
    invoice_number: str
    period_start: str
    period_end: str
    total_calls: int
    total_revenue: Decimal
    total_payout: Decimal
    total_amount: Decimal
    status: str
    paid_at: Optional[str] = None
    created_at: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True
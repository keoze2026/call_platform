from ninja import Schema
from pydantic import field_validator
from typing import Optional, List
from decimal import Decimal


# ===== REQUEST SCHEMAS =====

class BuyerCapSchema(Schema):
    max_calls_daily: Optional[int] = None
    max_calls_monthly: Optional[int] = None
    max_calls_global: Optional[int] = None
    max_concurrency: Optional[int] = None
    daily: Optional[int] = None
    monthly: Optional[int] = None
    concurrency: Optional[int] = None


class CreateBuyerSchema(Schema):
    name: str
    description: Optional[str] = ''
    routing_type: str = 'external'
    phone_number: Optional[str] = ''
    sip_endpoint: Optional[str] = ''
    payout_amount: Decimal = Decimal('0.00')
    min_call_duration: int = 0
    max_concurrency: int = 0
    dup_window_days: int = 30
    quality_score: int = 50
    contact_name: Optional[str] = ''
    contact_email: Optional[str] = ''
    cap: Optional[BuyerCapSchema] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError('Buyer name must be at least 2 characters')
        return v.strip()

    @field_validator('routing_type')
    @classmethod
    def validate_routing_type(cls, v: str) -> str:
        allowed = ['external', 'sip']
        if v not in allowed:
            raise ValueError(f'routing_type must be one of {allowed}')
        return v

    @field_validator('payout_amount')
    @classmethod
    def validate_payout(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError('Payout amount cannot be negative')
        return v

    @field_validator('min_call_duration')
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 0:
            raise ValueError('min_call_duration cannot be negative')
        return v


class UpdateBuyerSchema(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    routing_type: Optional[str] = None
    phone_number: Optional[str] = None
    sip_endpoint: Optional[str] = None
    payout_amount: Optional[Decimal] = None
    min_call_duration: Optional[int] = None
    max_concurrency: Optional[int] = None
    dup_window_days: Optional[int] = None
    quality_score: Optional[int] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None


class AssignCampaignSchema(Schema):
    campaign_id: str
    priority: int = 1
    weight: int = 100


# ===== RESPONSE SCHEMAS =====

class BuyerCapOutSchema(Schema):
    id: str
    max_calls_daily: int
    max_calls_monthly: int
    max_calls_global: int
    max_concurrency: int


class CampaignAssignmentOutSchema(Schema):
    id: str
    campaign_id: str
    campaign_name: str
    priority: int
    weight: int
    is_active: bool


class BuyerOutSchema(Schema):
    id: str
    name: str
    description: str
    status: str
    routing_type: str
    phone_number: str
    sip_endpoint: str
    payout_amount: str
    min_call_duration: int
    max_concurrency: int
    dup_window_days: int
    quality_score: int
    organization_id: str
    organization_name: str
    contact_name: str
    contact_email: str
    created_by_id: Optional[str] = None
    cap: Optional[BuyerCapOutSchema] = None
    campaigns: List[CampaignAssignmentOutSchema] = []
    created_at: str
    updated_at: str


class BuyerListOutSchema(Schema):
    id: str
    name: str
    status: str
    routing_type: str
    phone_number: str
    payout_amount: str
    created_at: str


class BuyerStatsSchema(Schema):
    buyer_id: str
    buyer_name: str
    total_calls: int
    calls_today: int
    calls_this_month: int
    payout_today: str
    payout_this_month: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True
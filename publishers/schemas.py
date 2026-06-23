from ninja import Schema
from pydantic import EmailStr, field_validator
from typing import Optional, List
from decimal import Decimal


# ===== REQUEST SCHEMAS =====

class PublisherCapSchema(Schema):
    max_calls_daily: Optional[int] = None
    max_calls_monthly: Optional[int] = None
    max_calls_global: Optional[int] = None
    daily: Optional[int] = None
    monthly: Optional[int] = None
    concurrency: Optional[int] = None


class CreatePublisherSchema(Schema):
    name: str
    description: Optional[str] = ''
    email: Optional[str] = ''
    phone_number: Optional[str] = ''
    payout_amount: Decimal = Decimal('0.00')
    cap: Optional[PublisherCapSchema] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError('Publisher name must be at least 2 characters')
        return v.strip()

    @field_validator('payout_amount')
    @classmethod
    def validate_payout(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError('Payout amount cannot be negative')
        return v


class UpdatePublisherSchema(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    payout_amount: Optional[Decimal] = None


class AssignCampaignSchema(Schema):
    campaign_id: str
    payout_amount: Decimal = Decimal('0.00')


# ===== RESPONSE SCHEMAS =====

class PublisherCapOutSchema(Schema):
    id: str
    max_calls_daily: int
    max_calls_monthly: int
    max_calls_global: int


class CampaignAssignmentOutSchema(Schema):
    id: str
    campaign_id: str
    campaign_name: str
    payout_amount: str
    is_active: bool


class PublisherOutSchema(Schema):
    id: str
    name: str
    description: str
    status: str
    email: str
    phone_number: str
    payout_amount: str
    unique_id: str
    organization_id: str
    created_by_id: Optional[str] = None
    cap: Optional[PublisherCapOutSchema] = None
    campaigns: List[CampaignAssignmentOutSchema] = []
    created_at: str
    updated_at: str


class PublisherListOutSchema(Schema):
    id: str
    name: str
    status: str
    email: str
    payout_amount: str
    unique_id: str
    created_at: str


class PublisherStatsSchema(Schema):
    publisher_id: str
    publisher_name: str
    total_calls: int
    calls_today: int
    calls_this_month: int
    payout_today: str
    payout_this_month: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True
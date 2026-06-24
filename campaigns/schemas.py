from ninja import Schema
from pydantic import field_validator
from typing import Optional, List
from decimal import Decimal


# ===== REQUEST SCHEMAS =====

class CampaignCapSchema(Schema):
    max_calls_daily: int = 0
    max_calls_monthly: int = 0
    max_calls_global: int = 0
    max_concurrency: int = 0


class CampaignScheduleSchema(Schema):
    day_of_week: int
    open_time: str
    close_time: str
    is_closed: bool = False

    @field_validator('day_of_week')
    @classmethod
    def validate_day(cls, v: int) -> int:
        if v not in range(7):
            raise ValueError('day_of_week must be between 0 and 6')
        return v

    @field_validator('open_time', 'close_time')
    @classmethod
    def validate_time(cls, v: str) -> str:
        import re
        if not re.match(r'^\d{2}:\d{2}$', v):
            raise ValueError('Time must be in HH:MM format')
        return v


class CreateCampaignSchema(Schema):
    name: str
    description: Optional[str] = ''
    routing_type: str = 'priority'
    payout_amount: Decimal = Decimal('0.00')
    revenue_amount: Decimal = Decimal('0.00')
    min_call_duration: int = 0
    duplicate_call_block: bool = False
    duplicate_call_block_hours: int = 24
    bid_floor: Decimal = Decimal('0')
    rtb_timeout_seconds: int = 5
    cap: Optional[CampaignCapSchema] = None
    schedules: Optional[List[CampaignScheduleSchema]] = None
    greeting_enabled: bool = False
    greeting_message: Optional[str] = ''
    whisper_enabled: bool = False
    whisper_message: Optional[str] = ''
    auto_sms_enabled: bool = False
    auto_sms_message: Optional[str] = ''
    recording_enabled: bool = True

    @field_validator('routing_type')
    @classmethod
    def validate_routing_type(cls, v: str) -> str:
        allowed = ['priority', 'percentage', 'round_robin']
        if v not in allowed:
            raise ValueError(f'routing_type must be one of {allowed}')
        return v

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError('Campaign name must be at least 2 characters')
        return v.strip()

    @field_validator('payout_amount', 'revenue_amount', 'bid_floor')
    @classmethod
    def validate_amounts(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError('Amount cannot be negative')
        return v

    @field_validator('min_call_duration')
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 0:
            raise ValueError('min_call_duration cannot be negative')
        return v

    @field_validator('rtb_timeout_seconds')
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 1 or v > 30:
            raise ValueError('rtb_timeout_seconds must be between 1 and 30')
        return v


class UpdateCampaignSchema(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    routing_type: Optional[str] = None
    payout_amount: Optional[Decimal] = None
    revenue_amount: Optional[Decimal] = None
    min_call_duration: Optional[int] = None
    duplicate_call_block: Optional[bool] = None
    duplicate_call_block_hours: Optional[int] = None
    bid_floor: Optional[Decimal] = None
    rtb_timeout_seconds: Optional[int] = None
    greeting_enabled: Optional[bool] = None
    greeting_message: Optional[str] = None
    whisper_enabled: Optional[bool] = None
    whisper_message: Optional[str] = None
    auto_sms_enabled: Optional[bool] = None
    auto_sms_message: Optional[str] = None
    recording_enabled: Optional[bool] = None
    advanced_settings: Optional[dict] = None
    queue_enabled: Optional[bool] = None
    queue_max_size: Optional[int] = None
    queue_max_wait_seconds: Optional[int] = None
    queue_music_url: Optional[str] = None
    queue_message: Optional[str] = None


# ===== RESPONSE SCHEMAS =====

class CampaignCapOutSchema(Schema):
    id: str
    max_calls_daily: int
    max_calls_monthly: int
    max_calls_global: int
    max_concurrency: int


class CampaignScheduleOutSchema(Schema):
    id: str
    day_of_week: int
    day_name: str
    open_time: str
    close_time: str
    is_closed: bool


class CampaignOutSchema(Schema):
    id: str
    name: str
    description: str
    status: str
    routing_type: str
    payout_amount: str
    revenue_amount: str
    min_call_duration: int
    duplicate_call_block: bool
    duplicate_call_block_hours: int
    bid_floor: str
    rtb_timeout_seconds: int
    organization_id: str
    created_by_id: Optional[str] = None
    cap: Optional[CampaignCapOutSchema] = None
    schedules: List[CampaignScheduleOutSchema] = []
    greeting_enabled: bool = False
    greeting_message: str = ''
    whisper_enabled: bool = False
    whisper_message: str = ''
    auto_sms_enabled: bool = False
    auto_sms_message: str = ''
    recording_enabled: bool = True
    advanced_settings: Optional[dict] = None
    created_at: str
    updated_at: str

    queue_enabled: bool = False
    queue_max_size: int = 10
    queue_max_wait_seconds: int = 300
    queue_music_url: str = ''
    queue_message: str = ''


class CampaignListOutSchema(Schema):
    id: str
    name: str
    status: str
    routing_type: str
    payout_amount: str
    revenue_amount: str
    created_at: str


class CampaignStatsSchema(Schema):
    campaign_id: str
    campaign_name: str
    total_calls: int
    calls_today: int
    calls_this_month: int
    revenue_today: str
    revenue_this_month: str
    payout_today: str
    payout_this_month: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True
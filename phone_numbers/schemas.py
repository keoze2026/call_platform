from ninja import Schema
from typing import Optional, List


# ===== REQUEST SCHEMAS =====

class SearchNumberSchema(Schema):
    country_code: str = 'US'
    number_type: str = 'local'
    area_code: Optional[str] = None
    contains: Optional[str] = None
    limit: int = 10


class PurchaseNumberSchema(Schema):
    phone_number: str
    friendly_name: Optional[str] = ''
    number_type: str = 'local'
    campaign_id: Optional[str] = None
    vendor: Optional[str] = 'Twilio'
    state: Optional[str] = ''
    allocated_capacity: Optional[int] = 1
    renews_at: Optional[str] = None
    label: Optional[str] = None
    cap_enabled: Optional[bool] = None
    daily_cap: Optional[int] = None


class AssignNumberSchema(Schema):
    campaign_id: Optional[str] = None
    publisher_id: Optional[str] = None


_UNSET = object()

class UpdateNumberSchema(Schema):
    friendly_name: Optional[str] = None
    vendor: Optional[str] = None
    # Empty string clears the carrier; omitted leaves it as it was.
    carrier_id: Optional[str] = None
    state: Optional[str] = None
    allocated_capacity: Optional[int] = None
    renews_at: Optional[str] = None
    label: Optional[str] = None
    cap_enabled: Optional[bool] = None
    daily_cap: Optional[int] = None
    monthly_cap: Optional[int] = None
    concurrency_enabled: Optional[bool] = None
    concurrency_cap: Optional[int] = None
    vendor_enabled: Optional[bool] = None
    payout_per_call: Optional[float] = None
    payout_type: Optional[str] = None
    payout_on: Optional[str] = None
    dupe_revenue: Optional[str] = None
    dupe_revenue_days: Optional[int] = None
    traffic_source_enabled: Optional[bool] = None
    traffic_source_id: Optional[str] = None
    publisher_id: Optional[str] = None
    status: Optional[str] = None
    campaign_id: Optional[str] = None
    detach_campaign: Optional[bool] = None


# ===== RESPONSE SCHEMAS =====

class AvailableNumberSchema(Schema):
    phone_number: str
    friendly_name: str
    region: str
    postal_code: str
    number_type: str
    voice_enabled: bool
    sms_enabled: bool


class PhoneNumberOutSchema(Schema):
    id: str
    number: str
    friendly_name: str
    number_type: str
    status: str
    country_code: str
    twilio_sid: Optional[str] = None
    vendor: str
    carrier_id: Optional[str] = None
    carrier_name: str = ''
    carrier_code: str = ''
    assigned_at: Optional[str] = None
    expires_at: Optional[str] = None
    used_at: Optional[str] = None
    state: str
    allocated_capacity: int
    label: str
    cap_enabled: bool
    daily_cap: int
    monthly_cap: int
    concurrency_enabled: bool
    concurrency_cap: int
    vendor_enabled: bool
    payout_per_call: float
    payout_type: str
    payout_on: str
    dupe_revenue: str
    dupe_revenue_days: int
    traffic_source_enabled: bool
    traffic_source_id: Optional[str] = None
    renews_at: Optional[str] = None
    voice_enabled: bool
    sms_enabled: bool
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    publisher_id: Optional[str] = None
    publisher_name: Optional[str] = None
    organization_id: str
    created_at: str
    updated_at: str
    # Set only on purchase, when the number was bought but not attached to the SIP trunk
    trunk_warning: Optional[str] = None


class PhoneNumberListSchema(Schema):
    id: str
    number: str
    friendly_name: str
    number_type: str
    status: str
    country_code: str
    vendor: str
    carrier_id: Optional[str] = None
    carrier_name: str = ''
    carrier_code: str = ''
    assigned_at: Optional[str] = None
    expires_at: Optional[str] = None
    used_at: Optional[str] = None
    state: str
    allocated_capacity: int
    label: str
    cap_enabled: bool
    daily_cap: int
    monthly_cap: int
    concurrency_enabled: bool
    concurrency_cap: int
    vendor_enabled: bool
    payout_per_call: float
    payout_type: str
    payout_on: str
    dupe_revenue: str
    dupe_revenue_days: int
    traffic_source_enabled: bool
    traffic_source_id: Optional[str] = None
    renews_at: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    publisher_id: Optional[str] = None
    publisher_name: Optional[str] = None
    created_at: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True


class CarrierInSchema(Schema):
    name: str
    code: str
    is_active: Optional[bool] = True
    notes: Optional[str] = ''
    # A number from this carrier is retired once a call has used it.
    single_use: Optional[bool] = False
    # Hours a number stays usable. 0 means no limit.
    lifetime_hours: Optional[int] = 0


class CarrierUpdateSchema(Schema):
    name: Optional[str] = None
    code: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    single_use: Optional[bool] = None
    lifetime_hours: Optional[int] = None


class CarrierOutSchema(Schema):
    id: str
    name: str
    code: str
    is_active: bool
    notes: str
    single_use: bool = False
    lifetime_hours: int = 0
    numbers_count: int = 0
    created_at: str

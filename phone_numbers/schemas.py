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


class AssignNumberSchema(Schema):
    campaign_id: Optional[str] = None
    publisher_id: Optional[str] = None


class UpdateNumberSchema(Schema):
    friendly_name: Optional[str] = None


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
    voice_enabled: bool
    sms_enabled: bool
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    publisher_id: Optional[str] = None
    publisher_name: Optional[str] = None
    organization_id: str
    created_at: str
    updated_at: str


class PhoneNumberListSchema(Schema):
    id: str
    number: str
    friendly_name: str
    number_type: str
    status: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    publisher_id: Optional[str] = None
    publisher_name: Optional[str] = None
    created_at: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True
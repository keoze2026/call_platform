from ninja import Schema
from typing import Optional, List
from decimal import Decimal

class CreateWebhookSchema(Schema):
    name: str
    url: str
    secret: Optional[str] = ''
    headers: Optional[list] = []
    events: List[str] = []
    max_retries: Optional[int] = 3
    timeout_seconds: Optional[int] = 10


class UpdateWebhookSchema(Schema):
    name: Optional[str] = None
    url: Optional[str] = None
    secret: Optional[str] = None
    headers: Optional[list] = None
    events: Optional[List[str]] = None
    status: Optional[str] = None
    max_retries: Optional[int] = None
    timeout_seconds: Optional[int] = None


class WebhookDeliveryOutSchema(Schema):
    id: str
    event: str
    status: str
    response_code: Optional[int] = None
    response_body: str
    attempts: int
    created_at: str
    updated_at: str


class WebhookOutSchema(Schema):
    id: str
    name: str
    url: str
    events: list
    status: str
    max_retries: int
    secret: Optional[str] = None
    headers: Optional[list] = []
    timeout_seconds: int
    organization_id: str
    created_at: str
    updated_at: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True


class CreateConversionPixelSchema(Schema):
    name: str
    campaign_id: Optional[str] = None
    conversion_value: Decimal = Decimal('0')


class UpdateConversionPixelSchema(Schema):
    name: Optional[str] = None
    campaign_id: Optional[str] = None
    conversion_value: Optional[Decimal] = None
    status: Optional[str] = None


class ConversionPixelOutSchema(Schema):
    id: str
    name: str
    token: str
    campaign_id: Optional[str] = None
    conversion_value: str
    status: str
    postback_url: str
    created_at: str
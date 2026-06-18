from ninja import Schema
from typing import Optional, List


# ===== REQUEST SCHEMAS =====

class CreateRoutingRuleSchema(Schema):
    campaign_id: str
    name: str
    rule_type: str
    priority: int = 1


class UpdateRoutingRuleSchema(Schema):
    name: Optional[str] = None
    rule_type: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None


class CreateConditionSchema(Schema):
    conditions: dict


class CreateDestinationSchema(Schema):
    destination_type: str = 'phone'
    destination: Optional[str] = None
    phone_number: Optional[str] = None
    buyer_id: Optional[str] = None
    priority: int = 1
    weight: int = 100


# ===== RESPONSE SCHEMAS =====

class ConditionOutSchema(Schema):
    id: str
    conditions: dict


class DestinationOutSchema(Schema):
    id: str
    destination_type: str
    destination: Optional[str] = None
    priority: int
    weight: int
    buyer_id: Optional[str] = None
    buyer_name: Optional[str] = None


class RoutingRuleOutSchema(Schema):
    id: str
    name: str
    rule_type: str
    priority: int
    status: str
    campaign_id: str
    campaign_name: str
    conditions: List[ConditionOutSchema] = []
    destinations: List[DestinationOutSchema] = []
    created_at: str
    updated_at: str


class RoutingRuleListSchema(Schema):
    id: str
    name: str
    rule_type: str
    priority: int
    status: str
    campaign_id: str
    campaign_name: str
    created_at: str


class CallLogOutSchema(Schema):
    id: str
    caller_number: str
    called_number: str
    destination_number: str
    status: str
    duration: int
    caller_area_code: str
    caller_state: str
    caller_country: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    buyer_id: Optional[str] = None
    buyer_name: Optional[str] = None
    publisher_id: Optional[str] = None
    publisher_name: Optional[str] = None
    revenue: str
    buyer_payout: str
    publisher_payout: str
    recording_url: str
    tags: list
    notes: str
    created_at: str
    updated_at: str
    transcription_text: str = ''
    transcription_status: str = ''
    sentiment: str = ''
    sentiment_score: Optional[float] = None

class CallLogListSchema(Schema):
    id: str
    caller_number: str
    called_number: str
    destination_number: str
    status: str
    duration: int
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    buyer_id: Optional[str] = None
    buyer_name: Optional[str] = None
    revenue: str
    created_at: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True

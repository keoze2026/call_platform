from ninja import Schema
from typing import Optional, List
import uuid


class IVRNodeConfigSchema(Schema):
    message: Optional[str] = ''
    voice: Optional[str] = 'alice'
    language: Optional[str] = 'en-US'
    timeout: Optional[int] = 10
    num_digits: Optional[int] = 1
    destination: Optional[str] = ''
    record: Optional[bool] = False
    max_length: Optional[int] = 300
    pause_length: Optional[int] = 1


class CreateIVRNodeSchema(Schema):
    node_type: str
    name: str
    position: int = 0
    is_entry_point: bool = False
    config: IVRNodeConfigSchema = IVRNodeConfigSchema()


class CreateIVRTransitionSchema(Schema):
    from_node_id: str
    to_node_id: Optional[str] = None
    trigger: str


class CreateIVRFlowSchema(Schema):
    name: str
    description: Optional[str] = ''
    campaign_id: Optional[str] = None
    welcome_message: Optional[str] = ''
    language: Optional[str] = 'en-US'
    voice: Optional[str] = 'alice'


class UpdateIVRFlowSchema(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    campaign_id: Optional[str] = None
    welcome_message: Optional[str] = None
    language: Optional[str] = None
    voice: Optional[str] = None
    status: Optional[str] = None


class IVRNodeOutSchema(Schema):
    id: str
    node_type: str
    name: str
    position: int
    is_entry_point: bool
    config: dict
    transitions: List[dict] = []


class IVRFlowOutSchema(Schema):
    id: str
    name: str
    description: str
    status: str
    welcome_message: str
    language: str
    voice: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    organization_id: str
    nodes: List[IVRNodeOutSchema] = []
    created_at: str
    updated_at: str


class IVRFlowListSchema(Schema):
    id: str
    name: str
    status: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    created_at: str


class MessageResponseSchema(Schema):
    message: str
    success: bool = True
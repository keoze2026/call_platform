import uuid
from datetime import datetime
from typing import Optional
from ninja import Schema


# ─── Blacklist ────────────────────────────────────────────────────────────────

class BlacklistCreateSchema(Schema):
    phone_number: Optional[str] = None
    number: Optional[str] = None
    reason: str = 'manual'
    notes: str = ''
    campaign_id: Optional[uuid.UUID] = None
    expires_at: Optional[datetime] = None

    @property
    def resolved_number(self):
        return self.phone_number or self.number


class BlacklistUpdateSchema(Schema):
    phone_number: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    expires_at: Optional[datetime] = None


class BlacklistOutSchema(Schema):
    id: uuid.UUID
    phone_number: str
    reason: str
    notes: str
    is_active: bool
    campaign_id: Optional[uuid.UUID]
    expires_at: Optional[datetime]
    created_at: datetime


# ─── Whitelist ────────────────────────────────────────────────────────────────

class WhitelistCreateSchema(Schema):
    phone_number: str
    notes: str = ''
    campaign_id: Optional[uuid.UUID] = None


class WhitelistUpdateSchema(Schema):
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class WhitelistOutSchema(Schema):
    id: uuid.UUID
    phone_number: str
    notes: str
    is_active: bool
    campaign_id: Optional[uuid.UUID]
    created_at: datetime


# ─── Anonymous Call Block ─────────────────────────────────────────────────────

class AnonymousBlockCreateSchema(Schema):
    campaign_id: uuid.UUID
    is_active: bool = True


class AnonymousBlockUpdateSchema(Schema):
    is_active: bool


class AnonymousBlockOutSchema(Schema):
    id: uuid.UUID
    campaign_id: uuid.UUID
    is_active: bool
    created_at: datetime


# ─── Spam Reports ─────────────────────────────────────────────────────────────

class SpamReportOutSchema(Schema):
    id: uuid.UUID
    phone_number: str
    block_reason: str
    campaign_id: Optional[uuid.UUID]
    twilio_call_sid: str
    created_at: datetime


# ─── Generic ──────────────────────────────────────────────────────────────────

class MessageSchema(Schema):
    message: str
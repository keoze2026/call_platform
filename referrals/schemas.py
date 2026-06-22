from ninja import Schema
from typing import Optional, List


class ReferralProgramOutSchema(Schema):
    id: str
    code: str
    link: str
    commission_rate: str
    lifetime_earnings: str
    this_month_earnings: str


class ReferralOutSchema(Schema):
    id: str
    client_name: str
    vertical: str
    status: str
    spend_30d: str
    lifetime_spend: str
    commission_earned: str
    joined_at: str


class ReferralStatsSchema(Schema):
    total_referrals: int
    active_referrals: int
    commission_rate: str
    this_month_earnings: str


class SpendingTrackerSchema(Schema):
    date: str
    spend: str
    commission: str


class InviteSchema(Schema):
    email: str
    name: Optional[str] = ''


class MessageResponseSchema(Schema):
    message: str
    success: bool = True

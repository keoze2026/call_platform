from ninja import Schema
from typing import Optional, List, Union
from decimal import Decimal
from datetime import datetime
import uuid


# ─── Filters ─────────────────────────────────────────────────────────────────

class AnalyticsFilterSchema(Schema):
    date_from:    Optional[str] = None
    date_to:      Optional[str] = None
    created_at__gte: Optional[str] = None
    created_at__lte: Optional[str] = None
    start_date:   Optional[str] = None
    end_date:     Optional[str] = None
    campaign_id:  Optional[str] = None
    buyer_id:     Optional[str] = None
    publisher_id: Optional[str] = None
    status:       Optional[str] = None
    granularity:  Optional[str] = 'day'   # hour | day | week | month
    limit:        int = 100
    offset:       int = 0


# ─── Call Log ────────────────────────────────────────────────────────────────

class CallRecordSchema(Schema):
    id:               str
    twilio_call_sid:  str
    caller_number:    str
    caller_state:     str
    called_number:    str
    destination_number: Optional[str] = None
    destinationNumber: Optional[str] = None
    destination_number: Optional[str] = None
    destinationNumber: Optional[str] = None
    campaign_id:      Optional[str]
    campaign_name:    str
    buyer_id:         Optional[str]
    buyer_name:       str
    publisher_id:     Optional[str]
    publisher_name:   str
    status:           str
    duration_seconds: int
    is_converted:     bool
    is_duplicate:     bool
    is_spam:          bool
    revenue:          Decimal
    payout:           Decimal
    profit:           Decimal
    winning_bid:      Optional[Decimal]
    recording_url:    str
    started_at:       Optional[datetime]
    startedAt: Optional[Union[int, str]] = None
    ended_at:         Optional[datetime]
    created_at:       datetime
    ipqs_line_type:   Optional[str] = None


# ─── Dashboard ───────────────────────────────────────────────────────────────

class DashboardSchema(Schema):
    total_calls:       int
    calls_today:       int
    live_calls:        int
    completed_calls:   int
    converted_calls:   int
    conversion_rate:   float
    total_revenue:     Decimal
    total_payout:      Decimal
    total_profit:      Decimal
    avg_call_duration: float
    spam_blocked:      int
    duplicate_blocked: int
    # Account credit, so the header can show it without a second request.
    # Also served on its own at GET /api/billing/account.
    balance:           Decimal = Decimal('0')
    currency:          str = 'USD'


# ─── Time Series ─────────────────────────────────────────────────────────────

class TimeSeriesPointSchema(Schema):
    period:      str
    calls:       int
    converted:   int
    revenue:     Decimal
    payout:      Decimal
    profit:      Decimal
    avg_duration: float


# ─── Campaign Performance ────────────────────────────────────────────────────

class CampaignPerformanceSchema(Schema):
    campaign_id:      str
    campaign_name:    str
    total_calls:      int
    converted_calls:  int
    conversion_rate:  float
    total_revenue:    Decimal
    total_payout:     Decimal
    total_profit:     Decimal
    avg_duration:     float
    spam_blocked:     int


# ─── Buyer Performance ───────────────────────────────────────────────────────

class BuyerPerformanceSchema(Schema):
    buyer_id:      str
    buyer_name:    str
    total_calls:   int
    won_calls:     int
    avg_bid:       Decimal
    total_payout:  Decimal
    avg_duration:  float
    conversion_rate: float


# ─── Publisher Performance ───────────────────────────────────────────────────

class PublisherPerformanceSchema(Schema):
    publisher_id:    str
    publisher_name:  str
    total_calls:     int
    converted_calls: int
    conversion_rate: float
    total_revenue:   Decimal
    spam_rate:       float


# ─── Call Log List ───────────────────────────────────────────────────────────

class CallLogListSchema(Schema):
    total:  int
    offset: int
    limit:  int
    items:  List[CallRecordSchema]
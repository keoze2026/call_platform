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
    # Boolean filters used by the summary table's drill-downs, e.g.
    # /api/analytics/calls?is_qualified=true. Without these the query string was
    # ignored and the full list came back.
    is_qualified: Optional[bool] = None
    is_converted: Optional[bool] = None
    is_duplicate: Optional[bool] = None
    is_spam:      Optional[bool] = None

    # Which destination (tracking number's target) to report on. The dashboard's
    # "All destinations" dropdown had nothing behind it - the filter was never
    # accepted, so picking one changed nothing.
    destination: Optional[str] = None

    # IANA name, e.g. "America/New_York". Days and hours are bucketed in this
    # zone, and a bare date like "2026-09-28" means midnight-to-midnight there.
    # Without it everything was UTC, so an Eastern Time user saw a day that
    # started at 8pm the previous evening.
    timezone:     Optional[str] = None

    granularity:  Optional[str] = 'day'   # hour | day | week | month
    limit:        int = 100
    offset:       int = 0


# ─── Call Log ────────────────────────────────────────────────────────────────

class CallRecordSchema(Schema):
    id:               str
    twilio_call_sid:  str
    caller_number:    str
    caller_state:     str
    # Declared because Ninja serialises the response through this schema and
    # drops every key it does not name. _format_record has been sending these
    # since the mirror started carrying them; without a line here they were
    # removed on the way out and Caller Profile grouped every call as Unknown.
    caller_country:   Optional[str] = ''
    caller_city:      Optional[str] = ''
    caller_zip:       Optional[str] = ''
    caller_timezone:  Optional[str] = ''
    ipqs_fraud_score: Optional[int] = None
    # Same reason. _format_record sends all three and none was declared, so
    # the call list has never carried the carrier or the qualified verdict -
    # the Caller Profile carrier rows come from a separate breakdown endpoint,
    # which is why this went unnoticed.
    carrier:          Optional[str] = ''
    carrier_name:     Optional[str] = ''
    is_qualified:     Optional[bool] = None
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
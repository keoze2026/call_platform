from ninja import Schema
from typing import Optional, List
from datetime import datetime


# ── Pool ─────────────────────────────────────────────────────────────────────

class CreateDNIPoolSchema(Schema):
    name:                     str
    campaign_id:              Optional[str] = None
    description:              Optional[str] = ''
    session_duration_minutes: Optional[int] = 30
    fallback_number:          Optional[str] = ''
    allowed_domains:          Optional[List[str]] = []
    rotation_strategy:        Optional[str] = 'round-robin'
    country:                  Optional[str] = None
    closed_browser_delay_sec: Optional[int] = None
    idle_time_sec:            Optional[int] = None
    auto_buy:                 Optional[bool] = False
    replacement_number:       Optional[str] = None
    phone_number_format:      Optional[str] = 'E164'
    vendor_enabled:           Optional[bool] = False
    vendor_id:                Optional[str] = None
    traffic_sources_enabled:  Optional[bool] = False
    traffic_sources:          Optional[list] = []
    attached_number_ids:      Optional[List[str]] = None


class UpdateDNIPoolSchema(Schema):
    name:                     Optional[str] = None
    description:              Optional[str] = None
    session_duration_minutes: Optional[int] = None
    fallback_number:          Optional[str] = None
    allowed_domains:          Optional[List[str]] = None
    status:                   Optional[str] = None
    rotation_strategy:        Optional[str] = None
    country:                  Optional[str] = None
    closed_browser_delay_sec: Optional[int] = None
    idle_time_sec:            Optional[int] = None
    auto_buy:                 Optional[bool] = None
    replacement_number:       Optional[str] = None
    phone_number_format:      Optional[str] = None
    vendor_enabled:           Optional[bool] = None
    vendor_id:                Optional[str] = None
    traffic_sources_enabled:  Optional[bool] = None
    traffic_sources:          Optional[list] = None
    # CH-456: the portal's switch, campaign and the TFNs ticked on the page.
    active:                   Optional[bool] = None
    campaign_id:              Optional[str] = None
    attached_number_ids:      Optional[List[str]] = None


class AddNumberToPoolSchema(Schema):
    number:          Optional[str] = None
    phone_number_id: Optional[str] = None
    # CH-456: what the portal sends - the TFN's id from Phone Numbers.
    number_id:       Optional[str] = None


class DNINumberOutSchema(Schema):
    id:     str
    number: str
    status: str
    phone_number_id: Optional[str] = None


class DNIPoolOutSchema(Schema):
    id:                       str
    name:                     str
    status:                   str
    description:              str
    campaign_id:              Optional[str] = None
    campaign_name:            Optional[str] = None
    session_duration_minutes: int
    fallback_number:          str
    allowed_domains:          List[str]
    replacement_number:       Optional[str] = None
    phone_number_format:      Optional[str] = 'E164'
    vendor_enabled:           Optional[bool] = False
    vendor_id:                Optional[str] = None
    traffic_sources_enabled:  Optional[bool] = False
    traffic_sources:          Optional[list] = []
    numbers:                  List[DNINumberOutSchema] = []
    total_numbers:            int
    available_numbers:        int
    organization_id:          str
    created_at:               str
    updated_at:               str
    active:                   bool = True
    number_count:             int = 0
    attached_number_ids:      List[str] = []
    idle_time_sec:            int = 0
    calls_today:              int = 0
    numbers_in_use:           int = 0
    numbers_cooling:          int = 0
    numbers_free:             int = 0
    peak_visitors_7d:         int = 0
    fallback_today:           int = 0
    cooldown_minutes:         int = 10
    too_small:                bool = False


class DNIPoolListSchema(Schema):
    id:                str
    name:              str
    status:            str
    campaign_id:       Optional[str] = None
    campaign_name:     Optional[str] = None
    total_numbers:     int
    available_numbers: int
    created_at:        str


# ── Session / Number Assignment ───────────────────────────────────────────────

class AssignNumberSchema(Schema):
    """Sent by JS snippet to get a number for this visitor."""
    pool_id:      str
    visitor_id:   str
    utm_source:   Optional[str] = ''
    utm_medium:   Optional[str] = ''
    utm_campaign: Optional[str] = ''
    utm_term:     Optional[str] = ''
    utm_content:  Optional[str] = ''
    gclid:        Optional[str] = ''
    fbclid:       Optional[str] = ''
    referrer:     Optional[str] = ''
    landing_page: Optional[str] = ''


class AssignNumberResponseSchema(Schema):
    phone_number: str
    session_id:   str
    expires_at:   str


class DNISessionOutSchema(Schema):
    id:              str
    visitor_id:      str
    assigned_number: str
    utm_source:      str
    utm_medium:      str
    utm_campaign:    str
    utm_term:        str
    utm_content:     str
    gclid:           str
    fbclid:          str
    referrer:        str
    landing_page:    str
    status:          str
    expires_at:      datetime
    created_at:      datetime


# ── Misc ─────────────────────────────────────────────────────────────────────

class MessageResponseSchema(Schema):
    message: str
    success: bool = True
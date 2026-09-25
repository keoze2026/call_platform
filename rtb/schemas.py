import uuid
from decimal import Decimal
from datetime import datetime
from typing import Optional, List
from ninja import Schema


# ─── Ping Request (sent to buyers) ───────────────────────────────────────────

class PingRequestSchema(Schema):
    """Sent to each buyer endpoint when a call comes in."""
    auction_id    : uuid.UUID
    campaign_id   : uuid.UUID
    caller_number : str
    caller_state  : str
    call_time     : datetime


# ─── Bid Response (received from buyers) ─────────────────────────────────────

class BidResponseSchema(Schema):
    """Buyer responds with this to accept and place a bid.

    buyer_id identifies which bid is being set. Without it the endpoint had no
    way to tell bidders apart and wrote the amount onto every pending bid in the
    auction, so whoever posted last set the price for all of them.
    """
    auction_id : uuid.UUID
    buyer_id   : uuid.UUID
    bid_amount : Decimal
    accept     : bool = True


# ─── Auction Out ──────────────────────────────────────────────────────────────

class RTBBidOutSchema(Schema):
    id          : uuid.UUID
    buyer_id    : uuid.UUID
    bid_amount  : Optional[Decimal]
    status      : str
    response_ms : int
    created_at  : datetime


class RTBAuctionOutSchema(Schema):
    id              : uuid.UUID
    campaign_id     : uuid.UUID
    caller_number   : str
    caller_state    : str
    twilio_call_sid : str
    status          : str
    winning_bid     : Optional[Decimal]
    winner_id       : Optional[uuid.UUID]
    duration_ms     : int
    bids            : List[RTBBidOutSchema] = []
    created_at      : datetime


# ─── Generic ──────────────────────────────────────────────────────────────────

class MessageSchema(Schema):
    message: str
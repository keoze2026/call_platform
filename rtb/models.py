import uuid
from django.db import models


class RTBAuction(models.Model):
    """
    Created for every incoming call that triggers an RTB ping.
    Tracks the auction lifecycle from open to closed.
    """

    class Status(models.TextChoices):
        OPEN    = 'open',    'Open'
        CLOSED  = 'closed',  'Closed'
        TIMEOUT = 'timeout', 'Timeout'
        NO_BIDS = 'no_bids', 'No Bids'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField(db_index=True)

    campaign = models.ForeignKey(
        'campaigns.Campaign',
        on_delete=models.CASCADE,
        related_name='rtb_auctions'
    )

    caller_number   = models.CharField(max_length=20)
    caller_state    = models.CharField(max_length=10, blank=True, default='')
    twilio_call_sid = models.CharField(max_length=50, blank=True, default='')

    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    winning_bid     = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    winner = models.ForeignKey(
        'buyers.Buyer',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='won_auctions'
    )

    # How long we waited for bids in ms
    duration_ms = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'RTB Auction'
        verbose_name_plural = 'RTB Auctions'
        indexes = [
            models.Index(fields=['organization_id', 'created_at']),
            models.Index(fields=['campaign', 'status']),
        ]

    def __str__(self):
        return f'Auction [{self.caller_number}] — {self.status}'


class RTBBid(models.Model):
    """
    One bid per buyer per auction.
    Buyers respond to pings with a bid amount; highest valid bid wins.
    """

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        WON      = 'won',      'Won'
        LOST     = 'lost',     'Lost'
        TIMEOUT  = 'timeout',  'Timeout'
        REJECTED = 'rejected', 'Rejected'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id      = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    auction = models.ForeignKey(RTBAuction, on_delete=models.CASCADE, related_name='bids')

    buyer = models.ForeignKey(
        'buyers.Buyer',
        on_delete=models.CASCADE,
        related_name='rtb_bids'
    )

    # Amount buyer is willing to pay for this call
    bid_amount  = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    response_ms = models.IntegerField(default=0, help_text='How fast the buyer responded in ms')

    # Full raw response stored for debugging
    raw_response = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'RTB Bid'
        verbose_name_plural = 'RTB Bids'
        unique_together     = ('auction', 'buyer')
        indexes = [
            models.Index(fields=['auction', 'status']),
            models.Index(fields=['buyer', 'status']),
        ]

    def __str__(self):
        return f'Bid [{self.buyer}] ${self.bid_amount} — {self.status}'
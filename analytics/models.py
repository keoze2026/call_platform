from decimal import Decimal
import uuid
from django.db import models
from accounts.models import Organization


class CallRecord(models.Model):
    """
    Master analytics record created for every call.
    Denormalized for fast querying — no joins needed for reports.
    """

    class Status(models.TextChoices):
        COMPLETED   = 'completed',   'Completed'
        NO_ANSWER   = 'no_answer',   'No Answer'
        BUSY        = 'busy',        'Busy'
        FAILED      = 'failed',      'Failed'
        VOICEMAIL   = 'voicemail',   'Voicemail'
        IN_PROGRESS = 'in_progress', 'In Progress'

    class RoutingType(models.TextChoices):
        RTB        = 'rtb',        'RTB'
        PRIORITY   = 'priority',   'Priority'
        WEIGHTED   = 'weighted',   'Weighted'
        ROUND_ROBIN = 'round_robin', 'Round Robin'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='call_records')

    # Call identifiers
    twilio_call_sid   = models.CharField(max_length=50, blank=True, db_index=True)
    caller_number     = models.CharField(max_length=20, blank=True)
    caller_state      = models.CharField(max_length=10, blank=True)
    # The rest of what the lookup already paid for.
    #
    # RealValidito returns country, city, zip and timezone on every call and
    # the enrichment task stores all four on CallLog - but the mirror carried
    # only caller_state across, and the reports list endpoint reads this table.
    # So Caller Profile's Country, City, Zip code and Timezone groupings had no
    # column behind them, which is why the frontend filled them with invented
    # values until it was stripped back to "Not available".
    #
    # Lengths match routing.CallLog exactly so the mirror copy can never
    # truncate. (caller_state above is 10 against the call log's 50; the values
    # are two-letter codes, so it has not bitten yet.)
    caller_country    = models.CharField(max_length=50, blank=True, default='')
    caller_city       = models.CharField(max_length=100, blank=True, default='')
    caller_zip        = models.CharField(max_length=20, blank=True, default='')
    caller_timezone   = models.CharField(max_length=60, blank=True, default='')
    # Null, not 0. 0 is a real IPQS score meaning "clean", so a default of 0
    # would make every unscored call look verified. Null means "not scored".
    ipqs_fraud_score  = models.IntegerField(null=True, blank=True)
    called_number     = models.CharField(max_length=20, blank=True)
    # Where the call was sent. Absent from the mirror, so the dashboard's
    # "All destinations" dropdown had nothing to filter on.
    destination_number = models.CharField(max_length=20, blank=True, default='', db_index=True)
    # The amount charged for this call, copied from the call log rather than
    # recalculated, so reporting always equals the invoice.
    platform_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Campaign / buyer / publisher (denormalized — store IDs and names)
    campaign = models.ForeignKey(
        'campaigns.Campaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='campaign_id',
        related_name='+'
    )
    campaign_name = models.CharField(max_length=255, blank=True)

    buyer_id   = models.UUIDField(null=True, blank=True, db_index=True)
    buyer_name = models.CharField(max_length=255, blank=True)

    publisher_id   = models.UUIDField(null=True, blank=True, db_index=True)
    publisher_name = models.CharField(max_length=255, blank=True)

    @property
    def dynamic_revenue(self):
        if hasattr(self, '_dynamic_revenue'):
            return self._dynamic_revenue
        if not self.is_converted:
            return Decimal('0')
        if self.campaign_id and getattr(self, 'campaign', None):
            return self.campaign.revenue_amount
        return self.revenue

    @dynamic_revenue.setter
    def dynamic_revenue(self, value):
        self._dynamic_revenue = value

    @property
    def dynamic_payout(self):
        if hasattr(self, '_dynamic_payout'):
            return self._dynamic_payout
        if not self.is_converted:
            return Decimal('0')
        if self.campaign_id and getattr(self, 'campaign', None):
            return self.campaign.payout_amount
        return self.payout

    @dynamic_payout.setter
    def dynamic_payout(self, value):
        self._dynamic_payout = value

    @property
    def dynamic_profit(self):
        if hasattr(self, '_dynamic_profit'):
            return self._dynamic_profit
        return self.dynamic_revenue - self.dynamic_payout

    @dynamic_profit.setter
    def dynamic_profit(self, value):
        self._dynamic_profit = value

    # Call outcome
    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    routing_type    = models.CharField(max_length=20, choices=RoutingType.choices, blank=True)
    duration_seconds = models.IntegerField(default=0)
    billable_seconds = models.IntegerField(default=0)
    is_converted    = models.BooleanField(default=False)
    is_qualified    = models.BooleanField(default=False)
    is_duplicate    = models.BooleanField(default=False)
    is_spam         = models.BooleanField(default=False)
    recording_url   = models.URLField(blank=True)
    ipqs_line_type  = models.CharField(max_length=50, blank=True)
    carrier_name    = models.CharField(max_length=100, blank=True, db_index=True)
    carrier         = models.CharField(max_length=60, blank=True, db_index=True)

    # Financial
    revenue = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    payout  = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    profit  = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    # RTB data
    winning_bid  = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    auction_id   = models.UUIDField(null=True, blank=True)

    started_at  = models.DateTimeField(null=True, blank=True, db_index=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at    = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'call_records'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['organization', 'campaign_id', 'created_at']),
            models.Index(fields=['organization', 'buyer_id', 'created_at']),
            models.Index(fields=['organization', 'publisher_id', 'created_at']),
            models.Index(fields=['status', 'created_at']),
            # started_at is the field every report buckets and filters on -
            # CALL_TIME is Coalesce('started_at', 'created_at') - and it had no
            # index at all, so each one scanned the table.
            models.Index(fields=['organization', 'started_at'], name='callrec_org_started_idx'),
            models.Index(fields=['started_at'], name='callrec_started_idx'),
        ]

    def __str__(self):
        return f"{self.caller_number} → {self.campaign_name} ({self.status})"
from .scheduled_reports import ScheduledReport

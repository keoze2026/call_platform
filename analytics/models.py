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
    called_number     = models.CharField(max_length=20, blank=True)

    # Campaign / buyer / publisher (denormalized — store IDs and names)
    campaign_id   = models.UUIDField(null=True, blank=True, db_index=True)
    campaign_name = models.CharField(max_length=255, blank=True)

    buyer_id   = models.UUIDField(null=True, blank=True, db_index=True)
    buyer_name = models.CharField(max_length=255, blank=True)

    publisher_id   = models.UUIDField(null=True, blank=True, db_index=True)
    publisher_name = models.CharField(max_length=255, blank=True)

    # Call outcome
    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    routing_type    = models.CharField(max_length=20, choices=RoutingType.choices, blank=True)
    duration_seconds = models.IntegerField(default=0)
    billable_seconds = models.IntegerField(default=0)
    is_converted    = models.BooleanField(default=False)
    is_duplicate    = models.BooleanField(default=False)
    is_spam         = models.BooleanField(default=False)
    recording_url   = models.URLField(blank=True)

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
        ]

    def __str__(self):
        return f"{self.caller_number} → {self.campaign_name} ({self.status})"
from .scheduled_reports import ScheduledReport

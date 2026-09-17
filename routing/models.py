import uuid
from django.db import models
from accounts.models import Organization, User


class RoutingRule(models.Model):

    class RuleType(models.TextChoices):
        TIME_BASED = 'time_based', 'Time Based'
        GEO_BASED = 'geo_based', 'Geo Based'
        ROUND_ROBIN = 'round_robin', 'Round Robin'
        WEIGHTED = 'weighted', 'Weighted'
        PRIORITY = 'priority', 'Priority'
        CALLER_SPECIFIC = 'caller_specific', 'Caller Specific'
        OVERFLOW = 'overflow', 'Overflow'
        SCHEDULE_BASED = 'schedule_based', 'Schedule Based'
        TAG_BASED = 'tag_based', 'Tag Based'
        RTB = 'rtb', 'Real Time Bidding'
        PING_POST = 'ping_post', 'Ping Post'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='routing_rules')
    campaign = models.ForeignKey('campaigns.Campaign', on_delete=models.CASCADE, related_name='routing_rules')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_rules')

    name = models.CharField(max_length=255)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    priority = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'routing_rules'
        ordering = ['priority']

    def __str__(self):
        return f"{self.name} ({self.rule_type})"


class RuleCondition(models.Model):
    """Conditions for each routing rule stored as JSON"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(RoutingRule, on_delete=models.CASCADE, related_name='conditions')
    conditions = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rule_conditions'

    def __str__(self):
        return f"Conditions for {self.rule.name}"


class RuleDestination(models.Model):
    """Destination for each routing rule"""

    class DestinationType(models.TextChoices):
        PHONE = 'phone', 'Phone Number'
        SIP = 'sip', 'SIP Endpoint'
        BUYER = 'buyer', 'Buyer'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(RoutingRule, on_delete=models.CASCADE, related_name='destinations')
    destination_type = models.CharField(max_length=20, choices=DestinationType.choices, default=DestinationType.PHONE)
    destination = models.CharField(max_length=255)
    buyer = models.ForeignKey('buyers.Buyer', on_delete=models.SET_NULL, null=True, blank=True, related_name='rule_destinations')
    priority = models.IntegerField(default=1)
    weight = models.IntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rule_destinations'
        ordering = ['priority']

    def __str__(self):
        return f"{self.destination} ({self.destination_type})"


class CallLog(models.Model):
    """Complete record for every call"""

    class Status(models.TextChoices):
        RINGING = 'ringing', 'Ringing'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        NO_ANSWER = 'no_answer', 'No Answer'
        BUSY = 'busy', 'Busy'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='call_logs')
    campaign = models.ForeignKey('campaigns.Campaign', on_delete=models.SET_NULL, null=True, related_name='call_logs')
    routing_rule = models.ForeignKey(RoutingRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='call_logs')
    buyer = models.ForeignKey('buyers.Buyer', on_delete=models.SET_NULL, null=True, blank=True, related_name='call_logs')
    publisher = models.ForeignKey('publishers.Publisher', on_delete=models.SET_NULL, null=True, blank=True, related_name='call_logs')

    # Call details
    caller_number = models.CharField(max_length=20)
    called_number = models.CharField(max_length=20)
    destination_number = models.CharField(max_length=20, blank=True)
    twilio_call_sid = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RINGING)


    # IPQS data
    ipqs_checked = models.BooleanField(default=False)
    ipqs_fraud_score = models.IntegerField(null=True, blank=True)
    ipqs_is_voip = models.BooleanField(default=False)
    ipqs_line_type = models.CharField(max_length=50, blank=True)
    ipqs_block_reason = models.CharField(max_length=100, blank=True)

    # Why the call was not routed, for non-IPQS reasons (insufficient_balance,
    # campaign cap, no destination). Blank on calls that routed normally.
    block_reason = models.CharField(max_length=100, blank=True)
    
    # Tracking
    caller_area_code = models.CharField(max_length=5, blank=True)
    caller_state = models.CharField(max_length=50, blank=True)
    caller_country = models.CharField(max_length=50, blank=True)

    # Duration and cost
    duration = models.IntegerField(default=0)
    twilio_cost = models.DecimalField(max_digits=10, decimal_places=4, default=0.0000)
    buyer_payout = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    publisher_payout = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Recording
    recording_url = models.URLField(blank=True)
    recording_sid = models.CharField(max_length=100, blank=True)
    transcription = models.TextField(blank=True)

    # Tags and notes
    tags = models.JSONField(default=list)
    notes = models.TextField(blank=True)

    #transcription 
    transcription_text = models.TextField(blank=True, default='')
    transcription_status = models.CharField(max_length=20, default='pending')
    sentiment = models.CharField(max_length=20, blank=True, default='')
    sentiment_score = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'call_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['campaign', 'created_at']),
            models.Index(fields=['twilio_call_sid']),
            models.Index(fields=['called_number', 'status']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.caller_number} → {self.called_number} ({self.status})"
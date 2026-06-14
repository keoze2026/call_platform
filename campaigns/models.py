import uuid
from django.db import models
from accounts.models import User, Organization


class Campaign(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        ARCHIVED = 'archived', 'Archived'

    class RoutingType(models.TextChoices):
        PRIORITY = 'priority', 'Priority'
        PERCENTAGE = 'percentage', 'Percentage'
        ROUND_ROBIN = 'round_robin', 'Round Robin'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='campaigns')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_campaigns')

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    routing_type = models.CharField(max_length=20, choices=RoutingType.choices, default=RoutingType.PRIORITY)

    # Financial
    payout_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    revenue_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Call settings
    min_call_duration = models.IntegerField(default=0)  # seconds
    duplicate_call_block = models.BooleanField(default=False)
    duplicate_call_block_hours = models.IntegerField(default=24)

    class PayoutModel(models.TextChoices):
        PER_CALL = 'per_call', 'Per Call'
        PER_QUALIFIED = 'per_qualified', 'Per Qualified'
        PER_MINUTE = 'per_minute', 'Per Minute'

    class Vertical(models.TextChoices):
        HEALTH = 'health', 'Health'
        AUTO = 'auto', 'Auto'
        HOME = 'home', 'Home'
        FINANCE = 'finance', 'Finance'
        LEGAL = 'legal', 'Legal'
        INSURANCE = 'insurance', 'Insurance'
        OTHER = 'other', 'Other'

    vertical = models.CharField(max_length=50, choices=Vertical.choices, default='other', blank=True)
    payout_model = models.CharField(max_length=20, choices=PayoutModel.choices, default='per_call', blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Greeting and whisper
    greeting_message = models.TextField(blank=True)
    greeting_enabled = models.BooleanField(default=False)
    whisper_message = models.TextField(blank=True)
    whisper_enabled = models.BooleanField(default=False)

    # Auto SMS
    auto_sms_enabled = models.BooleanField(default=False)
    auto_sms_message = models.TextField(blank=True)

    # Recording
    recording_enabled = models.BooleanField(default=True)
    recording_storage = models.CharField(max_length=20, default='twilio')
    
    #bid floor 
    bid_floor = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Minimum acceptable bid amount in RTB auction')

    #time out 
    rtb_timeout_seconds = models.IntegerField(default=5, help_text='Seconds buyers have to respond to RTB ping (1-30)')

    # IPQS spam/VOIP protection
    ipqs_enabled = models.BooleanField(default=False)
    block_voip = models.BooleanField(default=False)
    block_risky = models.BooleanField(default=True)
    block_spammer = models.BooleanField(default=True)
    block_recent_abuse = models.BooleanField(default=True)
    block_invalid_numbers = models.BooleanField(default=True)
    max_fraud_score = models.IntegerField(default=85)
    
    # Call Queuing
    queue_enabled = models.BooleanField(default=False)
    queue_max_size = models.IntegerField(default=10)
    queue_max_wait_seconds = models.IntegerField(default=300)
    queue_music_url = models.URLField(blank=True, default='')
    queue_message = models.TextField(blank=True, default='All agents are busy. Please hold.')

    class Meta:
        db_table = 'campaigns'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.status})"


class CampaignCap(models.Model):

    class CapType(models.TextChoices):
        DAILY = 'daily', 'Daily'
        MONTHLY = 'monthly', 'Monthly'
        GLOBAL = 'global', 'Global'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.OneToOneField(Campaign, on_delete=models.CASCADE, related_name='cap')

    max_calls_daily = models.IntegerField(default=0)      # 0 = unlimited
    max_calls_monthly = models.IntegerField(default=0)    # 0 = unlimited
    max_calls_global = models.IntegerField(default=0)     # 0 = unlimited
    max_concurrency = models.IntegerField(default=0)      # 0 = unlimited

    class PayoutModel(models.TextChoices):
        PER_CALL = 'per_call', 'Per Call'
        PER_QUALIFIED = 'per_qualified', 'Per Qualified'
        PER_MINUTE = 'per_minute', 'Per Minute'

    class Vertical(models.TextChoices):
        HEALTH = 'health', 'Health'
        AUTO = 'auto', 'Auto'
        HOME = 'home', 'Home'
        FINANCE = 'finance', 'Finance'
        LEGAL = 'legal', 'Legal'
        INSURANCE = 'insurance', 'Insurance'
        OTHER = 'other', 'Other'

    vertical = models.CharField(max_length=50, choices=Vertical.choices, default='other', blank=True)
    payout_model = models.CharField(max_length=20, choices=PayoutModel.choices, default='per_call', blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'campaign_caps'

    def __str__(self):
        return f"Caps for {self.campaign.name}"


class CampaignSchedule(models.Model):

    DAYS = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='schedules')

    day_of_week = models.IntegerField(choices=DAYS)
    open_time = models.TimeField()
    close_time = models.TimeField()
    is_closed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'campaign_schedules'
        unique_together = ['campaign', 'day_of_week']
        ordering = ['day_of_week']

    def __str__(self):
        return f"{self.campaign.name} - {self.get_day_of_week_display()}"
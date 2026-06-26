import uuid
from django.db import models
from accounts.models import User, Organization


class Buyer(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        ARCHIVED = 'archived', 'Archived'

    class RoutingType(models.TextChoices):
        EXTERNAL = 'external', 'External Number'
        SIP = 'sip', 'SIP Endpoint'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='buyers')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_buyers')

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)


    #quality score 
    quality_score = models.IntegerField(default=50, help_text='0-100 quality score based on call outcomes')
    quality_score_updated_at = models.DateTimeField(null=True, blank=True)

    # How calls are delivered to this buyer
    dup_window_days = models.IntegerField(default=30, help_text='Days to block same caller from reaching this buyer again')
    routing_type = models.CharField(max_length=20, choices=RoutingType.choices, default=RoutingType.EXTERNAL)
    phone_number = models.CharField(max_length=20, blank=True)
    sip_endpoint = models.CharField(max_length=255, blank=True)
    rtb_endpoint = models.URLField(blank=True, default='')

    # Contact
    contact_name = models.CharField(max_length=255, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    payout_model = models.CharField(max_length=20, default='flat')

    # Financial
    payout_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Call settings
    min_call_duration = models.IntegerField(default=0)
    max_concurrency = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buyers'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.status})"


class BuyerCap(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.OneToOneField(Buyer, on_delete=models.CASCADE, related_name='cap')

    max_calls_daily = models.IntegerField(default=0)
    max_calls_monthly = models.IntegerField(default=0)
    max_calls_global = models.IntegerField(default=0)
    max_concurrency = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buyer_caps'

    def __str__(self):
        return f"Caps for {self.buyer.name}"


class BuyerCampaign(models.Model):
    """Links a buyer to a campaign with specific settings"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name='campaign_buyers')
    campaign = models.ForeignKey('campaigns.Campaign', on_delete=models.CASCADE, related_name='campaign_assignments')

    priority = models.IntegerField(default=1)
    weight = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'buyer_campaigns'
        unique_together = ['buyer', 'campaign']
        ordering = ['priority']

    def __str__(self):
        return f"{self.buyer.name} → {self.campaign.name}"




class BuyerSchedule(models.Model):
    """Weekly hours of operation for a buyer"""

    class DayOfWeek(models.IntegerChoices):
        MONDAY    = 0, 'Monday'
        TUESDAY   = 1, 'Tuesday'
        WEDNESDAY = 2, 'Wednesday'
        THURSDAY  = 3, 'Thursday'
        FRIDAY    = 4, 'Friday'
        SATURDAY  = 5, 'Saturday'
        SUNDAY    = 6, 'Sunday'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name='schedules')
    day_of_week = models.IntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    timezone = models.CharField(max_length=50, default='UTC')
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'buyer_schedules'
        unique_together = [('buyer', 'day_of_week')]

    def __str__(self):
        return f"{self.buyer.name} — {self.get_day_of_week_display()} {self.start_time}-{self.end_time}"
from .destination import Destination

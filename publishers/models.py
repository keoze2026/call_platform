import uuid
from django.db import models
from accounts.models import User, Organization


class Publisher(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        ARCHIVED = 'archived', 'Archived'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='publishers')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_publishers')

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # Contact info
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    # Financial
    payout_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # What this partner's own login may do, and what it may see of its reports.
    # The settings page offered both and saved neither - they lived in the
    # browser, so two people looking at the same publisher saw different
    # settings and the toggles gated nothing. Empty means "nobody has decided",
    # and the defaults in `accounts/partner_permissions.py` apply.
    permissions = models.JSONField(default=dict, blank=True)
    visible_report_columns = models.JSONField(default=list, blank=True)

    # Tracking
    unique_id = models.CharField(max_length=50, unique=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'publishers'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.unique_id:
            self.unique_id = f"PUB-{str(uuid.uuid4())[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.unique_id})"


class PublisherCap(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publisher = models.OneToOneField(Publisher, on_delete=models.CASCADE, related_name='cap')

    max_calls_daily = models.IntegerField(default=0)
    max_calls_monthly = models.IntegerField(default=0)
    max_calls_global = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'publisher_caps'

    def __str__(self):
        return f"Caps for {self.publisher.name}"


class PublisherCampaign(models.Model):
    """Links a publisher to a campaign"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publisher = models.ForeignKey(Publisher, on_delete=models.CASCADE, related_name='campaign_assignments')
    campaign = models.ForeignKey('campaigns.Campaign', on_delete=models.CASCADE, related_name='publisher_assignments')

    payout_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'publisher_campaigns'
        unique_together = ['publisher', 'campaign']

    def __str__(self):
        return f"{self.publisher.name} → {self.campaign.name}"
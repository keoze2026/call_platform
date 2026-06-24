import uuid
from django.db import models
from accounts.models import Organization, User


class Webhook(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'

    class Event(models.TextChoices):
        CALL_STARTED = 'call.started', 'Call Started'
        CALL_COMPLETED = 'call.completed', 'Call Completed'
        CALL_FAILED = 'call.failed', 'Call Failed'
        CALL_NO_ANSWER = 'call.no_answer', 'Call No Answer'
        CALL_CONVERTED = 'call.converted', 'Call Converted'
        RTB_AUCTION_CREATED = 'rtb.auction_created', 'RTB Auction Created'
        RTB_AUCTION_CLOSED = 'rtb.auction_closed', 'RTB Auction Closed'
        CAMPAIGN_PAUSED = 'campaign.paused', 'Campaign Paused'
        CAMPAIGN_CAP_REACHED = 'campaign.cap_reached', 'Campaign Cap Reached'
        BUYER_CAP_REACHED = 'buyer.cap_reached', 'Buyer Cap Reached'
        PUBLISHER_CAP_REACHED = 'publisher.cap_reached', 'Publisher Cap Reached'
        CRM_CONTACT_CREATED = 'crm.contact_created', 'CRM Contact Created'
        CRM_DEAL_CREATED = 'crm.deal_created', 'CRM Deal Created'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='webhooks')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_webhooks')

    name = models.CharField(max_length=255)
    url = models.URLField()
    secret = models.CharField(max_length=255, blank=True)
    headers = models.JSONField(default=list, blank=True)
    events = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # Retry settings
    max_retries = models.IntegerField(default=3)
    timeout_seconds = models.IntegerField(default=10)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'webhooks'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.url})"


class WebhookDelivery(models.Model):

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'
        RETRYING = 'retrying', 'Retrying'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name='deliveries')

    event = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    response_code = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    attempts = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'webhook_deliveries'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.event} → {self.webhook.url} ({self.status})"
    


class ConversionPixel(models.Model):
    """
    Postback URL fired by buyers when a call converts (sale, appointment, etc).
    Credits the publisher and updates call log.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, related_name='conversion_pixels')
    campaign = models.ForeignKey('campaigns.Campaign', on_delete=models.CASCADE, related_name='conversion_pixels', null=True, blank=True)

    name = models.CharField(max_length=255)
    token = models.CharField(max_length=64, unique=True, db_index=True, help_text='Unique token used in postback URL')
    conversion_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'conversion_pixels'

    def __str__(self):
        return f"{self.name} ({self.status})"


class ConversionEvent(models.Model):
    """Logs each conversion postback received."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pixel = models.ForeignKey(ConversionPixel, on_delete=models.CASCADE, related_name='events')
    call_log = models.ForeignKey('routing.CallLog', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversions')

    caller_number = models.CharField(max_length=20, blank=True)
    conversion_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    raw_payload = models.JSONField(default=dict)
    source_ip = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'conversion_events'
        ordering = ['-created_at']

    def __str__(self):
        return f"Conversion for {self.caller_number} (${self.conversion_value})"
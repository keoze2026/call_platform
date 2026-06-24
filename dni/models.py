import uuid
from django.db import models
from accounts.models import Organization


class DNIPool(models.Model):
    """
    A pool of phone numbers assigned to a campaign for number swapping.
    Each pool belongs to one campaign and holds multiple tracked numbers.
    """

    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        PAUSED   = 'paused',   'Paused'
        ARCHIVED = 'archived', 'Archived'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='dni_pools')

    campaign = models.ForeignKey(
        'campaigns.Campaign',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='dni_pools'
    )

    name        = models.CharField(max_length=255)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    description = models.TextField(blank=True)

    # How long a number is held for a visitor (in minutes)
    session_duration_minutes = models.IntegerField(default=30)

    # Default number to show if pool is exhausted
    fallback_number = models.CharField(max_length=20, blank=True)

    # Which website domains are allowed to use this pool
    allowed_domains = models.JSONField(default=list, blank=True)
    replacement_number = models.CharField(max_length=20, blank=True, default='')
    phone_number_format = models.CharField(max_length=20, default='E164')
    vendor_enabled = models.BooleanField(default=False)
    vendor_id = models.CharField(max_length=255, blank=True, null=True, default=None)
    traffic_sources_enabled = models.BooleanField(default=False)
    traffic_sources = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dni_pools'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.status})"


class DNINumber(models.Model):
    """
    A single tracked number inside a DNI pool.
    Each number can only be assigned to one active session at a time.
    """

    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        IN_USE    = 'in_use',    'In Use'
        DISABLED  = 'disabled',  'Disabled'

    id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pool   = models.ForeignKey(DNIPool, on_delete=models.CASCADE, related_name='numbers')
    number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)

    # Link to your PhoneNumber model
    phone_number = models.ForeignKey(
        'phone_numbers.PhoneNumber',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='dni_numbers'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'dni_numbers'

    def __str__(self):
        return f"{self.number} ({self.status})"


class DNISession(models.Model):
    """
    Created when a visitor lands on a page with the DNI snippet.
    Tracks which number was assigned to which visitor and their UTM data.
    Released when session expires or visitor calls.
    """

    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        EXPIRED  = 'expired',  'Expired'
        CALLED   = 'called',   'Called'
        RELEASED = 'released', 'Released'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pool       = models.ForeignKey(DNIPool, on_delete=models.CASCADE, related_name='sessions')
    dni_number = models.ForeignKey(DNINumber, on_delete=models.SET_NULL, null=True, related_name='sessions')

    # Visitor fingerprint
    visitor_id  = models.CharField(max_length=100, db_index=True)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    user_agent  = models.TextField(blank=True)

    # UTM tracking data
    utm_source   = models.CharField(max_length=255, blank=True)
    utm_medium   = models.CharField(max_length=255, blank=True)
    utm_campaign = models.CharField(max_length=255, blank=True)
    utm_term     = models.CharField(max_length=255, blank=True)
    utm_content  = models.CharField(max_length=255, blank=True)
    gclid        = models.CharField(max_length=255, blank=True)
    fbclid       = models.CharField(max_length=255, blank=True)
    referrer     = models.URLField(blank=True)
    landing_page = models.URLField(blank=True)

    # Assigned number (denormalized for quick lookup)
    assigned_number = models.CharField(max_length=20, blank=True)

    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    expires_at = models.DateTimeField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dni_sessions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['visitor_id', 'status']),
            models.Index(fields=['pool', 'status']),
            models.Index(fields=['assigned_number', 'status']),
            models.Index(fields=['expires_at', 'status']),
        ]

    def __str__(self):
        return f"Session {self.visitor_id} → {self.assigned_number}"
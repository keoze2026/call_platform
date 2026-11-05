import uuid
from django.db import models
from accounts.models import Organization


def default_capture_params():
    return ['gclid', 'msclkid', 'utm_source', 'utm_campaign', 'utm_term']


def new_public_key():
    import secrets
    return 'pk_' + secrets.token_hex(12)


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

    # CH-460 (Sir's number pool design, 31 Oct).
    # The key the website snippet carries (data-pool="pk_..."), not the id.
    public_key = models.CharField(max_length=40, unique=True, null=True, blank=True)
    country = models.CharField(max_length=2, default='US')
    # After a visit ends its number rests this long before the next visitor.
    cooldown_seconds = models.PositiveIntegerField(default=120)
    # How long a number stays held after the visitor closes the page.
    closed_browser_delay_seconds = models.PositiveIntegerField(default=30)
    # Which URL parameters of the landing page are kept with each call.
    capture_params = models.JSONField(default=default_capture_params, blank=True)
    # Spam-attack rules (see dni/protection.py RULES for the keys and defaults).
    protection = models.JSONField(default=dict, blank=True)
    # The last time the snippet asked for a number, and from which site.
    snippet_seen_at = models.DateTimeField(null=True, blank=True)
    snippet_seen_host = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dni_pools'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.public_key:
            self.public_key = new_public_key()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.status})"


class DNINumber(models.Model):
    """
    A single tracked number inside a DNI pool.
    Each number can only be assigned to one active session at a time.
    """

    class Status(models.TextChoices):
        AVAILABLE   = 'available',   'Available'
        IN_USE      = 'in_use',      'In Use'
        DISABLED    = 'disabled',    'Disabled'
        # CH-460: pulled out of rotation (by hand, or a call flood) until restored.
        QUARANTINED = 'quarantined', 'Quarantined'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pool   = models.ForeignKey(DNIPool, on_delete=models.CASCADE, related_name='numbers')
    number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    status_reason = models.CharField(max_length=255, blank=True, default='')
    status_changed_at = models.DateTimeField(null=True, blank=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True)

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

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
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

    # CH-460: Microsoft click id, sub id, and every captured URL parameter.
    msclkid = models.CharField(max_length=255, blank=True, default='')
    sub_id = models.CharField(max_length=255, blank=True, default='')
    captured = models.JSONField(default=dict, blank=True)
    # The pool was full: the visitor was shown the fallback number.
    overflow = models.BooleanField(default=False, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

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


class DNILander(models.Model):
    """A landing page that shows a pool's numbers (CH-460). The page is read
    once to find the phone numbers on it; the ticked ones are swapped."""
    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pool = models.ForeignKey(DNIPool, on_delete=models.CASCADE, related_name='landers')
    url = models.URLField(max_length=1000)
    # [{"number": "8885550100", "display": "1-888-555-0100", "where": "header a.btn-call", "label": "..."}]
    found = models.JSONField(default=list, blank=True)
    # The 10-digit numbers to swap on this page.
    swap = models.JSONField(default=list, blank=True)
    scan_error = models.CharField(max_length=255, blank=True, default='')
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    snippet_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'dni_landers'
        ordering = ['created_at']


class DNICall(models.Model):
    """Every call on a pool number (CH-460): which visit it came from, and
    whether the pool's spam rules refused it."""
    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id = models.BigAutoField(primary_key=True)
    pool = models.ForeignKey(DNIPool, on_delete=models.CASCADE, related_name='calls')
    number = models.ForeignKey(DNINumber, on_delete=models.SET_NULL, null=True, blank=True, related_name='calls')
    session = models.ForeignKey(DNISession, on_delete=models.SET_NULL, null=True, blank=True, related_name='calls')
    call_log = models.ForeignKey('routing.CallLog', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    caller_id = models.CharField(max_length=20, blank=True, default='', db_index=True)
    called = models.CharField(max_length=20, blank=True, default='')
    blocked_reason = models.CharField(max_length=40, blank=True, default='')
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    duration_sec = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'dni_calls'
        ordering = ['-started_at']
        indexes = [models.Index(fields=['pool', 'started_at']), models.Index(fields=['caller_id', 'started_at'])]


class DNIComplaint(models.Model):
    """A complaint about an advertiser (carrier, FTC, buyer, caller...). Kept
    per advertiser workspace, so it stays on record when a number is pulled."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='dni_complaints')
    tfn = models.CharField(max_length=20, blank=True, default='')
    source = models.CharField(max_length=60)
    notes = models.TextField(blank=True, default='')
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'dni_complaints'
        ordering = ['-received_at']


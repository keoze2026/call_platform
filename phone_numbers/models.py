import uuid
from django.db import models
from accounts.models import Organization, User


class PhoneNumber(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        RELEASED = 'released', 'Released'
        PENDING = 'pending', 'Pending'

    class NumberType(models.TextChoices):
        LOCAL = 'local', 'Local'
        TOLL_FREE = 'toll_free', 'Toll Free'
        MOBILE = 'mobile', 'Mobile'

    class Vendor(models.TextChoices):
        TWILIO = 'Twilio', 'Twilio'
        TELNYX = 'Telnyx', 'Telnyx'
        BANDWIDTH = 'Bandwidth', 'Bandwidth'
        VONAGE = 'Vonage', 'Vonage'
        OTHER = 'Other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='phone_numbers')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='purchased_numbers')

    # Number details
    number = models.CharField(max_length=20, unique=True)
    friendly_name = models.CharField(max_length=255, blank=True)
    number_type = models.CharField(max_length=20, choices=NumberType.choices, default=NumberType.LOCAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # Carrier/vendor
    twilio_sid = models.CharField(max_length=100, unique=True, null=True, blank=True)
    vendor = models.CharField(max_length=50, choices=Vendor.choices, default=Vendor.TWILIO)
    country_code = models.CharField(max_length=5, default='US')
    state = models.CharField(max_length=100, blank=True, default='')

    # Capacity & billing
    allocated_capacity = models.IntegerField(default=1)
    label = models.CharField(max_length=255, blank=True, default='')
    cap_enabled = models.BooleanField(default=False)
    daily_cap = models.IntegerField(default=0)
    monthly_cap = models.IntegerField(default=0)
    concurrency_enabled = models.BooleanField(default=False)
    concurrency_cap = models.IntegerField(default=0)
    vendor_enabled = models.BooleanField(default=False)
    payout_per_call = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payout_type = models.CharField(max_length=20, default='amount')
    payout_on = models.CharField(max_length=20, default='connected')
    dupe_revenue = models.CharField(max_length=20, default='disabled')
    dupe_revenue_days = models.IntegerField(default=0)
    traffic_source_enabled = models.BooleanField(default=False)
    traffic_source_id = models.CharField(max_length=255, blank=True, null=True, default=None)
    renews_at = models.DateTimeField(null=True, blank=True)

    # Assignment
    campaign = models.ForeignKey(
        'campaigns.Campaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='phone_numbers'
    )
    publisher = models.ForeignKey(
        'publishers.Publisher',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='phone_numbers'
    )

    # Capabilities
    voice_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)

    # Which carrier actually carries this number, and when it was put on a
    # campaign. Neither could be answered from the Numbers page before.
    carrier = models.ForeignKey(
        'phone_numbers.Carrier',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='phone_numbers',
    )
    assigned_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'phone_numbers'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.number} ({self.status})"


class Carrier(models.Model):
    """The carrier a number is actually carried by, with its short code.

    The vendor field above is where the number was bought - Twilio, or `Other`
    for one handed over by a carrier directly. It cannot say who carries the
    traffic, and with toll-frees coming from more than one carrier there was no
    way to tell from the Numbers page which one a call would ride.

    A table rather than choices on the model: a new carrier is added by the
    people using the platform, on the day they sign one, without a deploy.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='carriers',
    )
    name = models.CharField(max_length=100)
    # Short code shown on the Numbers page, e.g. KMQ.
    code = models.CharField(max_length=12)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Carrier'
        verbose_name_plural = 'Carriers'
        unique_together = ('organization', 'code')
        ordering = ['name']
        indexes = [
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self):
        return f'{self.name} ({self.code})'

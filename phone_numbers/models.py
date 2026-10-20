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
    # When the number was deleted (released). The user stops seeing it, but the
    # row stays so the owner can check the carrier's bill against it (CH-154).
    released_at = models.DateTimeField(null=True, blank=True)

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
    # When this number stops being usable, taken from the carrier's
    # lifetime_hours when the number is taken on. Null = no limit.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # The first call that landed on it. A single-use number is retired on the
    # strength of this, and it is the record of which call consumed it.
    used_at = models.DateTimeField(null=True, blank=True)
    used_by_call_id = models.CharField(max_length=64, blank=True, default='')

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

    # How this carrier's numbers behave. Policy belongs on the carrier because
    # it is the carrier's terms: a number is handed over for one call, or for a
    # day, and that is not something to re-enter per number or to fix in code.
    # single_use retires the number once a call has used it.
    single_use = models.BooleanField(default=False)
    # Hours a number stays usable from the moment it is taken on. 0 = no limit.
    lifetime_hours = models.IntegerField(default=0)
    # The owner's Oct 15 rule, mirroring the carrier's own: a number that has
    # taken this many ANSWERED calls is suspended at the nightly 1:30 AM
    # Eastern sweep - suspended overnight, never killed mid-day. 0 = off.
    max_answered_calls = models.IntegerField(default=0)
    # What the Risk column shows for this carrier's numbers. Empty = work it
    # out from the retirement rules above (CH-135). "high" or "low" overrides
    # that, for a carrier whose risk is not about expiry: High means its
    # numbers may be advertised publicly, Low means they only forward calls
    # from a publisher to a buyer. HYU XOLO is High with no expiry rule.
    risk_override = models.CharField(
        max_length=4, blank=True, default='',
        choices=[('', 'Auto'), ('high', 'High'), ('low', 'Low')],
    )
    # What this carrier charges US (owner-only, never shown to workspaces):
    # a monthly fee per number, a flat monthly portal fee for the carrier, and
    # a cost per connected minute. Used by the Owner > Carrier Costs page to
    # check the carrier's invoice (CH-154).
    number_monthly_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    portal_monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_minute_cost = models.DecimalField(max_digits=8, decimal_places=4, default=0)
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

    @property
    def risk(self) -> str:
        """'high' or 'low' for this carrier's numbers (the Risk column).

        An explicit setting wins. Otherwise High when any retirement rule can
        take a number - single use, a lifetime, the nightly answered sweep -
        and Low when nothing ever expires it.
        """
        if self.risk_override in ('high', 'low'):
            return self.risk_override
        if self.single_use or self.lifetime_hours or self.max_answered_calls:
            return 'high'
        return 'low'

    def __str__(self):
        return f'{self.name} ({self.code})'

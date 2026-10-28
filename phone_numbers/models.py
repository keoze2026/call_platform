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
    # Opted in to the automatic morning test call (CH-175).
    daily_test_enabled = models.BooleanField(default=False)

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
    # Why the platform retired the number (CH-179): 'used' (single use),
    # 'expired' (its lifetime ran out) or the nightly answered-calls rule.
    # Empty for a number somebody deleted by hand - that is how the "TFNs
    # deleted" notice tells the platform's retirements from a person's.
    retired_reason = models.CharField(max_length=255, blank=True, default='')
    # What the carrier charges for this TFN (CH-189). Sir, 21 Oct: "as soon as
    # someone adds TFNs with code, the system automatically adds the price as
    # well". Copied from the carrier's TFN price the moment the number gets a
    # carrier, however it was added, and kept: a later change of the carrier's
    # price applies to numbers added after it, never rewrites past bills.
    # Null = the carrier had no price yet; it is filled once one is set.
    # Owner-only, like every carrier cost.
    tfn_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'phone_numbers'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.number} ({self.status})"

    def save(self, *args, **kwargs):
        # Price the number from its carrier when it has none yet (CH-189).
        if self.carrier_id and self.tfn_price is None:
            price = (
                Carrier.objects.filter(pk=self.carrier_id)
                .values_list('number_monthly_cost', flat=True).first()
            )
            if price:
                self.tfn_price = price
                fields = kwargs.get('update_fields')
                if fields is not None and 'tfn_price' not in fields:
                    kwargs['update_fields'] = list(fields) + ['tfn_price']
        super().save(*args, **kwargs)


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
    # The threshold matrix (Sir, 20 Oct): a number that took MORE than this
    # many ANSWERED calls on one Eastern calendar day is retired at the
    # nightly 1:30 AM Eastern sweep - never mid-day. This many or fewer keeps
    # it live, and days are never added together. While it is set the
    # lifetime_hours clock does not apply. 0 = off.
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
    # How the carrier rounds each call's minutes (CH-254): 1 = exact seconds,
    # 6 = up to the next 6 seconds, 60 = up to the whole minute.
    BILLING_INCREMENTS = (1, 6, 30, 60)
    billing_increment = models.PositiveSmallIntegerField(default=1)
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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Numbers taken on while this carrier had no TFN price get it now
        # (CH-189). Numbers already priced keep the price they were added at.
        price_unpriced_numbers(self)


def price_unpriced_numbers(carrier) -> int:
    """Give every number of `carrier` that has no TFN price the carrier's
    current one. Returns how many were priced. Nothing when it has no price."""
    if not carrier.number_monthly_cost:
        return 0
    return PhoneNumber.objects.filter(carrier=carrier, tfn_price__isnull=True).update(
        tfn_price=carrier.number_monthly_cost,
    )



class TfnTest(models.Model):
    """One check that a tracking number is alive (CH-175).

    A real test places a call from our own Asterisk out to the number through
    the carrier. If the carrier hands it back to us, the number is live at the
    carrier, and the platform then checks that the number, its campaign and its
    routing would take the call. No CallLog row, no billing, no buyer is rung.
    Where the Asterisk manager link is not configured, only the platform half
    runs and the row says so (mode = platform_check).
    """

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        PASSED = 'passed', 'Passed'
        FAILED = 'failed', 'Failed'

    class Mode(models.TextChoices):
        REAL_CALL = 'real_call', 'Real call'
        PLATFORM_CHECK = 'platform_check', 'Platform check'
        # A test call started inside our own Asterisk, entering the inbound
        # dialplan exactly where a carrier call does (CH-239). Proves our
        # switch, routing and campaign take the number; nothing goes to the
        # carrier, so it says nothing about carrier delivery.
        INTERNAL_CALL = 'internal_call', 'Internal call'

    class Trigger(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        MORNING = 'morning', 'Morning'
        # A number with no recent calls, tested by the scheduled sweep (CH-213).
        AUTO = 'auto', 'Automatic'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tfn_tests')
    phone_number = models.ForeignKey(PhoneNumber, on_delete=models.CASCADE, related_name='tests')
    number = models.CharField(max_length=20, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.PLATFORM_CHECK)
    trigger = models.CharField(max_length=10, choices=Trigger.choices, default=Trigger.MANUAL)
    reason = models.CharField(max_length=255, blank=True, default='')
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # What the test did, in order, for the screen (CH-239):
    # [{"key": "placed", "ok": true, "text": "...", "at": "<iso>"}, ...]
    steps = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['phone_number', '-created_at'])]


class TfnCheckLink(models.Model):
    """A private link to the TFN Check page (CH-213), like XOLO's number lookup.

    Whoever holds the link can paste numbers and see whether each one is
    working, without logging in. The token is the only key, so it is long and
    random, and a link can be cancelled at any time. A link made for a
    publisher answers only for that publisher's numbers; every other number
    reads "Not Found", exactly as if it were not ours.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tfn_check_links')
    name = models.CharField(max_length=100)
    token = models.CharField(max_length=64, unique=True)
    # Deleting the publisher deletes its links: a link must never widen to the
    # whole workspace because the record it was limited to went away.
    publisher = models.ForeignKey('publishers.Publisher', on_delete=models.CASCADE,
                                  null=True, blank=True, related_name='tfn_check_links')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({"revoked" if self.revoked_at else "active"})'


class TfnCheckEvent(models.Model):
    """One number checked once on TFN Check (CH-342, Sir 28 Oct).

    The page lists today's checks until 12 AM Eastern, so whoever checks
    numbers - on the portal or from a share link - can see how many times each
    one was checked and tested today. Only the number and the result the
    checker saw are kept: nothing a share link may not already show.
    """
    class Source(models.TextChoices):
        PORTAL = 'portal', 'Portal'
        LINK = 'link', 'Share link'

    id = models.BigAutoField(primary_key=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tfn_check_events')
    number = models.CharField(max_length=10)
    status = models.CharField(max_length=16)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.PORTAL)
    # A share link or a publisher login sees only its own checks.
    link = models.ForeignKey(TfnCheckLink, on_delete=models.CASCADE, null=True, blank=True, related_name='events')
    publisher = models.ForeignKey('publishers.Publisher', on_delete=models.CASCADE,
                                  null=True, blank=True, related_name='+')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'created_at'])]


class RoutingRate(models.Model):
    """What we pay to route a call on to a buyer (CH-186).

    Every call forwarded to a buyer leaves through the termination carrier
    (XOLO, Sir 21 Oct: "this charge is for TFN termination", $0.004 a minute
    plus $350 a month). That cost has nothing to do with which carrier the
    tracking number came from, so it is not a Carrier field: it is one
    platform-wide rate, kept here and edited on Owner > Carrier Costs.
    """
    key = models.CharField(max_length=32, unique=True, default='termination')
    name = models.CharField(max_length=100, default='XOLO TFN termination')
    per_minute_cost = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # How the termination carrier rounds each call: 1 (exact seconds), 6, 30
    # or 60, as Carrier.billing_increment (CH-334). Exact until its invoice
    # shows otherwise.
    billing_increment = models.PositiveSmallIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name}: {self.per_minute_cost}/min + {self.monthly_fee}/month'

import datetime
import uuid
from django.db import models
from accounts.models import User, Organization


def _all_report_columns():
    """CH-224: a new partner starts with every report column visible. Imported
    here, when a record is created, not at load time, so the apps stay free of
    an import cycle."""
    from accounts.partner_permissions import all_report_columns
    return all_report_columns()


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

    # What this buyer's own logins may do, and the zone their reports count
    # days in. Controlled by the workspace admin; enforced server-side - a
    # hidden button is not a permission system (frontend spec, boss request).
    # Blank = the portal's time zone (CH-260); set only when the buyer
    # counts its days in a zone of its own.
    timezone = models.CharField(max_length=64, blank=True, default='')
    can_block_numbers = models.BooleanField(default=False)
    can_download_reports = models.BooleanField(default=False)
    payout_model = models.CharField(max_length=20, default='flat')

    # Financial
    payout_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # What this partner's own login may do, and what it may see of its reports.
    # Same fields as Publisher, because a buyer login is the same kind of thing:
    # an outside company with an account inside this workspace. Empty means
    # nobody has decided, and the defaults in
    # `accounts/partner_permissions.py` apply.
    permissions = models.JSONField(default=dict, blank=True)
    visible_report_columns = models.JSONField(default=_all_report_columns, blank=True)

    # Call settings
    min_call_duration = models.IntegerField(default=0)
    max_concurrency = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buyers'
        ordering = ['-created_at']
        constraints = [
            # Two buyers called `RNY` existed in one workspace, which makes any
            # report naming one of them ambiguous. Enforced in the database so
            # it cannot come back through the UI, the API, the admin or a
            # script - the places a validation check in one of them would miss.
            models.UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_buyer_name_per_organization',
            ),
        ]

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


class BuyerTelegramReport(models.Model):
    """The buyer's own daily report, sent to its Telegram (CH-258, Sir 24 Oct).

    At send_time (Eastern) the platform builds one Excel file per destination
    of the buyer, with that destination's calls, and sends each to chat_id.
    The buyer's choices are applied to every report: no recording links,
    connected calls only, no Fail Reason column, its own column headings, and
    its own wording for comments (wording only - a comment can never be made
    to say a call connected when it did not).
    """
    buyer = models.OneToOneField(Buyer, on_delete=models.CASCADE, related_name='telegram_report')
    enabled = models.BooleanField(default=False)
    chat_id = models.CharField(max_length=64, blank=True, default='')
    # Eastern. A time before 06:00 reports the whole previous day; any later
    # time reports the day so far.
    send_time = models.TimeField(default=datetime.time(21, 0))
    no_recording_link = models.BooleanField(default=False)
    connected_only = models.BooleanField(default=False)
    hide_fail_reason = models.BooleanField(default=False)
    # The columns in the files (CH-267); empty = the first set (Date, Caller
    # ID, Destination, Status, Duration, Fail Reason, Recording).
    included_columns = models.JSONField(default=list, blank=True)
    # {"status": "Call Status", ...} - the report's column headings.
    column_names = models.JSONField(default=dict, blank=True)
    # {"No Destination": "Line Busy", ...} - the wording of Fail Reason comments.
    comment_rewrites = models.JSONField(default=dict, blank=True)

    # The buyer's own consent (CH-259, Sir 25 Oct): a report goes out only
    # when the buyer's account has ticked "send my report to Telegram", and
    # it can untick it at any time. Set from the buyer's own login only.
    buyer_approved = models.BooleanField(default=False)
    buyer_approved_at = models.DateTimeField(null=True, blank=True)
    buyer_approved_by = models.CharField(max_length=254, blank=True, default='')

    last_sent_on = models.DateField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=255, blank=True, default='')
    attempts_on = models.DateField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buyer_telegram_reports'

    def __str__(self):
        return f"{self.buyer.name} report at {self.send_time}"

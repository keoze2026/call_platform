import uuid
from django.db import models
from accounts.models import Organization, User


class NotificationRule(models.Model):

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'

    class Event(models.TextChoices):
        CALL_MISSED = 'call.missed', 'Call Missed'
        CALL_COMPLETED = 'call.completed', 'Call Completed'
        CAMPAIGN_CAP_REACHED = 'campaign.cap_reached', 'Campaign Cap Reached'
        BUYER_CAP_REACHED = 'buyer.cap_reached', 'Buyer Cap Reached'
        PUBLISHER_CAP_REACHED = 'publisher.cap_reached', 'Publisher Cap Reached'
        LOW_BALANCE = 'low.balance', 'Low Balance'
        CAMPAIGN_PAUSED = 'campaign.paused', 'Campaign Paused'
        DAILY_SUMMARY = 'daily.summary', 'Daily Summary'
        NEW_CALL = 'call.started', 'New Call'
        DESTINATION_CAP_REACHED = 'destination.cap_reached', 'Destination Cap Reached'
        BUYER_MISSED = 'buyer.missed', 'Buyer Missed Call'
        LOW_AHT = 'aht.low', 'Average Handle Time Dropped'
        NUMBER_DELETED = 'number.deleted', 'TFN Deleted'
        AUTOPILOT_ALERT = 'autopilot.alert', 'Autopilot Alert'
        NUMBER_NOT_WORKING = 'number.not_working', 'TFN Not Working'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='notification_rules')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_notification_rules')

    name = models.CharField(max_length=255)
    event = models.CharField(max_length=50, choices=Event.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.EMAIL)
    recipients = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notification_rules'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.event})"


class NotificationLog(models.Model):

    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        PENDING = 'pending', 'Pending'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='notification_logs')
    rule = models.ForeignKey(NotificationRule, on_delete=models.SET_NULL, null=True, related_name='logs')

    event = models.CharField(max_length=50)
    channel = models.CharField(max_length=20)
    recipient = models.CharField(max_length=255)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notification_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'created_at'], name='notiflog_org_created_idx'),
        ]

    def __str__(self):
        return f"{self.event} → {self.recipient} ({self.status})"


class NotificationPreference(models.Model):
    """Which alerts a user wants surfaced as a pop-up.

    Separate from NotificationRule: a rule decides whether an event is dispatched
    at all and to whom by email or SMS, this decides whether it interrupts the
    person looking at the dashboard. One person wanting cap alerts on top should
    not change what anyone else receives.

    popup_events holds NotificationRule.Event values. An empty list means no
    pop-ups; the defaults below are what a new user starts with.
    """

    DEFAULT_POPUP_EVENTS = [
        NotificationRule.Event.CAMPAIGN_CAP_REACHED,
        NotificationRule.Event.BUYER_CAP_REACHED,
        NotificationRule.Event.DESTINATION_CAP_REACHED,
        NotificationRule.Event.LOW_BALANCE,
    ]

    # CH-335 (Sir, 28 Oct): "Buyer and Publisher should only get according to
    # their access in pop up alert, not as main account". A buyer login is
    # offered only the alerts about its own calls, caps and destinations; a
    # publisher login only its own calls, cap and TFNs. Campaign caps, the
    # workspace's balance, campaign pauses, AHT and Autopilot belong to the
    # main account.
    PARTNER_EVENTS = {
        'buyer': [
            NotificationRule.Event.NEW_CALL,
            NotificationRule.Event.CALL_COMPLETED,
            NotificationRule.Event.BUYER_MISSED,
            NotificationRule.Event.BUYER_CAP_REACHED,
            NotificationRule.Event.DESTINATION_CAP_REACHED,
            NotificationRule.Event.DAILY_SUMMARY,
        ],
        'publisher': [
            NotificationRule.Event.NEW_CALL,
            NotificationRule.Event.CALL_COMPLETED,
            NotificationRule.Event.CALL_MISSED,
            NotificationRule.Event.PUBLISHER_CAP_REACHED,
            NotificationRule.Event.DAILY_SUMMARY,
            NotificationRule.Event.NUMBER_DELETED,
            NotificationRule.Event.NUMBER_NOT_WORKING,
        ],
    }
    PARTNER_DEFAULTS = {
        'buyer': [NotificationRule.Event.BUYER_CAP_REACHED, NotificationRule.Event.DESTINATION_CAP_REACHED],
        'publisher': [NotificationRule.Event.PUBLISHER_CAP_REACHED],
    }

    @classmethod
    def partner_kind(cls, user):
        """'buyer' or 'publisher' for a partner login, else None (a superuser
        is never a partner, as everywhere else)."""
        if getattr(user, 'is_superuser', False):
            return None
        role = getattr(user, 'role', '')
        return role if role in cls.PARTNER_EVENTS else None

    @classmethod
    def allowed_events(cls, user) -> list:
        """The alert types this login may see and switch, in catalogue order."""
        order = [v for v, _ in NotificationRule.Event.choices]
        kind = cls.partner_kind(user)
        if kind is None:
            return order
        allowed = {str(e) for e in cls.PARTNER_EVENTS[kind]}
        return [v for v in order if v in allowed]

    @classmethod
    def defaults_for(cls, user) -> list:
        kind = cls.partner_kind(user)
        events = cls.PARTNER_DEFAULTS[kind] if kind else cls.DEFAULT_POPUP_EVENTS
        return [str(e) for e in events]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='notification_preference'
    )

    popups_enabled = models.BooleanField(
        default=True, help_text='Master switch for pop-up alerts'
    )
    popup_events = models.JSONField(
        default=list, blank=True,
        help_text='Event types that surface as a pop-up. Empty means none.'
    )
    sound_enabled = models.BooleanField(default=False)
    # The newest TFN retirement this user has acknowledged (CH-179). The
    # "TFNs deleted" notice lists retirements after it and stays until the
    # user clicks Seen, on every device. Null = never acknowledged.
    deleted_tfns_seen_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notification_preferences'

    def __str__(self):
        return f"Pop-up preferences for {self.user.email}"

    def wants_popup(self, event: str) -> bool:
        return self.popups_enabled and event in (self.popup_events or [])

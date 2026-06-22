import uuid
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Blacklist(models.Model):
    """
    Blocked numbers — rejected before routing.
    Scoped org-wide or narrowed to a single campaign.
    """

    class Reason(models.TextChoices):
        SPAM         = 'spam',             'Spam'
        FRAUD        = 'fraud',            'Fraud'
        MANUAL       = 'manual',           'Manually Added'
        AUTO         = 'auto_detected',    'Auto Detected'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField(db_index=True)

    # Optional: restrict block to one campaign only
    campaign = models.ForeignKey(
        'campaigns.Campaign',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='blacklisted_numbers',
        help_text='Leave blank to block org-wide.'
    )

    phone_number = models.CharField(max_length=20, db_index=True)
    reason       = models.CharField(max_length=20, choices=Reason.choices, default=Reason.MANUAL)
    notes        = models.TextField(blank=True, default='')
    is_active    = models.BooleanField(default=True, db_index=True)
    expires_at   = models.DateTimeField(null=True, blank=True, help_text='Null = permanent block')

    added_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='blacklist_entries'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Blacklist Entry'
        verbose_name_plural = 'Blacklist Entries'
        unique_together     = ('organization_id', 'campaign', 'phone_number')
        indexes = [
            models.Index(fields=['organization_id', 'phone_number', 'is_active']),
            models.Index(fields=['organization_id', 'campaign', 'phone_number', 'is_active']),
        ]

    def __str__(self):
        scope = f'campaign:{self.campaign_id}' if self.campaign_id else 'org-wide'
        return f'Blacklist [{self.phone_number}] ({scope})'


class Whitelist(models.Model):
    """
    Trusted numbers — bypass all spam checks including blacklist.
    Useful for internal test numbers and VIP callers.
    """

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField(db_index=True)

    campaign = models.ForeignKey(
        'campaigns.Campaign',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='whitelisted_numbers',
        help_text='Leave blank to whitelist org-wide.'
    )

    phone_number = models.CharField(max_length=20, db_index=True)
    notes        = models.TextField(blank=True, default='')
    is_active    = models.BooleanField(default=True, db_index=True)

    added_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='whitelist_entries'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Whitelist Entry'
        verbose_name_plural = 'Whitelist Entries'
        unique_together     = ('organization_id', 'campaign', 'phone_number')
        indexes = [
            models.Index(fields=['organization_id', 'phone_number', 'is_active']),
        ]

    def __str__(self):
        scope = f'campaign:{self.campaign_id}' if self.campaign_id else 'org-wide'
        return f'Whitelist [{self.phone_number}] ({scope})'


class AnonymousCallBlock(models.Model):
    """
    Per-campaign setting to block calls with no caller ID (anonymous/private numbers).
    """

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField(db_index=True)

    campaign = models.OneToOneField(
        'campaigns.Campaign',
        on_delete=models.CASCADE,
        related_name='anonymous_call_block'
    )

    is_active = models.BooleanField(
        default=False,
        help_text='When True, calls with no caller ID are rejected.'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Anonymous Call Block'
        verbose_name_plural = 'Anonymous Call Blocks'

    def __str__(self):
        status = 'enabled' if self.is_active else 'disabled'
        return f'AnonymousBlock [{self.campaign}] ({status})'


class SpamReport(models.Model):
    """
    Audit log created each time a call is blocked by spam protection.
    Useful for analytics and reviewing false positives.
    """

    class BlockReason(models.TextChoices):
        BLACKLISTED = 'blacklisted',  'Blacklisted Number'
        ANONYMOUS   = 'anonymous',    'Anonymous Call'
        DUPLICATE   = 'duplicate',    'Duplicate Call'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField(db_index=True)

    campaign = models.ForeignKey(
        'campaigns.Campaign',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='spam_reports'
    )

    phone_number  = models.CharField(max_length=20, db_index=True)
    block_reason  = models.CharField(max_length=20, choices=BlockReason.choices)
    twilio_call_sid = models.CharField(max_length=50, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name        = 'Spam Report'
        verbose_name_plural = 'Spam Reports'
        indexes = [
            models.Index(fields=['organization_id', 'created_at']),
            models.Index(fields=['phone_number', 'organization_id']),
        ]

    def __str__(self):
        return f'SpamReport [{self.phone_number}] — {self.block_reason}'

class Shield(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, related_name='shields')
    name = models.CharField(max_length=255)
    shield_type = models.CharField(max_length=20)
    campaign_ids = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'spam_shields'

    def __str__(self):
        return f"{self.name} ({self.shield_type})"

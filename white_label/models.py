import uuid
from django.db import models
from accounts.models import Organization, User


class WhiteLabel(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='white_label')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_white_labels')

    # Branding
    company_name = models.CharField(max_length=255, blank=True)
    logo_url = models.URLField(blank=True)
    favicon_url = models.URLField(blank=True)
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=20, blank=True)
    website_url = models.URLField(blank=True)

    # Colors
    primary_color = models.CharField(max_length=7, default='#2563eb')
    secondary_color = models.CharField(max_length=7, default='#1e40af')
    accent_color = models.CharField(max_length=7, default='#3b82f6')
    background_color = models.CharField(max_length=7, default='#ffffff')
    text_color = models.CharField(max_length=7, default='#111827')

    # Custom CSS
    custom_css = models.TextField(blank=True)

    # Email templates
    email_header = models.TextField(blank=True)
    email_footer = models.TextField(blank=True)
    email_from_name = models.CharField(max_length=255, blank=True)
    email_from_address = models.EmailField(blank=True)

    # Feature flags
    features = models.JSONField(default=dict)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'white_labels'

    def __str__(self):
        return f"{self.company_name} ({self.organization})"


class WhiteLabelDomain(models.Model):

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACTIVE = 'active', 'Active'
        FAILED = 'failed', 'Failed'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    white_label = models.ForeignKey(WhiteLabel, on_delete=models.CASCADE, related_name='domains')
    domain = models.CharField(max_length=255, unique=True)
    is_primary = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'white_label_domains'

    def __str__(self):
        return f"{self.domain} ({self.status})"
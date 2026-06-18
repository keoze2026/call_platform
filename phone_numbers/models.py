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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='phone_numbers')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='purchased_numbers')

    # Number details
    number = models.CharField(max_length=20, unique=True)
    friendly_name = models.CharField(max_length=255, blank=True)
    number_type = models.CharField(max_length=20, choices=NumberType.choices, default=NumberType.LOCAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # Twilio details
    twilio_sid = models.CharField(max_length=100, unique=True, null=True, blank=True, default=None)
    country_code = models.CharField(max_length=5, default='US')

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'phone_numbers'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.number} ({self.status})"
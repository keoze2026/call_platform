import uuid
import random
import string
from django.db import models
from accounts.models import Organization, User


def generate_referral_code():
    chars = string.ascii_uppercase + string.digits
    return 'AVRTYX-' + ''.join(random.choices(chars, k=6))


class ReferralProgram(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='referral_program')
    code = models.CharField(max_length=20, unique=True, default=generate_referral_code)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=5.00)
    lifetime_earnings = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    this_month_earnings = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'referral_programs'

    def __str__(self):
        return f"{self.organization.name} — {self.code}"

    @property
    def link(self):
        return f"https://avortyx.io/r/{self.code}"


class Referral(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        CHURNED = 'churned', 'Churned'
        PENDING = 'pending', 'Pending'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(ReferralProgram, on_delete=models.CASCADE, related_name='referrals')
    referred_organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE,
        related_name='referred_by', null=True, blank=True
    )
    client_name = models.CharField(max_length=255)
    vertical = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    spend_30d = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    lifetime_spend = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    commission_earned = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'referrals'
        ordering = ['-joined_at']

    def __str__(self):
        return f"{self.client_name} via {self.program.code}"


class ReferralEarning(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(ReferralProgram, on_delete=models.CASCADE, related_name='earnings')
    referral = models.ForeignKey(Referral, on_delete=models.CASCADE, related_name='earnings')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'referral_earnings'
        ordering = ['-created_at']

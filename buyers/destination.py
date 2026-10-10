from django.db import models
import uuid


class Destination(models.Model):

    class ForwardType(models.TextChoices):
        NUMBER = 'number', 'Phone Number'
        SIP = 'sip', 'SIP Endpoint'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey('buyers.Buyer', on_delete=models.SET_NULL, null=True, blank=True, related_name='destinations')
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, related_name='destinations')
    name = models.CharField(max_length=200)
    tfn = models.CharField(max_length=30)
    forward_type = models.CharField(max_length=20, choices=ForwardType.choices, default=ForwardType.NUMBER)
    enabled = models.BooleanField(default=True)
    concurrency_cap = models.IntegerField(default=0)
    hourly_cap = models.IntegerField(default=0)
    daily_cap = models.IntegerField(default=0)
    monthly_cap = models.IntegerField(default=0)
    global_cap = models.IntegerField(default=0)
    live_calls = models.IntegerField(default=0)
    hourly_calls = models.IntegerField(default=0)
    daily_calls = models.IntegerField(default=0)
    monthly_calls = models.IntegerField(default=0)
    global_calls = models.IntegerField(default=0)
    ring_duration_sec = models.IntegerField(default=30)
    timezone = models.CharField(max_length=100, default='America/New_York')
    filter_enabled = models.BooleanField(default=False)
    filter_groups = models.JSONField(default=list)
    business_hours_enabled = models.BooleanField(default=False)
    business_hour_slots = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'destinations'
        ordering = ['-created_at']
        constraints = [
            # Calls are attributed by an exact match on this number, so two live
            # destinations sharing one means every buyer holding it is credited
            # with the same call and every cap counts it. Enabled only: the
            # disabled rows are a record of numbers that were used before, and
            # are worth keeping.
            models.UniqueConstraint(
                fields=['organization', 'tfn'],
                condition=models.Q(enabled=True),
                name='unique_enabled_destination_tfn_per_organization',
            ),
            # The one-live-destination-per-buyer constraint stood here until
            # the boss reversed the rule (CH-098). It existed because the
            # hangup-side lookup took `.first()` of the buyer's live rows,
            # making a second one silently dead; that lookup is now
            # `pick_live_destination` - cap-aware rotation across every live
            # TFN - so the constraint's reason is gone and migration 0018
            # dropped it from the database.
        ]

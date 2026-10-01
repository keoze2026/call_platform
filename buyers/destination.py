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
            # One live destination per buyer.
            #
            # This is not a new restriction - it is the rule the platform has
            # always followed without saying so. The handler that routes a call
            # resolves the buyer's destination with
            #
            #     Destination.objects.filter(buyer=..., enabled=True)
            #         .order_by('-created_at').first()
            #
            # so a buyer with two enabled destinations has every call sent to
            # whichever was created last, and the other is dead. The interface
            # let you create it, show it as active, and watch it never ring.
            #
            # Enforcing it here makes that lookup correct by construction: there
            # is only ever one row to find. The alternative was changing the
            # routing handler, which is not worth the risk when the same fault
            # can be removed from the data side.
            models.UniqueConstraint(
                fields=['buyer'],
                condition=models.Q(enabled=True, buyer__isnull=False),
                name='one_enabled_destination_per_buyer',
            ),
        ]

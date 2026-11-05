import uuid
from django.db import models
from accounts.models import Organization, User


class IVRFlow(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        DRAFT = 'draft', 'Draft'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='ivr_flows')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_flows')
    campaign = models.ForeignKey(
        'campaigns.Campaign',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ivr_flows'
    )

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    welcome_message = models.TextField(blank=True)
    language = models.CharField(max_length=10, default='en-US')
    voice = models.CharField(max_length=50, default='alice')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ivr_flows'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.status})"


class IVRNode(models.Model):

    class NodeType(models.TextChoices):
        SAY = 'say', 'Say'
        GATHER = 'gather', 'Gather'
        DIAL = 'dial', 'Dial'
        HANGUP = 'hangup', 'Hangup'
        TRANSFER = 'transfer', 'Transfer'
        VOICEMAIL = 'voicemail', 'Voicemail'
        RECORD = 'record', 'Record'
        PAUSE = 'pause', 'Pause'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(IVRFlow, on_delete=models.CASCADE, related_name='nodes')

    node_type = models.CharField(max_length=20, choices=NodeType.choices)
    name = models.CharField(max_length=255)
    position = models.IntegerField(default=0)
    is_entry_point = models.BooleanField(default=False)

    # Configuration stored as JSON
    config = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ivr_nodes'
        ordering = ['position']

    def __str__(self):
        return f"{self.name} ({self.node_type})"


class IVRNodeTransition(models.Model):

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_node = models.ForeignKey(IVRNode, on_delete=models.CASCADE, related_name='transitions')
    to_node = models.ForeignKey(IVRNode, on_delete=models.CASCADE, related_name='incoming_transitions', null=True, blank=True)

    # What input triggers this transition e.g. "1", "2", "timeout", "no_input"
    trigger = models.CharField(max_length=50)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ivr_node_transitions'
        unique_together = ['from_node', 'trigger']

    def __str__(self):
        return f"{self.from_node.name} --{self.trigger}--> {self.to_node.name if self.to_node else 'END'}" 



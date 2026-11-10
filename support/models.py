from django.db import models
import uuid


class SupportSession(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, null=True,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'support_sessions'
        ordering = ['-created_at']


class SupportMessage(models.Model):
    class Sender(models.TextChoices):
        VISITOR = 'visitor', 'Visitor'
        AGENT = 'agent', 'Agent'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, null=True,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(SupportSession, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=20, choices=Sender.choices)
    message = models.TextField(blank=True)
    # CH-654: a picture with the message (pasted or picked in the portal's
    # chat, or a photo the team answered with in Telegram), up to 2 MB. Kept
    # here, not on disk, so the nightly database backup has it like the text.
    # Never loaded with the transcript (defer); served on its own address.
    image = models.BinaryField(null=True, blank=True, editable=False)
    image_type = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'support_messages'
        ordering = ['created_at']

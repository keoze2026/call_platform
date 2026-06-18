from django.db import models
import uuid


class SupportSession(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(SupportSession, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=20, choices=Sender.choices)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'support_messages'
        ordering = ['created_at']

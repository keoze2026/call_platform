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
    # CH-669: who is writing, to know the same person in a new chat (the chat
    # code changes with every new chat): internet address, the browser's own
    # random id (kept in that browser), and the browser / device it says it is.
    client_ip = models.CharField(max_length=45, blank=True, default='', db_index=True)
    browser_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    # CH-669: greetings from this person are not sent to the team: 'day' until
    # greeting_block_until (24 h), or 'forever'. Real questions always go.
    greeting_block = models.CharField(max_length=8, blank=True, default='')
    greeting_block_until = models.DateTimeField(null=True, blank=True)
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
    # CH-669: a visitor message that is only a greeting ("hi", "hello" ...),
    # and one kept here but not sent to the team (greetings blocked).
    greeting = models.BooleanField(default=False)
    held = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'support_messages'
        ordering = ['created_at']

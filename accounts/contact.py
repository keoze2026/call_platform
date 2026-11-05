from django.db import models
import uuid


class ContactMessage(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'New'
        READ = 'read', 'Read'
        REPLIED = 'replied', 'Replied'
        SPAM = 'spam', 'Spam'

    # CH-578: the account this row belongs to, set on every save from its
    # parent (accounts/tenancy.py).
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, null=True,
                                     blank=True, editable=False, related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    email = models.EmailField(max_length=255)
    message = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contact_messages'
        ordering = ['-created_at']

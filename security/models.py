"""The reports PIN: one per workspace, enforced by the server.

The PIN existed only in the browser's localStorage, in plain digits. It
protected nothing - it was per browser, buyers and publishers never had it, and
the data was readable straight from the API by anyone with a token. A lock that
the thing being locked does not know about is not a lock.

So the PIN lives here, hashed, and `security/enforcement.py` makes the API
refuse historical data without it.
"""
import uuid

from django.db import models


class ReportsPin(models.Model):
    """One PIN per organisation. Never stored or returned in the clear."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        'accounts.Organization', on_delete=models.CASCADE, related_name='reports_pin'
    )
    pin_hash = models.CharField(max_length=128)
    # Bumped on every set, change and remove. An unlock only counts while its
    # version matches, so changing the PIN invalidates every open unlock in the
    # workspace at once - without having to find and delete them.
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        'accounts.User', null=True, on_delete=models.SET_NULL, related_name='+'
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reports_pins'

    def __str__(self):
        return f'Reports PIN for {self.organization} (v{self.version})'


class ReportsPinUnlock(models.Model):
    """One row per unlocked login session.

    Bound to the session rather than the user so signing in on a second device
    does not inherit an unlock from the first.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='reports_pin_unlocks')
    session_key = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reports_pin_unlocks'
        unique_together = [('user', 'session_key')]
        indexes = [
            models.Index(fields=['user', 'session_key'], name='pinunlock_user_session_idx'),
            models.Index(fields=['expires_at'], name='pinunlock_expires_idx'),
        ]

    def __str__(self):
        return f'{self.user.email} unlocked until {self.expires_at}'


class ReportsPinAttempt(models.Model):
    """Wrong-PIN counter, per user.

    A 4-digit PIN is 10,000 combinations. Without a limit it is guessable in
    minutes by anyone holding a token.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField('accounts.User', on_delete=models.CASCADE, related_name='reports_pin_attempt')
    failed_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reports_pin_attempts'

    def __str__(self):
        return f'{self.user.email}: {self.failed_count} failed'

"""CH-675: Message client - what the platform owner sends to an account from
the Organizations page, shown to the account's people at the top of their
portal until each one clicks Seen.

Stored as the owner action in the account's own activity log
(accounts/owner_audit.py, OWNER_MESSAGE; the text in metadata['message']), so
nothing new is kept and the log shows it too, from "Avortyx". Seen is per
login (NotificationPreference.owner_messages_seen_at), on every device.
Buyer and publisher logins are not shown them: they are for the account.
"""
from django.utils import timezone

KEEP_DAYS = 30
LIMIT = 5


def _can_see(user) -> bool:
    return (getattr(user, 'organization_id', None) is not None
            and getattr(user, 'role', '') not in ('buyer', 'publisher'))


def _seen_at(user):
    from notifications.models import NotificationPreference
    pref = NotificationPreference.objects.filter(user=user).only('owner_messages_seen_at').first()
    return pref.owner_messages_seen_at if pref else None


def unseen_for(user) -> dict:
    empty = {'unseen_count': 0, 'messages': [], 'newest_at': None}
    if not _can_see(user):
        return empty
    from datetime import timedelta
    from accounts.models import ActivityLog
    from accounts.owner_audit import OWNER_MESSAGE
    qs = ActivityLog.objects.filter(organization_id=user.organization_id, action=OWNER_MESSAGE,
                                    created_at__gte=timezone.now() - timedelta(days=KEEP_DAYS))
    seen = _seen_at(user)
    if seen:
        qs = qs.filter(created_at__gt=seen)
    rows = list(qs.order_by('-created_at')[:LIMIT])
    return {
        'unseen_count': qs.count(),
        'messages': [{'id': str(r.id), 'at': r.created_at.isoformat(), 'from': 'Avortyx',
                      'text': (r.metadata or {}).get('message', '') if isinstance(r.metadata, dict) else ''}
                     for r in rows],
        'newest_at': rows[0].created_at.isoformat() if rows else None,
    }


def mark_seen(user, up_to=None) -> dict:
    """Seen up to `up_to` (the newest message the user was shown), so one that
    arrives while the bar is open is not swallowed."""
    if not _can_see(user):
        return unseen_for(user)
    from notifications.models import NotificationPreference
    now = timezone.now()
    stamp = up_to if up_to and up_to <= now else now
    pref, _ = NotificationPreference.objects.get_or_create(
        user=user, defaults={'popup_events': list(NotificationPreference.DEFAULT_POPUP_EVENTS)})
    if pref.owner_messages_seen_at is None or stamp > pref.owner_messages_seen_at:
        pref.owner_messages_seen_at = stamp
        pref.save(update_fields=['owner_messages_seen_at', 'updated_at'])
    return unseen_for(user)

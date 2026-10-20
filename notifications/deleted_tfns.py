"""The "TFNs deleted" notice (CH-179).

Sir, 21 Oct: once a used TFN is deleted, the TFNs must show under
notifications - "these TFNs are deleted, add other TFNs" - and the notice
must stay until the user has seen it.

Lists the numbers the platform itself retired (single use, lifetime, or the
nightly answered-calls rule) since the user last clicked Seen. Numbers a
person deleted by hand are not listed: they know already. "Seen" is stored
per user on the server, so the notice is gone on every device once
acknowledged, and back the moment another number is retired.

Reads only. Nothing here decides when a number is retired.
"""
from datetime import timedelta

from django.utils import timezone

# A user who has never clicked Seen gets the last few days, not the whole
# history of the workspace.
FIRST_LOOK_DAYS = 3
LIMIT = 200


def _can_see(user) -> bool:
    if getattr(user, 'role', '') in ('buyer', 'publisher'):
        # Partners: a retired number is no longer theirs, and retirement
        # clears the publisher on it, so there is nothing of theirs to show.
        return False
    if getattr(user, 'organization_id', None) is None:
        return False
    from accounts.workspace_permissions import has_perm
    return has_perm(user, 'tracking-numbers', 'view')


def reason_label(reason: str) -> str:
    r = (reason or '').strip()
    if r == 'used':
        return 'Used (single use)'
    if r == 'expired':
        return 'Time limit reached'
    if 'answered calls' in r:
        # "12 answered calls on 2026-10-20, over the limit of 10, nightly 1:30 AM rule"
        return 'Over the daily answered-call limit (' + r.split(',')[0] + ')'
    return r[:1].upper() + r[1:] if r else 'Retired'


def _seen_at(user):
    from notifications.models import NotificationPreference
    pref = NotificationPreference.objects.filter(user=user).only('deleted_tfns_seen_at').first()
    return pref.deleted_tfns_seen_at if pref else None


def unseen_for(user) -> dict:
    empty = {'count': 0, 'numbers': [], 'seen_at': None, 'newest_at': None}
    if not _can_see(user):
        return empty

    from phone_numbers.models import PhoneNumber

    seen_at = _seen_at(user)
    since = seen_at or (timezone.now() - timedelta(days=FIRST_LOOK_DAYS))
    qs = (
        PhoneNumber.objects
        .filter(organization_id=user.organization_id, status=PhoneNumber.Status.RELEASED,
                released_at__gt=since)
        .exclude(retired_reason='')
        .select_related('carrier')
        .order_by('-released_at')
    )
    count = qs.count()
    rows = list(qs[:LIMIT])
    return {
        'count': count,
        'numbers': [
            {
                'id': str(n.id),
                'number': n.number,
                'name': n.label or n.friendly_name or '',
                'carrier_code': n.carrier.code if n.carrier_id else '',
                'reason': reason_label(n.retired_reason),
                'retired_at': n.released_at.isoformat(),
            }
            for n in rows
        ],
        'seen_at': seen_at.isoformat() if seen_at else None,
        'newest_at': rows[0].released_at.isoformat() if rows else None,
    }


def mark_seen(user, up_to=None) -> dict:
    """Acknowledge every retirement up to `up_to` (the newest one the user was
    shown), so a number retired while the notice was open is not swallowed.
    Without it, everything up to now."""
    from notifications.models import NotificationPreference

    now = timezone.now()
    stamp = up_to if up_to and up_to <= now else now
    pref, _ = NotificationPreference.objects.get_or_create(
        user=user,
        defaults={'popup_events': list(NotificationPreference.DEFAULT_POPUP_EVENTS)},
    )
    if pref.deleted_tfns_seen_at is None or stamp > pref.deleted_tfns_seen_at:
        pref.deleted_tfns_seen_at = stamp
        pref.save(update_fields=['deleted_tfns_seen_at', 'updated_at'])
    return unseen_for(user)

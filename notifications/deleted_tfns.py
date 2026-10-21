"""The "TFNs deleted" notice (CH-179, CH-182).

Sir, 21 Oct: once a used TFN is deleted, the TFNs must show under
notifications - "these TFNs are deleted, add other TFNs".
Sir, 21 Oct (CH-182): the notice disappeared once someone clicked Seen; keep
it under notifications until 12 PM Eastern.

So a retired TFN stays listed until the first 12:00 PM Eastern after it was
retired. The nightly 1:30 AM sweep's numbers therefore stay all morning and
drop off at noon by themselves; a number retired after noon stays until noon
the next day. "Seen" no longer hides anything: it only acknowledges, which
clears the red count on the bell and the pop-up banner. It is stored per user
on the server.

Lists the numbers the platform itself retired (single use, lifetime, or the
nightly answered-calls rule). Numbers a person deleted by hand are not listed:
they know already.

Reads only. Nothing here decides when a number is retired.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

EASTERN = ZoneInfo('America/New_York')
# The notice is kept until this Eastern time of day.
KEEP_UNTIL = time(12, 0)
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


def window(now=None):
    """(start, end) of what is listed now: retirements from the last 12:00 PM
    Eastern up to now, shown until the next 12:00 PM Eastern."""
    now = now or timezone.now()
    local = now.astimezone(EASTERN)
    noon_today = datetime.combine(local.date(), KEEP_UNTIL, tzinfo=EASTERN)
    if local >= noon_today:
        start, end = noon_today, noon_today + timedelta(days=1)
    else:
        start, end = noon_today - timedelta(days=1), noon_today
    # Built with the zone of the date it falls on, so a DST change is honoured.
    start = datetime.combine(start.date(), KEEP_UNTIL, tzinfo=EASTERN)
    end = datetime.combine(end.date(), KEEP_UNTIL, tzinfo=EASTERN)
    return start, end


def _seen_at(user):
    from notifications.models import NotificationPreference
    pref = NotificationPreference.objects.filter(user=user).only('deleted_tfns_seen_at').first()
    return pref.deleted_tfns_seen_at if pref else None


def unseen_for(user) -> dict:
    """The notice as this user sees it now. `numbers` is everything kept until
    12 PM; `unseen_count` is the part retired after the user last clicked Seen."""
    empty = {'count': 0, 'unseen_count': 0, 'numbers': [], 'seen_at': None,
             'newest_at': None, 'visible_until': None}
    if not _can_see(user):
        return empty

    from phone_numbers.models import PhoneNumber

    start, end = window()
    seen_at = _seen_at(user)
    qs = (
        PhoneNumber.objects
        .filter(organization_id=user.organization_id, status=PhoneNumber.Status.RELEASED,
                released_at__gte=start)
        .exclude(retired_reason='')
        .select_related('carrier')
        .order_by('-released_at')
    )
    count = qs.count()
    unseen = qs.filter(released_at__gt=seen_at).count() if seen_at else count
    rows = list(qs[:LIMIT])
    return {
        'count': count,
        'unseen_count': unseen,
        'numbers': [
            {
                'id': str(n.id),
                'number': n.number,
                'name': n.label or n.friendly_name or '',
                'carrier_code': n.carrier.code if n.carrier_id else '',
                'reason': reason_label(n.retired_reason),
                'retired_at': n.released_at.isoformat(),
                'seen': bool(seen_at and n.released_at <= seen_at),
            }
            for n in rows
        ],
        'seen_at': seen_at.isoformat() if seen_at else None,
        'newest_at': rows[0].released_at.isoformat() if rows else None,
        'visible_until': end.isoformat() if count else None,
    }


def mark_seen(user, up_to=None) -> dict:
    """Acknowledge every retirement up to `up_to` (the newest one the user was
    shown), so a number retired while the notice was open is not swallowed.
    Without it, everything up to now. The list itself stays until 12 PM."""
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

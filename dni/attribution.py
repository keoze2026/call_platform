"""Tie a call on a number-pool number back to the website visit (CH-452).

A visitor's page asks /api/dni/assign for a number; the pool hands out one of
its numbers and keeps the visit (UTM tags, Google / Facebook click id,
referrer, landing page) in a DNISession for 30 minutes. Nothing tied the call
back to that visit: the session was never marked "called", the number stayed
held for the rest of the 30 minutes, and the call showed no ad source.

`mark_called` runs in the caller-lookup worker, after the call is routed -
never on the call path. `visit_for_call` reads the visit back for the call's
detail panel. No new columns: the session already has the number it handed
out and the time it was valid.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

AD_FIELDS = (
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'gclid', 'fbclid', 'referrer', 'landing_page',
)


def _last10(number: str) -> str:
    return ''.join(c for c in (number or '') if c.isdigit())[-10:]


def _sessions_for(call):
    """Visits on this call's number in this call's workspace, newest first."""
    from dni.models import DNISession

    digits = _last10(call.called_number)
    if len(digits) != 10 or not call.organization_id:
        return DNISession.objects.none()
    return (
        DNISession.objects
        .filter(pool__organization_id=call.organization_id, assigned_number__endswith=digits)
        .select_related('pool', 'dni_number')
        .order_by('-created_at')
    )


def mark_called(call) -> bool:
    """The visit holding this number when the call came in is marked called
    and its number goes back to the pool. True when a visit was found."""
    from dni.models import DNINumber, DNISession

    when = call.created_at or timezone.now()
    session = _sessions_for(call).filter(
        status=DNISession.Status.ACTIVE, created_at__lte=when, expires_at__gte=when,
    ).first()
    if session is None:
        return False
    session.status = DNISession.Status.CALLED
    session.save(update_fields=['status', 'updated_at'])
    if session.dni_number_id:
        DNINumber.objects.filter(pk=session.dni_number_id, status=DNINumber.Status.IN_USE).update(
            status=DNINumber.Status.AVAILABLE)
    return True


def visit_for_call(call):
    """The website visit this call came from, or None.

    A call up to the pool's grace time after the visit ended still belongs to
    that visit (CH-460, Sir's design: 10 minutes) - with the cool-down nobody
    else was shown the number in between.
    """
    from datetime import timedelta
    from dni.models import DNISession
    from dni.protection import rules

    when = call.created_at
    if when is None:
        return None
    candidates = _sessions_for(call).filter(
        status__in=[DNISession.Status.ACTIVE, DNISession.Status.CALLED, DNISession.Status.EXPIRED,
                    DNISession.Status.RELEASED],
        created_at__lte=when, overflow=False,
    )[:5]
    for s in candidates:
        grace = timedelta(seconds=rules(s.pool)['call_grace_sec'])
        if s.expires_at >= when - grace:
            return s
    return None


def visit_event(call):
    """The "Website Visit" line for the call's activity timeline, or None."""
    try:
        visit = visit_for_call(call)
    except Exception:
        logger.exception('could not read the website visit for call %s', getattr(call, 'id', ''))
        return None
    if visit is None:
        return None
    detail = {'pool': visit.pool.name if visit.pool_id else None}
    for f in AD_FIELDS:
        detail[f] = getattr(visit, f, '') or None
    return {
        'event': 'website_visit',
        'label': 'Website Visit',
        'at': visit.created_at.isoformat(),
        'detail': detail,
    }

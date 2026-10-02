"""Check a caller against the do-not-call registers, after the call.

The TCPA Shield has existed in the interface since the beginning with nothing
behind it. The lookup was written in September, wired into `route_call`, and
took every call down on 30 September when the provider was slow: the request
never completed, so nothing reached the access log and the platform simply
stopped answering. It was removed from the call path that day and has been
connected to nothing since.

This is the version that cannot do that.

**It runs after the call has ended**, in the Celery worker that already looks up
the caller's carrier. A call is never delayed by it and never refused by it. The
worst case is a flag arriving a few seconds late.

What it is worth, given it cannot block the call it checked:

  the record     every call to a listed number is flagged with the register it
                 came from, so the exposure is visible rather than discovered in
                 a letter from a lawyer
  the next one   a number found on the register is added to the local blacklist,
                 and `RoutingEngine.is_blacklisted` already reads that - from
                 the database, with no external request. So the caller is
                 refused on their *second* call, at routing, with nothing on the
                 call path.

A TCPA litigator is the case that matters most, and they rarely call once.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def check_and_record(call_log) -> dict:
    """Look up one caller and record what came back.

    Returns the fields to write onto the CallLog, or an empty dict when the
    check did not run. Never raises: a compliance flag is worth having and is
    never worth losing the enrichment it travels with.
    """
    if not getattr(settings, 'DNC_CHECK_ENABLED', False):
        return {}

    number = (call_log.caller_number or '').strip()
    if not number:
        return {}

    try:
        from spam_protection.realvalidito import DNCLookup
        result = DNCLookup.check(number)
    except Exception:
        logger.exception('dnc lookup failed for %s', number)
        return {}

    if not result.get('checked'):
        # No credits, no credentials, or their service is down. Nothing is
        # recorded rather than recording a clean result we did not get - a
        # false "not listed" is the expensive direction to be wrong.
        return {}

    if not result.get('listed'):
        return {'is_dnc': False, 'dnc_reason': ''}

    reason = (result.get('reason') or 'DNC')[:80]
    logger.warning(
        'caller %s is on a do-not-call register (%s) - call %s',
        number, reason, call_log.id,
    )

    _blacklist(call_log, number, reason)

    return {'is_dnc': True, 'dnc_reason': reason}


def _blacklist(call_log, number, reason):
    """Stop the next call from this number, with no external request.

    `RoutingEngine.is_blacklisted` reads this table at routing time. Writing
    here means the second call from a listed number is refused locally, which
    is the protection the call path can afford.
    """
    from spam_protection.models import Blacklist

    try:
        entry, created = Blacklist.objects.get_or_create(
            organization_id=call_log.organization_id,
            campaign=None,          # org-wide: a litigator is not one campaign's problem
            phone_number=number,
            defaults={
                'reason': Blacklist.Reason.AUTO,
                'is_active': True,
                'notes': f'Added automatically: {reason}. '
                         f'First seen on call {call_log.id}.',
            },
        )
        if created:
            logger.warning('blacklisted %s org-wide after a DNC hit (%s)', number, reason)
        elif not entry.is_active:
            # Somebody switched it off. Left alone - a person decided that, and
            # overriding them silently is how a block list stops being trusted.
            logger.info('%s is on a DNC register but its blacklist entry is disabled', number)
    except Exception:
        logger.exception('could not blacklist %s after a DNC hit', number)

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
    # CH-451: a customer's TCPA Shield on this call's campaign asks for the
    # check too, whatever the server-wide switch says.
    shield = tcpa_shield_on(call_log)
    if not shield and not getattr(settings, 'DNC_CHECK_ENABLED', False):
        return {}

    number = (call_log.caller_number or '').strip()
    if not number:
        return {}

    try:
        from spam_protection.realvalidito import DNCLookup
        result = DNCLookup.check(number, force=shield)
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

    # DNC_BLOCK_LISTED has existed since this was written and was never read,
    # so a listed caller was blacklisted whatever the setting said - and it
    # defaults to False. 161 calls were refused on 2026-10-07 alone.
    #
    # These are INBOUND calls. The register governs who you may call, not who
    # may call you, so a listed number dialling our TFN is not the exposure
    # this was built for. The flag is still worth recording: a publisher
    # sending a run of DNC callers is robocalling them and transferring them
    # in, and that shows up in the flag, not in a block.
    if getattr(settings, 'DNC_BLOCK_LISTED', False):
        _blacklist(call_log, number, reason)
    elif shield and reason == LITIGATOR:
        # CH-451: the TCPA Shield blocks known TCPA litigators on the campaigns
        # it covers, from their next call. A number that is only on a do-not-
        # call register is flagged, not blocked: the register is about who may
        # be called, and these callers rang us.
        _blacklist(call_log, number, reason, campaign=call_log.campaign)

    return {'is_dnc': True, 'dnc_reason': reason}


LITIGATOR = 'TCPA litigator'
_TCPA_META = '__tcpa_meta__|'


def _meta_active(blocked_carriers) -> bool:
    """The TCPA Shield page keeps its on/off switch inside a JSON entry in
    `blocked_carriers` ("__tcpa_meta__|{...}"); no entry means on."""
    import json
    for entry in blocked_carriers or []:
        if isinstance(entry, str) and entry.startswith(_TCPA_META):
            try:
                return bool(json.loads(entry[len(_TCPA_META):]).get('active', True))
            except Exception:
                return True
    return True


def tcpa_shield_on(call_log) -> bool:
    """True when one of the workspace's TCPA Shields is switched on and covers
    this call's campaign (CH-451)."""
    campaign_id = getattr(call_log, 'campaign_id', None)
    if not campaign_id:
        return False
    try:
        from spam_protection.models import Shield
        shields = Shield.objects.filter(
            organization_id=call_log.organization_id, shield_type='tcpa', is_active=True,
        ).only('campaign_ids', 'blocked_carriers')
        for s in shields:
            if str(campaign_id) in {str(c) for c in (s.campaign_ids or [])} and _meta_active(s.blocked_carriers):
                return True
    except Exception:
        logger.exception('could not read TCPA shields for call %s', getattr(call_log, 'id', ''))
    return False


def _blacklist(call_log, number, reason, campaign=None):
    """Stop the next call from this number, with no external request.

    `RoutingEngine.is_blacklisted` reads this table at routing time. Writing
    here means the second call from a listed number is refused locally, which
    is the protection the call path can afford.

    `campaign`: None blocks it workspace-wide (the server-wide DNC setting);
    a TCPA Shield blocks it on the campaign the call came in on (CH-451).
    """
    from spam_protection.models import Blacklist

    try:
        entry, created = Blacklist.objects.get_or_create(
            organization_id=call_log.organization_id,
            campaign=campaign,
            phone_number=number,
            defaults={
                'reason': Blacklist.Reason.AUTO,
                'is_active': True,
                'notes': f'Added automatically: {reason}. '
                         f'First seen on call {call_log.id}.',
            },
        )
        if created:
            logger.warning('blacklisted %s %s after a DNC hit (%s)', number,
                           'on its campaign' if campaign else 'org-wide', reason)
        elif not entry.is_active:
            # Somebody switched it off. Left alone - a person decided that, and
            # overriding them silently is how a block list stops being trusted.
            logger.info('%s is on a DNC register but its blacklist entry is disabled', number)
    except Exception:
        logger.exception('could not blacklist %s after a DNC hit', number)

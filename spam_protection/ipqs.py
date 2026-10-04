"""IPQualityScore lookups: the fraud score the platform was named for.

`CallLog.ipqs_fraud_score`, `Campaign.max_fraud_score` and the fraud filter in
`TelnyxLookupService.should_block` have all existed since the beginning. None of
them has ever held or seen a real number: Telnyx returns
`"fraud_score": 0,  # Telnyx does not provide this` and RealValidito does not
sell a score at all, so `0 > 85` was the only comparison the filter ever made
and it has never once blocked a call.

This is the provider those fields were named after. It is wired the same way as
the do-not-call and VOIP checks, for the same reason: a lookup inside
`route_call` took every call down on 29 and 30 September, so nothing here goes
near the call path.

What it does, given it cannot refuse the call it inspected:

  the record     the real score is stored, so fraud is visible rather than
                 assumed to be zero
  the next one   a caller scoring above the campaign's `max_fraud_score` goes
                 on the local blacklist, which `RoutingEngine.is_blacklisted`
                 already reads at routing time from the database. They are
                 refused on their second call, with no external request on the
                 call path.

**Switched off until a key is set.** With no `IPQS_API_KEY` every function here
returns empty and the enrichment task carries on exactly as it does today - so
this can ship before the subscription is bought and start working the moment the
key lands in `.env`, with no code change.

Every result is cached, because IPQS bills per lookup: a caller who rings ten
times costs one credit, not ten.
"""
import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

ENDPOINT = 'https://www.ipqualityscore.com/api/json/phone'
CACHE_PREFIX = 'ipqs:phone:'


def _api_key() -> str:
    return getattr(settings, 'IPQS_API_KEY', '') or ''


def is_enabled() -> bool:
    """True only when a key is configured. Everything here is a no-op without one."""
    return bool(_api_key()) and getattr(settings, 'IPQS_ENABLED', True)


def _ttl() -> int:
    # A fraud score moves, but not hour to hour. Shorter than the carrier
    # profile cache (30 days) because a clean number can start abusing.
    return getattr(settings, 'IPQS_CACHE_DAYS', 7) * 86400


def lookup(number: str) -> dict:
    """Score one number. Returns {} when disabled, cached, or anything fails.

    Never raises. A failed lookup must never cost a call or a record - the
    enrichment task carries on with whatever else it has.
    """
    if not is_enabled():
        return {}

    digits = ''.join(filter(str.isdigit, number or ''))
    if not digits:
        return {}

    cached = cache.get(CACHE_PREFIX + digits)
    if cached is not None:
        return cached

    try:
        import requests
        resp = requests.get(
            f'{ENDPOINT}/{_api_key()}/{digits}',
            params={'strictness': getattr(settings, 'IPQS_STRICTNESS', 0)},
            timeout=getattr(settings, 'IPQS_TIMEOUT', 6),
        )
        result = resp.json() or {}
    except Exception:
        logger.exception('ipqs lookup failed for %s', digits)
        return {}

    if not result.get('success'):
        # Out of credits, bad key, malformed number. Logged, not cached - a
        # credit problem should resolve itself without poisoning the cache for
        # a week.
        logger.warning(
            'ipqs returned no result for %s: %s',
            digits, result.get('message', 'no message'),
        )
        return {}

    cache.set(CACHE_PREFIX + digits, result, _ttl())
    return result


def check_and_record(call_log, result: dict = None) -> dict:
    """Store the score and, if the campaign asked, stop the caller ringing again.

    Returns the fields to write onto the CallLog. Never raises.
    """
    if not is_enabled():
        return {}

    if result is None:
        result = lookup(call_log.caller_number)
    if not result:
        return {}

    try:
        score = int(result.get('fraud_score') or 0)
    except (TypeError, ValueError):
        score = 0

    fields = {'ipqs_fraud_score': score, 'ipqs_checked': True}

    campaign = call_log.campaign
    # 100, not 0, when the campaign has no setting: an absent threshold must
    # mean "no limit", never "block everything".
    ceiling = getattr(campaign, 'max_fraud_score', 100)
    if ceiling is None:
        ceiling = 100

    if score <= ceiling:
        return fields

    fields['ipqs_block_reason'] = f'fraud_score_{score}'[:100]
    _blacklist(call_log, score, ceiling)
    return fields


def _blacklist(call_log, score: int, ceiling: int):
    """Refuse the next call from this number, locally and with no lookup."""
    from spam_protection.models import Blacklist

    number = (call_log.caller_number or '').strip()
    if not number:
        return

    try:
        entry, created = Blacklist.objects.get_or_create(
            organization_id=call_log.organization_id,
            # Scoped to the campaign that set the threshold, like the VOIP
            # block: another campaign with a looser ceiling should still take
            # this caller.
            campaign=call_log.campaign,
            phone_number=number,
            defaults={
                'reason': Blacklist.Reason.AUTO,
                'is_active': True,
                'notes': f'Added automatically: IPQS fraud score {score} is '
                         f'above this campaign\'s limit of {ceiling}. '
                         f'First seen on call {call_log.id}.',
            },
        )
        if created:
            logger.warning(
                'blacklisted %s for campaign %s: fraud score %s > %s',
                number, getattr(call_log.campaign, 'name', call_log.campaign_id),
                score, ceiling,
            )
        elif not entry.is_active:
            logger.info('%s scored %s but its blacklist entry is switched off', number, score)
    except Exception:
        logger.exception('could not blacklist %s after a fraud hit', number)

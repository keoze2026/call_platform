"""Decide whether a caller is on a VOIP line, and act on it after the call.

The VoIP Shield has been in the interface since the beginning. `block_voip`
exists on every campaign, the shields page writes to it, `CallLog.ipqs_is_voip`
is filled in on every call — and `TelnyxLookupService.should_block`, the
function that decides to refuse a VOIP caller, has **no callers anywhere in the
codebase**. The toggle has never blocked anything.

This is the half that was missing, built the same way as the do-not-call check
and for the same reason: a lookup inside `route_call` took every call down on
30 September, so nothing here goes near the call path.

It costs nothing extra. RealValidito already returns the line type on every
call and the enrichment task already stores it, so this reads a value that is
sitting there rather than making a request.

What it does, given it cannot refuse the call it inspected:

  the record     the call is flagged, so VOIP volume is visible rather than
                 guessed at
  the next one   the number goes on the local blacklist, which
                 `RoutingEngine.is_blacklisted` already reads at routing time
                 from the database. The caller is refused on their second call,
                 with no external request on the call path.

Only acts when the campaign has `block_voip` switched on. A campaign that has
not asked for it gets the flag and nothing else.
"""
import logging

logger = logging.getLogger(__name__)

# What the two providers call a VOIP line. RealValidito says "voip" or
# "non-fixed voip"; Telnyx says "voip". Matched case-insensitively against a
# normalised value, because the raw data holds "Mobile" and "mobile" as
# separate strings and would hold "VOIP" and "voip" the same way.
VOIP_TYPES = {'voip', 'non-fixed voip', 'fixed voip', 'nonfixed voip'}

# The spellings the two providers use for the same thing. Without this a report
# grouped by line type splits one category in two - today's data holds
# "Mobile" 742 and "mobile" 265, which are the same line type counted twice.
LINE_TYPE_FAMILIES = {
    'mobile': {'mobile', 'wireless', 'cell', 'cellular'},
    'landline': {'landline', 'fixed line', 'fixed', 'fixed_line'},
    'voip': {'voip', 'non-fixed voip', 'fixed voip', 'nonfixed voip'},
    'toll-free': {'toll free', 'toll-free', 'tollfree'},
}


# VOIP is tested before anything else, and this order is not cosmetic.
# "Non-Fixed VOIP" contains the substring "fixed", so checking the landline
# family first classified a VOIP line as a landline - and a VOIP caller would
# have been waved through the shield built to stop them. Caught by testing the
# function against the real values rather than reading it.
FAMILY_ORDER = ['voip', 'toll-free', 'mobile', 'landline']


def normalise_line_type(raw: str) -> str:
    """One spelling per line type, so a report can group on it.

    Returns the family name, or the cleaned original when nothing matches - an
    unrecognised type stays readable instead of collapsing into one bucket.
    """
    if not raw:
        return ''
    text = raw.strip().lower()

    # Exact match first, across every family, so a precise value is never
    # decided by which family happens to be checked first.
    for family in FAMILY_ORDER:
        if text in LINE_TYPE_FAMILIES[family]:
            return family

    # Then substring, VOIP before the rest.
    for family in FAMILY_ORDER:
        for spelling in LINE_TYPE_FAMILIES[family]:
            if spelling in text:
                return family

    return text[:50]


def is_voip(raw_line_type: str) -> bool:
    return normalise_line_type(raw_line_type) == 'voip'


def check_and_record(call_log, raw_line_type: str) -> dict:
    """Flag a VOIP caller and, if the campaign asked, stop them calling again.

    Returns the fields to write onto the CallLog. Never raises.
    """
    fields = {}

    normalised = normalise_line_type(raw_line_type)
    if normalised:
        fields['ipqs_line_type'] = normalised[:50]
    fields['ipqs_is_voip'] = normalised == 'voip'

    if normalised != 'voip':
        return fields

    campaign = call_log.campaign
    if not getattr(campaign, 'block_voip', False):
        # Flagged, not acted on. The campaign has not asked for VOIP to be
        # blocked, and deciding that for them is not this function's job.
        logger.info(
            'caller %s is on a VOIP line; %s does not have block_voip on',
            call_log.caller_number, getattr(campaign, 'name', 'the campaign'),
        )
        return fields

    _blacklist(call_log)
    return fields


def _blacklist(call_log):
    """Refuse the next call from this number, locally and with no lookup."""
    from spam_protection.models import Blacklist

    number = (call_log.caller_number or '').strip()
    if not number:
        return

    try:
        entry, created = Blacklist.objects.get_or_create(
            organization_id=call_log.organization_id,
            # Scoped to the campaign that asked, unlike the DNC block. A
            # campaign with block_voip off should still receive this caller.
            campaign=call_log.campaign,
            phone_number=number,
            defaults={
                'reason': Blacklist.Reason.AUTO,
                'is_active': True,
                'notes': f'Added automatically: VOIP line, and this campaign '
                         f'blocks VOIP. First seen on call {call_log.id}.',
            },
        )
        if created:
            logger.warning(
                'blacklisted %s for campaign %s after a VOIP hit',
                number, getattr(call_log.campaign, 'name', call_log.campaign_id),
            )
        elif not entry.is_active:
            logger.info('%s is VOIP but its blacklist entry is switched off', number)
    except Exception:
        logger.exception('could not blacklist %s after a VOIP hit', number)

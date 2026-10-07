"""Retiring a number when it has been used, or when its time is up.

Numbers from these carriers are not permanent inventory. One is handed over for
a single call, or for a day, and once that is spent it has to come off the
Numbers page by itself - nobody is going to watch eleven toll-frees and delete
them by hand.

Both rules live on the carrier, not here: `single_use` and `lifetime_hours`.
A carrier with neither set behaves exactly as before.

**Retired, not erased.** The row stays and its status becomes `released`, which
is already how the Numbers page decides what to show, so it disappears from the
interface while the calls that came in on it keep their history. Deleting the
row would take the number out of the call log too.
"""
import logging

from django.utils import timezone

from phone_numbers.models import PhoneNumber

logger = logging.getLogger(__name__)

# The event the interface listens for. "TFN Deleted" is the wording asked for.
EVENT = 'number.deleted'


def stamp_lifetime(number: PhoneNumber) -> None:
    """Set when this number stops being usable, from its carrier's terms.

    Called when a number is taken on or moved to a different carrier. Does
    nothing for a carrier with no limit, and never shortens a deadline that is
    already set - that would quietly move a date somebody is relying on.
    """
    carrier = number.carrier
    if not carrier or not carrier.lifetime_hours:
        return
    if number.expires_at:
        return
    number.expires_at = timezone.now() + timezone.timedelta(hours=carrier.lifetime_hours)


def _notify(number: PhoneNumber, why: str) -> None:
    try:
        from notifications.services import NotificationService
        NotificationService.dispatch(EVENT, number.organization, {
            'number': number.number,
            'name': number.friendly_name,
            'carrier': number.carrier.name if number.carrier_id else '',
            'carrier_code': number.carrier.code if number.carrier_id else '',
            'reason': why,
            'message': 'TFN Deleted',
        })
    except Exception:
        # A number that could not be announced is still retired. Leaving it
        # live because a notification failed is the worse of the two.
        logger.exception('could not announce the retirement of %s', number.number)


def retire(number: PhoneNumber, why: str, call_id: str = '') -> bool:
    """Take a number out of service. Returns False if it already was."""
    if number.status == PhoneNumber.Status.RELEASED:
        return False

    now = timezone.now()
    fields = ['status', 'campaign', 'publisher', 'updated_at']
    number.status = PhoneNumber.Status.RELEASED
    number.campaign = None
    number.publisher = None
    if why == 'used' and not number.used_at:
        number.used_at = now
        number.used_by_call_id = call_id or ''
        fields += ['used_at', 'used_by_call_id']
    number.save(update_fields=fields)

    logger.info('retired %s (%s)', number.number, why)
    _notify(number, why)
    return True


def retire_if_single_use(called_number: str, call_id: str = '') -> bool:
    """Retire the number a call came in on, if its carrier hands them out once.

    Takes the dialled number rather than an id because that is what the call
    carries. Runs after the call has ended, off the call path.
    """
    digits = ''.join(c for c in (called_number or '') if c.isdigit())
    if not digits:
        return False

    number = (
        PhoneNumber.objects
        .select_related('carrier', 'organization')
        .filter(number__endswith=digits[-10:], status=PhoneNumber.Status.ACTIVE)
        .first()
    )
    if not number or not number.carrier_id or not number.carrier.single_use:
        return False

    return retire(number, 'used', call_id)


def retire_expired() -> int:
    """Retire every number whose time is up. Returns how many."""
    now = timezone.now()
    due = (
        PhoneNumber.objects
        .select_related('carrier', 'organization')
        .filter(status=PhoneNumber.Status.ACTIVE, expires_at__lte=now)
    )
    n = 0
    for number in due:
        if retire(number, 'expired'):
            n += 1
    return n

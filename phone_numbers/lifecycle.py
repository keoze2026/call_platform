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
    """The clock starts at first USE, never at import.

    "Use" is a call a buyer answered (CH-143). A call the caller abandoned, a
    decline, a no-answer or a refusal does not start it.

    The first version stamped the deadline when the number was taken on, and
    the whole KMQ batch deleted itself the next morning having never carried a
    call - the boss called it, rightly, a bug. A number that has not been used
    is not touched; once it carries its first call, the carrier's
    lifetime_hours start counting from that moment.
    """
    carrier = number.carrier
    if not carrier or not carrier.lifetime_hours:
        return
    if number.expires_at or not number.used_at:
        return
    number.expires_at = number.used_at + timezone.timedelta(hours=carrier.lifetime_hours)


def _notify(number: PhoneNumber, why: str) -> None:
    try:
        from notifications.services import NotificationService
        NotificationService.dispatch(EVENT, number.organization, {
            'number': number.number,
            'name': number.friendly_name,
            'carrier': number.carrier.name if number.carrier_id else '',
            'carrier_code': number.carrier.code if number.carrier_id else '',
            'reason': why,
            # The owner: the notification must always say WHICH numbers.
            'message': f'TFN Deleted {number.number}',
        })
    except Exception:
        # A number that could not be announced is still retired. Leaving it
        # live because a notification failed is the worse of the two.
        logger.exception('could not announce the retirement of %s', number.number)


def answered_calls(number: PhoneNumber) -> int:
    """Answered calls this number has taken inside its carrier's window.

    The window is the carrier's lifetime (24 hours for KMQ and H24), counted
    back from now: "10 answered calls within 24 hours". A carrier with no
    lifetime has no window, so every answered call counts.
    """
    from routing.models import CallLog

    digits = ''.join(c for c in number.number if c.isdigit())[-10:]
    calls = CallLog.objects.filter(called_number__endswith=digits, status='completed')
    hours = number.carrier.lifetime_hours if number.carrier_id else 0
    if hours:
        calls = calls.filter(created_at__gte=timezone.now() - timezone.timedelta(hours=hours))
    return calls.count()


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
    """Apply the carrier's terms to the number a call just used.

    Single use retires it on the spot. A lifetime starts its clock now - from
    the first ANSWERED call, never from import and never from a call nobody
    answered, which is the use check the first version lacked. The caller
    (call_ended) only queues this for an answered call. Runs after the call has
    ended, off the call path.
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
    if not number or not number.carrier_id:
        return False

    if not number.used_at:
        number.used_at = timezone.now()
        number.used_by_call_id = call_id or ''
        number.save(update_fields=['used_at', 'used_by_call_id', 'updated_at'])

    if number.carrier.single_use:
        return retire(number, 'used', call_id)

    # The owner's rule: the number takes its allowance of answered calls and is
    # gone, so the call after the last one finds it deleted. Done here, at the
    # call that reaches the limit, rather than waiting for the 1:30 AM sweep.
    limit = number.carrier.max_answered_calls
    if limit:
        taken = answered_calls(number)
        if taken >= limit:
            return retire(number, f'{taken} answered calls, limit {limit}', call_id)

    if number.carrier.lifetime_hours and not number.expires_at:
        stamp_lifetime(number)
        if number.expires_at:
            number.save(update_fields=['expires_at', 'updated_at'])
    return False


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

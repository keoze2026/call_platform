"""A buyer's answer that reached us just after the caller hung up (CH-225).

16787331137, 23 Oct: the caller's carrier cancelled at 16:56:53.677 and Revo's
answer (200 OK) for the same call arrived at 16:56:53.772 - the answer and our
cancel crossed on the line. Our switch acknowledges such an answer and hangs up,
as SIP requires, so Revo's leg is answered and Revo shows the call Completed,
2 seconds, with a conversion. Our switch only reports DIALSTATUS=CANCEL, so the
portal showed it missed.

Sir (23 Oct): if Revo shows a duration, ours must too. The answer is in our own
SIP capture, so a host script (docs/asterisk/late_answers.py) finds these and
`manage.py mark_late_answers` counts each one the way CH-222 counts an answer
under a second: Completed, 1 second, settled and charged like any answered call.
"""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from routing.models import CallLog

logger = logging.getLogger(__name__)

NOT_CONNECTED_LABEL = 'Not Connected'
# Our call row is written when routing starts, a moment before the INVITE
# leaves; the INVITE time from the capture is matched to it within this.
MATCH_BEFORE = timedelta(seconds=5)
MATCH_AFTER = timedelta(seconds=1)


def _last10(value) -> str:
    return ''.join(ch for ch in str(value or '') if ch.isdigit())[-10:]


def _at(epoch) -> datetime:
    return datetime.fromtimestamp(float(epoch), dt_timezone.utc)


def find_call(event: dict):
    """The call row a late answer belongs to, or None.

    `event`: caller, called (the number we dialled), invite_at and answer_at as
    Unix seconds - one line of late_answers.py.
    """
    caller, called = _last10(event.get('caller')), _last10(event.get('called'))
    if not caller or not called:
        return None
    invited = _at(event['invite_at'])
    return (CallLog.objects
            .filter(caller_number__endswith=caller,
                    destination_number__endswith=called,
                    created_at__gte=invited - MATCH_BEFORE,
                    created_at__lte=invited + MATCH_AFTER)
            .select_related('campaign', 'organization', 'buyer', 'publisher')
            .order_by('-created_at').first())


def mark(call: CallLog, event: dict) -> bool:
    """Count `call` as answered for 1 second. False when it is not a missed call
    any more (already marked, or answered in the normal way)."""
    from routing.asterisk_handler import settle_call_money

    if call.status != CallLog.Status.NO_ANSWER or call.block_reason != NOT_CONNECTED_LABEL:
        return False
    answered_at = _at(event['answer_at'])
    call.status = CallLog.Status.COMPLETED
    call.duration = 1
    call.block_reason = ''
    call.answered_at = answered_at
    call.ring_seconds = max(0, int((answered_at - call.created_at).total_seconds()))
    # Saves the row (and so mirrors it into Reports), with revenue, payout and
    # the balance charge exactly as for an answer reported at hang-up.
    settle_call_money(call, True, 1)
    try:
        from tasks import retire_used_number
        retire_used_number.delay(call.called_number, str(call.id))
    except Exception:
        logger.warning('retire_not_queued: late answer call_log=%s', call.id)
    logger.warning('late answer counted: call %s, caller %s, answered %.3fs after the cancel',
                   call.id, call.caller_number,
                   float(event['answer_at']) - float(event.get('cancel_at') or event['answer_at']))
    return True

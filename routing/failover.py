"""Try the next buyer, and rest a destination that keeps missing (CH-514).

Sir, 4 Nov:
  "Every call: try the next buyer. If a destination doesn't answer within
   about 15 seconds, the call moves to the next buyer straight away. The
   caller is never lost.
   After 5 misses in a row: cooldown. The backend skips that destination for
   15 minutes, so calls stop going there.
   After the cooldown: auto-retry. It sends one call. If that's answered, the
   destination goes back to normal. If not, another 15-minute cooldown starts."

Until now Server A rang a buyer with no limit of its own (Dial's ring time was
the 3600 s call limit) until the sending platform gave up and dialled us again:
Q06's line refused every call and the senders redialled it 143 times on 3 Nov.

How it works:
  - The route reply carries `ring_timeout` (FAILOVER_RING_SECONDS) and
    `next_on_fail`. Server A's dialplan rings that long; when the destination
    does not answer (no answer, busy, refused, unavailable) and the caller is
    still on the line, route_call.py asks `route-next` for the next buyer.
  - The call keeps ONE row, the one the caller's call was created with: the
    Call Log, the reports, the recording file and the hang-up all keep the same
    call id. Each destination that was tried and missed is kept as a hidden
    row of its own (retry_hidden, retry_root = the call) - never shown or
    counted (CH-513), but it is what the cooldown reads.
  - A destination already tried on this call is not tried again.
  - Off (FAILOVER_RING_SECONDS = 0): no ring limit is sent, Server A dials as
    before, `route-next` answers "hangup", and no destination is cooled down.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# A destination that did not take the call; "Not Connected" is the caller
# hanging up, not the destination's fault.
MISS_REASONS = ('No Answer', 'Destination busy')
DIAL_MISS = {
    'NOANSWER': 'No Answer',
    'CONGESTION': 'No Answer',
    'CHANUNAVAIL': 'No Answer',
    'BUSY': 'Destination busy',
}


def ring_seconds() -> int:
    return max(0, int(getattr(settings, 'FAILOVER_RING_SECONDS', 0) or 0))


def enabled() -> bool:
    return ring_seconds() > 0


def _d10(number) -> str:
    return ''.join(c for c in str(number or '') if c.isdigit())[-10:]


def route_extras() -> dict:
    """What the route reply adds for Server A's dialplan."""
    if not enabled():
        return {}
    return {'ring_timeout': ring_seconds(), 'next_on_fail': 1}


def cooling(buyer, number, now=None):
    """None, or why this destination is resting.

    The last LINE_COOLDOWN_MISSES finished calls on the line (the buyer and its
    TFN), tried-and-missed ones included: all missed -> rest
    LINE_COOLDOWN_MINUTES from the newest miss. After that one call goes
    through; while it is still ringing or talking the line rests. Answered, the
    run of misses is broken and the line is back to normal; missed, it is the
    newest miss and the rest starts again.
    """
    if not enabled() or buyer is None:
        return None
    misses = max(1, int(getattr(settings, 'LINE_COOLDOWN_MISSES', 5) or 5))
    rest = timedelta(minutes=max(1, int(getattr(settings, 'LINE_COOLDOWN_MINUTES', 15) or 15)))
    d10 = _d10(number)
    if len(d10) < 10:
        return None
    from routing.models import CallLog
    now = now or timezone.now()
    line = CallLog.objects.filter(buyer=buyer, destination_number__endswith=d10,
                                  created_at__gte=now - timedelta(days=2))
    last = list(
        line.filter(ended_at__isnull=False)
        .filter(Q(status='completed') | Q(status='no_answer', block_reason__in=MISS_REASONS))
        .order_by('-ended_at').values_list('status', 'ended_at')[:misses]
    )
    if len(last) < misses or any(s != 'no_answer' for s, _ in last):
        return None
    newest = last[0][1]
    if now - newest < rest:
        left = int((rest - (now - newest)).total_seconds() // 60) + 1
        return f'resting {left} min after {misses} missed in a row'
    # The rest is over: one call may go through. While it is on, the line rests.
    if line.filter(status__in=['ringing', 'in_progress'], created_at__gte=newest).exists():
        return 'one test call already on after the rest'
    return None


def record_miss(call_log, dial_status: str, dialed_seconds: int):
    """Keep the destination this call just missed as a hidden row."""
    from routing.models import CallLog
    now = timezone.now()
    reason = DIAL_MISS.get((dial_status or '').upper(), 'No Answer')
    root_id = call_log.retry_root_id or call_log.pk
    n = CallLog.objects.filter(retry_root_id=root_id, twilio_call_sid__contains='#try').count() + 1
    miss = CallLog.objects.create(
        organization_id=call_log.organization_id, campaign_id=call_log.campaign_id,
        publisher_id=call_log.publisher_id, publisher_payout=0,
        buyer_id=call_log.buyer_id, routing_rule_id=call_log.routing_rule_id,
        rule_destination_id=call_log.rule_destination_id,
        caller_number=call_log.caller_number, called_number=call_log.called_number,
        caller_area_code=call_log.caller_area_code, caller_state=call_log.caller_state,
        caller_country=call_log.caller_country,
        destination_number=call_log.destination_number,
        twilio_call_sid=f'{call_log.twilio_call_sid}#try{n}',
        is_duplicate=call_log.is_duplicate,
        status=CallLog.Status.NO_ANSWER, block_reason=reason,
        ring_seconds=max(0, int(dialed_seconds or 0)),
        ended_at=now, retry_hidden=True, retry_root_id=root_id,
        routing_trace=call_log.routing_trace or {},
    )
    # When this try started ringing, so the cooldown and any report see it in order.
    started = now - timedelta(seconds=max(0, int(dialed_seconds or 0)))
    CallLog.objects.filter(pk=miss.pk).update(created_at=max(started, call_log.created_at))
    return miss


def tried_numbers(call_log) -> set:
    """The destinations already tried on this caller's call."""
    from routing.models import CallLog
    root_id = call_log.retry_root_id or call_log.pk
    rows = CallLog.objects.filter(Q(pk=call_log.pk) | Q(retry_root_id=root_id, twilio_call_sid__contains='#try'))
    return {_d10(n) for n in rows.values_list('destination_number', flat=True) if n}

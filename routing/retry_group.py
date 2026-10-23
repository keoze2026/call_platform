"""One row per caller call, the way Revo shows it (CH-215).

A sending platform (Revo) keeps the caller on the line and, when our buyer has
not answered within its per-try limit, cancels and dials us again within a
second. Each try reaches us as a brand-new SIP call - new Call-ID, sometimes a
different carrier edge, no shared identifier (checked on the SIP capture,
22 Oct). Revo reports the whole thing as ONE call; we reported every try:

    12052619645  Revo: 1 missed (54s)         us: 3 red rows  (23s, 23s, 1s)
    17198591047  Revo: 3 calls (56s, 59s, live)  us: 16 rows (6-8s each)

What links the tries is how Revo makes them: the same caller on the same
tracking number, the next try arriving within a few seconds of the previous
one ending, and every try running the same length - Revo's limit - until the
last. A try that ends early was the caller hanging up; what comes after it is
a new call even if it arrives a second later (17198591047 at 4:23:28 PM).

The rule, for a new call against the previous try of the same caller on the
same number:
  1. the previous try went unanswered and has ended;
  2. it ended at most RETRY_GAP_SECONDS before the new call arrived;
  3. it ran at least MIN_TRY_SECONDS - a shorter try is the caller hanging up;
  4. it ran at least SAME_LIMIT_RATIO of the try before it (Revo's limit is the
     same for every try of one call; a try well short of it was cut by the
     caller, not by Revo).
Rules 3 and 4 do not apply when the buyer's line itself refused the previous
try (fail reason "No Answer"): then the redial is the sender's whatever the
length (CH-227).

The grouped call is shown once: its latest try (so its result is the final
one), dated from the first try, with the waiting time counted from the first
try. Earlier tries are kept with retry_hidden set and are left out of every
report, total and export.
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

RETRY_GAP_SECONDS = 4
MIN_TRY_SECONDS = 4
SAME_LIMIT_RATIO = 0.6
LOOKBACK_MINUTES = 15
# The fail reason a try gets when the buyer's line refused or rang out it
# (routing.asterisk_handler.NO_ANSWER_LABEL), as opposed to the caller hanging up.
REFUSED_LABEL = 'No Answer'


def try_length(call) -> float:
    """Seconds one try lasted on our side, arrival to end."""
    if not call.ended_at:
        return 0.0
    return max(0.0, (call.ended_at - call.created_at).total_seconds())


def is_retry(prev, before, arrived_at) -> bool:
    """Is a call arriving at `arrived_at` the sender retrying `prev`?
    `before` is the try ahead of `prev` in the same call, or None."""
    from routing.models import CallLog
    if prev is None or prev.status != CallLog.Status.NO_ANSWER or not prev.ended_at:
        return False
    gap = (arrived_at - prev.ended_at).total_seconds()
    # A little slack below zero: the new call can be written a moment before
    # the previous try's end is.
    if gap < -1 or gap > RETRY_GAP_SECONDS:
        return False
    length = try_length(prev)
    # CH-227: the buyer's line itself refused or rang out the previous try
    # ("No Answer"), so the redial is the sender's, not a caller who hung up -
    # however short the try. 17192334345, 23 Oct: the PDSO line refused 24
    # tries in a row within a second each, the sender redialled every 1.5 s,
    # and each became its own red row because each was shorter than
    # MIN_TRY_SECONDS.
    refused = prev.block_reason == REFUSED_LABEL
    if length < MIN_TRY_SECONDS and not refused:
        return False
    if before is not None and not refused and length < SAME_LIMIT_RATIO * try_length(before):
        return False
    return True


def _try_before(prev):
    """The try ahead of `prev` in its call, or None when prev is the first."""
    from routing.models import CallLog
    if not prev.retry_root_id:
        return None
    return (CallLog.objects
            .filter(Q(pk=prev.retry_root_id) | Q(retry_root_id=prev.retry_root_id),
                    created_at__lt=prev.created_at)
            .order_by('-created_at').first())


def find_retry_of(organization_id, caller, called, arrived_at=None):
    """The previous try this new call continues, or None."""
    from routing.models import CallLog
    if not caller or not called:
        return None
    arrived_at = arrived_at or timezone.now()
    prev = (CallLog.objects
            .filter(organization_id=organization_id, caller_number=caller, called_number=called,
                    retry_hidden=False,
                    created_at__gte=arrived_at - timedelta(minutes=LOOKBACK_MINUTES),
                    created_at__lt=arrived_at)
            .order_by('-created_at').first())
    if prev is None:
        return None
    return prev if is_retry(prev, _try_before(prev), arrived_at) else None


def link(call, prev) -> None:
    """Make `call` the visible try of `prev`'s call and hide `prev`."""
    from analytics.models import CallRecord
    from routing.models import CallLog
    root_id = prev.retry_root_id or prev.pk
    CallLog.objects.filter(pk=call.pk).update(retry_root_id=root_id)
    call.retry_root_id = root_id
    CallLog.objects.filter(pk=prev.pk).update(retry_hidden=True)
    # The reports read the mirror; the hidden try leaves it.
    CallRecord.objects.filter(id=prev.pk).delete()


def group_view(call):
    """(started_at, waited_seconds) for the row a grouped call shows: dated from
    its first try; waited is first try to answer, or to the end if unanswered -
    what Revo reports as time to connect for the same call."""
    from routing.models import CallLog
    if not call.retry_root_id:
        return call.created_at, call.ring_seconds
    root = CallLog.objects.filter(pk=call.retry_root_id).values_list('created_at', flat=True).first()
    started = min(root, call.created_at) if root else call.created_at
    end = call.answered_at or call.ended_at or call.created_at
    return started, max(0, int((end - started).total_seconds()))


def attempts_of(call) -> int:
    """How many tries the call shown on this row took (1 when not grouped)."""
    from routing.models import CallLog
    root_id = call.retry_root_id or call.pk
    return CallLog.objects.filter(Q(pk=root_id) | Q(retry_root_id=root_id),
                                  created_at__lte=call.created_at).count()

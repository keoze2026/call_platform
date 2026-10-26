"""An answer that arrives while a CANCEL is being held (CH-296).

Long-term fix for "missed on Avortyx, Completed on the buyer's platform": when
the caller hangs up while the buyer is ringing, our CANCEL used to reach XOLO
within 0.1 s and close the path, so a buyer who picked up in the same second
was never heard of here (28 of 29 such calls on 26 Oct: no 200 OK at all).

The SIP relay in front of XOLO (docs/kamailio/) now holds each CANCEL for a
moment (1000 ms to start - sized on the 29 calls). A 200 OK that comes back
during the hold reaches our switch, which ACKs and hangs up, and the relay
reports it here at once. The call is then recorded Answered, 1 second, the
same as the buyer's platform records it - live, with no Revo and no capture.

The relay can report before or after the dialplan's hang-up report:
  before -> remembered, and call_ended counts the call answered;
  after  -> the missed call is made Completed, 1 s, here.
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.core.cache import cache

logger = logging.getLogger(__name__)

KEY = 'held_answer:%s'
TAG = 'Answered during cancel hold'
TTL = 15 * 60


def remember(call_log_id, answered_at: float) -> None:
    cache.set(KEY % call_log_id, float(answered_at), TTL)


def take(call_log_id):
    """The held answer's time for this call (Unix seconds), once, or None."""
    key = KEY % call_log_id
    value = cache.get(key)
    if value is not None:
        cache.delete(key)
    return value


def as_datetime(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch), dt_timezone.utc)


def tag(call) -> None:
    tags = [t for t in (call.tags if isinstance(call.tags, list) else []) if t]
    if TAG not in tags:
        tags.append(TAG)
    call.tags = tags

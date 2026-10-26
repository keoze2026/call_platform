"""Serve finished days from Redis instead of recomputing them.

A range that ended before today (in the requester's timezone) is history;
the first person to open it pays the database, everyone after reads Redis.
Today is always computed live.

Two deliberate choices:

  the key carries who may see what   partners get their own entries (their
  masking depends on their record), employees share one entry per workspace
  role (hidden report columns differ by role, nothing else does). Caching
  the finished response per audience class is what makes a cache hit safe.

  six hours, not forever              "a closed day never changes" is almost
  true: repairs and backfills do change history (CH-097 repaired two calls
  from weeks back). A TTL bounds how stale a repaired day can look.
"""
import hashlib
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

TTL_SECONDS = 6 * 3600

# A day is closed this long after it ends, not at midnight (CH-287). A call
# is recorded when it ENDS but belongs to the day it started, so a call still
# running at midnight joins that day after midnight. Cached at 00:05, the day
# was served without those calls for six hours.
SETTLE = timedelta(hours=2)

# Every filter that changes the response must be in the key, or two
# different requests would share one answer.
_KEY_FIELDS = (
    'date_from', 'date_to', 'start_date', 'end_date', 'timezone',
    'campaign_id', 'buyer_id', 'publisher_id', 'status', 'destination',
    'search', 'granularity', 'limit', 'offset',
    'is_qualified', 'is_converted', 'is_duplicate', 'is_spam',
)


def _range_is_closed(filters, user) -> bool:
    from analytics.services import _tz, _f
    date_to = _f(filters, 'date_to') or _f(filters, 'end_date')
    date_from = _f(filters, 'date_from') or _f(filters, 'start_date')
    if not date_from or not date_to:
        return False
    settled = timezone.localtime(timezone.now() - SETTLE, _tz(filters, user)).date().isoformat()
    return date_to < settled


def closed_range_response(request, filters, name, compute):
    user = request.auth
    if not _range_is_closed(filters, user):
        return compute()

    if getattr(user, 'role', '') in ('buyer', 'publisher'):
        audience = f'partner:{user.id}'
    else:
        audience = f'role:{user.role}'
    raw = '|'.join(f'{f}={getattr(filters, f, None)}' for f in _KEY_FIELDS)
    # v2 (CH-284): the call log gained `cost` and publishers `total_cost`;
    # entries cached before that would show "-" for up to six hours.
    key = (f'reports:v2:{name}:{user.organization_id}:{audience}:'
           f'{hashlib.sha256(raw.encode()).hexdigest()}')

    hit = cache.get(key)
    if hit is not None:
        return hit
    result = compute()
    try:
        cache.set(key, result, TTL_SECONDS)
    except Exception:
        # A cache that cannot store must never fail the report.
        pass
    return result

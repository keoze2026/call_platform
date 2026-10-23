"""Has the report on screen gone out of date? (CH-219)

Reports only reloaded on a timer. With auto refresh Off nothing reloaded at all,
so the candles stayed where they were until someone pressed Refresh (Sir,
23 Oct: "when auto refresh is off it should update by itself").

Reloading the whole day every few seconds to find out would be wasteful. This
answers the cheap question instead: a short fingerprint of the calls in the
same view the report shows - same days, same time zone, same partner scope -
plus the server's time. The page asks every few seconds, and reloads the report
only when the fingerprint changes: a call arrived, was answered, ended, or was
corrected.

The fingerprint covers
  - finished calls (the reports mirror): how many, how many completed, their
    total seconds, and the latest change to any of them;
  - calls still in progress: how many, and the latest change to any of them.
A call that is grouped into another (CH-215) leaves the mirror, so the count
changes too.
"""
import hashlib

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone


def _iso(value):
    return value.isoformat() if value else '-'


def report_stamp(user, filters) -> dict:
    from analytics.services import AnalyticsService

    # The very queryset every report is built on, so the days, the time zone
    # and a partner login's scope are exactly the report's.
    hist = AnalyticsService._base_qs(user, filters).aggregate(
        n=Count('id'),
        completed=Count('id', filter=Q(status='completed')),
        seconds=Coalesce(Sum('duration_seconds'), 0),
        changed=Max('updated_at'),
    )
    live = AnalyticsService._live_qs(user, filters).aggregate(
        n=Count('id'),
        changed=Max('updated_at'),
    )
    raw = '|'.join(str(part) for part in (
        hist['n'], hist['completed'], hist['seconds'], _iso(hist['changed']),
        live['n'], _iso(live['changed']),
    ))
    return {
        'stamp': hashlib.sha1(raw.encode()).hexdigest()[:16],
        'as_of': timezone.now().isoformat(),
        'calls': hist['n'],
        'live': live['n'],
    }

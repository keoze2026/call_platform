"""CH-538: what "live" means - one rule for every page.

Sir, 4 Nov: "Live counts in Reports must come from the same live source as the
header and Live Monitor." Before this each place had its own rule:

    header Live          ringing, in progress, initiated, queued - today (server day)
    Live Monitor list    in progress only (a ringing call was not on it)
    Reports Live column  ringing, in progress, initiated - in the report's dates
    campaign LIVE column ringing, in progress, not ended, started < 4 h ago

so the same moment could read 2 on one screen and 1 on another.

Now a call is live when it is ringing or connected, has not ended, and started
less than MAX_LIVE_HOURS ago (anything older is a stuck row the stale sweep
closes - tasks.close_stale_calls). Reports apply the same rule inside the dates
on screen: a live call shows on the day it started.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

# 'initiated' and 'queued' are older names some paths still write.
LIVE_STATUSES = ('ringing', 'in_progress', 'initiated', 'queued')
MAX_LIVE_HOURS = 4


def live_q(now=None) -> Q:
    now = now or timezone.now()
    return Q(status__in=LIVE_STATUSES, ended_at__isnull=True,
             created_at__gte=now - timedelta(hours=MAX_LIVE_HOURS))


def live(qs, now=None):
    """The live calls of a CallLog queryset."""
    return qs.filter(live_q(now))

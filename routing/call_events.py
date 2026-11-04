"""CH-538: every call event reaches the reports at once.

Sir, 4 Nov: "Clear or update the report cache the moment a call event happens,
instead of on a fixed timer. Use a WebSocket/event push so the page updates
without a refresh. Target: any call appears in Reports and Call Log within 2
seconds of starting."

When a call is created, answered, ended or changed (CallLog saved -
routing/signals.py), after the database has committed:

  1. the workspace's report version goes up by one. Today's cached reports and
     the header (analytics/report_cache.py) carry this version in their key,
     so the very next request computes fresh figures - the cache stays for
     everything that did not change, it is not removed;
  2. a "report.changed" message goes to every portal tab of the workspace on
     the live WebSocket (/ws/live-calls/). It carries no call details - only
     the kind of event - so a buyer or publisher login learns nothing it may
     not see: its page reloads through the same scoped API as before.

Never raises: a call must not fail because Redis or the socket is down. The
page's own check (every 10 s) is still there as a fallback.
"""
import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

_KEY = 'reports:ver:{}'
# Its own group, not live_calls_org_{}: that one carries call details (old
# Twilio path) and a buyer's or publisher's tab must never receive them.
GROUP = 'reports_org_{}'


def report_version(organization_id) -> str:
    """The workspace's current report version ('0' when unknown)."""
    try:
        v = cache.get(_KEY.format(organization_id))
    except Exception:
        return '0'
    return str(v) if v is not None else '0'


def _bump(organization_id) -> None:
    key = _KEY.format(organization_id)
    try:
        # Start from the clock, so a version lost with a Redis restart never
        # comes back to a number an older cache entry was stored under.
        cache.add(key, int(time.time() * 1000), None)
        cache.incr(key)
    except Exception:
        logger.debug('report version not bumped for %s', organization_id, exc_info=True)


def _push(organization_id, event: str) -> None:
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(GROUP.format(organization_id),
                                        {'type': 'report_changed', 'data': {'event': event}})
    except Exception:
        logger.debug('report push failed for %s', organization_id, exc_info=True)


def publish(organization_id, event: str) -> None:
    if not organization_id:
        return
    _bump(organization_id)
    _push(organization_id, event)


def event_name(status: str, created: bool) -> str:
    if created:
        return 'call.created'
    if status == 'in_progress':
        return 'call.connected'
    if status in ('completed', 'no_answer', 'busy', 'failed', 'canceled'):
        return 'call.ended'
    return 'call.updated'

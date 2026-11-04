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


# ── CH-542: settings changed (caps, lines, buyers) ───────────────────────────
#
# Camp team, 4 Nov: "when you set a buyer's cap I don't have to refresh
# Destinations for it to reflect ... when one member sets a buyer's cap, I
# have to refresh so that it can reflect on my side." When a buyer, a buyer
# cap, a destination line, a campaign's routed line, a campaign cap or a
# publisher cap is saved or deleted, every portal tab of the workspace gets
# "report.changed" with event "config.changed" - nothing else - and reloads
# those figures through the same API (lib/report-events.ts). Reports' cache
# is not touched: a cap is not a call.

CONFIG_EVENT = 'config.changed'


def publish_config(organization_id) -> None:
    if organization_id:
        _push(organization_id, CONFIG_EVENT)


def _org_of(instance):
    """The workspace of a saved settings row, or None."""
    for path in ('organization_id', 'buyer.organization_id', 'rule.organization_id',
                 'campaign.organization_id', 'publisher.organization_id'):
        obj = instance
        try:
            for part in path.split('.'):
                obj = getattr(obj, part)
        except Exception:
            continue
        if obj:
            return obj
    return None


def _on_settings_change(sender, instance, **kwargs):
    from django.db import transaction
    try:
        org_id = _org_of(instance)
    except Exception:
        return
    if org_id:
        transaction.on_commit(lambda: publish_config(org_id))


def connect_settings_signals() -> None:
    from django.db.models.signals import post_delete, post_save
    from buyers.destination import Destination
    from buyers.models import Buyer, BuyerCap
    from campaigns.models import CampaignCap
    from publishers.models import PublisherCap
    from routing.models import RuleDestination
    for model in (Buyer, BuyerCap, Destination, RuleDestination, CampaignCap, PublisherCap):
        post_save.connect(_on_settings_change, sender=model, dispatch_uid=f'ch542-save-{model.__name__}')
        post_delete.connect(_on_settings_change, sender=model, dispatch_uid=f'ch542-del-{model.__name__}')

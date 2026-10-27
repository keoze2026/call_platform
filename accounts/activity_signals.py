"""Record every change to a workspace record in the activity log.

`/api/accounts/workspace/activity` has only ever returned logins. The reader was
fixed to show which record an entry refers to, but there was nothing to show:
the only writers in the codebase are the account ones in `accounts/services.py`
- login, logout, password change, MFA, API keys. Creating a buyer, editing a
campaign, deleting a destination wrote nothing at all, so the audit trail the
page exists for did not exist.

This listens to the models instead of the endpoints. Nothing in any view or API
module changes, which matters for two reasons: there is no forty-first endpoint
to forget, and the call path is not touched to add it.

Only the models a person edits are registered. `CallLog` and the analytics
mirrors are deliberately absent - they are written by the platform thousands of
times a day and would bury the entries a person is looking for.

Every handler swallows its own failures. An audit line is worth having, and it
is never worth losing the save it was describing.
"""
import logging

from django.db.models.signals import post_delete, post_save, pre_save

from .current_request import get_current_request, get_current_user

logger = logging.getLogger(__name__)

# The models worth an entry, and how to name one in the feed.
TRACKED = {
    'buyers.Buyer': ('buyer', 'name'),
    'buyers.Destination': ('destination', 'name'),
    'publishers.Publisher': ('publisher', 'name'),
    'campaigns.Campaign': ('campaign', 'name'),
    'campaigns.CampaignGroup': ('campaign_group', 'name'),
    'routing.RoutingRule': ('routing_rule', 'name'),
    'routing.RuleDestination': ('campaign_destination', 'destination'),
    'phone_numbers.PhoneNumber': ('phone_number', 'number'),
    'notifications.NotificationRule': ('notification_rule', 'name'),
    'accounts.User': ('user', 'email'),
    # Everything else a person can change from the portal. '__str__' names the
    # record by its own text where it has no name of its own (a cap belongs to
    # a campaign, a schedule to a buyer).
    'accounts.Organization': ('workspace', 'name'),
    'accounts.CustomRole': ('role', 'name'),
    'campaigns.CampaignCap': ('campaign_cap', '__str__'),
    'campaigns.CampaignSchedule': ('campaign_schedule', '__str__'),
    'buyers.BuyerCap': ('buyer_cap', '__str__'),
    'buyers.BuyerSchedule': ('buyer_schedule', '__str__'),
    'buyers.BuyerCampaign': ('buyer_campaign', '__str__'),
    'publishers.PublisherCap': ('publisher_cap', '__str__'),
    'publishers.PublisherCampaign': ('publisher_campaign', '__str__'),
    'spam_protection.Blacklist': ('blocked_number', 'phone_number'),
    'spam_protection.Whitelist': ('allowed_number', 'phone_number'),
    'spam_protection.AnonymousCallBlock': ('anonymous_call_block', '__str__'),
    'spam_protection.Shield': ('shield', 'name'),
    'ivr.IVRFlow': ('ivr_flow', 'name'),
    'call_queue.CallQueue': ('call_queue', '__str__'),
    'webhooks.Webhook': ('webhook', 'name'),
    'webhooks.ConversionPixel': ('conversion_pixel', 'name'),
    'dni.DNIPool': ('dni_pool', '__str__'),
    'phone_numbers.Carrier': ('carrier', 'name'),
    'white_label.WhiteLabel': ('white_label', '__str__'),
    'billing.BillingAccount': ('billing_account', '__str__'),
}


def _label(instance) -> str:
    return f'{instance._meta.app_label}.{instance._meta.object_name}'


def _write(instance, action, extra=None):
    from .models import ActivityLog

    tracked = TRACKED.get(_label(instance))
    if tracked is None:
        return

    target_type, name_field = tracked
    extra = dict(extra or {})

    user = get_current_user()
    if user is None:
        # A management command, a Celery task, or the seeding code. There is no
        # person to attribute it to, and an entry with no actor is worse than
        # none: the feed is there to answer "who did this".
        return

    request = get_current_request()
    # CH-328: a change made through a Campaign Group says so; the Telegram
    # alert then sends the group's one line instead of one per campaign.
    via = getattr(request, '_activity_via_group', None) if request is not None else None
    if via:
        extra['via_group'] = via

    try:
        ActivityLog.objects.create(
            user=user,
            action=action,
            ip_address=_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else '')[:500],
            metadata={
                'target_type': target_type,
                'target_id': str(instance.pk),
                'target_name': _target_name(instance, name_field)[:200],
                **extra,
            },
        )
    except Exception:
        # Never let the audit line take the save down with it.
        logger.exception(
            'activity log write failed: action=%s target=%s id=%s',
            action, target_type, instance.pk,
        )


def _target_name(instance, name_field) -> str:
    name = (str(instance) if name_field == '__str__'
            else str(getattr(instance, name_field, '') or ''))
    # A buyer line is known by its number as much as its name (CH-200):
    # "PDSO +18667404516", so the feed says which line was switched.
    if _label(instance) == 'buyers.Destination':
        tfn = str(getattr(instance, 'tfn', '') or '')
        if tfn and tfn not in name:
            name = f'{name} {tfn}'.strip()
    return name


def _client_ip(request):
    if request is None:
        return None
    # RealClientIPMiddleware has already rewritten REMOTE_ADDR from
    # CF-Connecting-IP, so this is the caller's address and not Cloudflare's.
    ip = request.META.get('REMOTE_ADDR') or None
    return ip


# Fields nobody wants to read in an audit feed: they change on every save and
# say nothing about what a person did.
NOISE_FIELDS = {'updated_at', 'password', 'last_login', 'live_calls',
                'hourly_calls', 'daily_calls', 'monthly_calls', 'global_calls'}


# A field whose value is a secret. The feed says it changed and never says to
# what: the old value of `mfa_secret` was being written into the log, in plain
# text, when two-factor was turned off - readable by anyone who can open the
# Activity tab.
SENSITIVE = ('secret', 'token', 'password', 'hash', 'api_key', 'private_key')
HIDDEN = '(hidden)'


def _is_sensitive(field_name: str) -> bool:
    name = field_name.lower()
    return any(part in name for part in SENSITIVE)


def _snapshot(instance) -> dict:
    """The row as it is in the database right now, before this save."""
    try:
        current = type(instance).objects.filter(pk=instance.pk).first()
    except Exception:
        return {}
    if current is None:
        return {}
    out = {}
    for f in type(instance)._meta.concrete_fields:
        if f.name in NOISE_FIELDS:
            continue
        try:
            out[f.name] = getattr(current, f.attname)
        except Exception:
            continue
    return out


def on_pre_save(sender, instance, **kwargs):
    """Stash the old values so on_save can say what actually changed.

    Without this the feed could only say "updated", which is why every row read
    the same and told nobody anything.
    """
    if instance.pk is None:
        instance._activity_before = {}
        return
    instance._activity_before = _snapshot(instance)


def _changed_fields(instance) -> dict:
    """{field: {'old': …, 'new': …}} for everything this save altered."""
    before = getattr(instance, '_activity_before', None)
    if not before:
        return {}
    changes = {}
    for name, old in before.items():
        try:
            field = type(instance)._meta.get_field(name)
            new = getattr(instance, field.attname)
        except Exception:
            continue
        if old == new:
            continue
        if _is_sensitive(name):
            changes[name] = {'old': HIDDEN, 'new': HIDDEN}
            continue
        changes[name] = {
            'old': None if old is None else str(old)[:200],
            'new': None if new is None else str(new)[:200],
        }
    return changes


def on_save(sender, instance, created, **kwargs):
    from .models import ActivityLog

    # Every login saves the user to stamp `last_login`, and a password reset
    # saves it again. Both already have their own entry written by hand in
    # `accounts/services.py`, so logging the save as well would double every
    # login in the feed.
    update_fields = kwargs.get('update_fields')
    if update_fields and set(update_fields) <= {'last_login', 'password', 'updated_at'}:
        return

    extra = {'fields': sorted(update_fields)} if update_fields else None

    if not created:
        changes = _changed_fields(instance)
        if not changes:
            # Nothing a reader would care about moved. Logging it anyway is how
            # a feed fills with thousands of identical "Update" rows that say
            # nothing - which is exactly what was reported.
            return
        extra = {**(extra or {}), 'changes': changes}
        if _label(instance) == 'buyers.Destination' and 'enabled' in changes:
            # Switching a line off refuses every call routed to it, so the
            # feed names it as its own action (CH-200).
            extra['switched'] = 'on' if changes['enabled']['new'] == 'True' else 'off'

    _write(
        instance,
        ActivityLog.Action.RECORD_CREATED if created else ActivityLog.Action.RECORD_UPDATED,
        extra,
    )


def on_delete(sender, instance, **kwargs):
    from .models import ActivityLog

    _write(instance, ActivityLog.Action.RECORD_DELETED)


def on_log_save(sender, instance, **kwargs):
    """Put the address and browser on every entry that does not have them.

    The entries written by hand in `accounts/services.py` (password change,
    two-factor, API keys, profile) never passed the address, so those rows had
    no IP. Filled in here, once, for all of them - and for any writer added
    later - from the request being served.
    """
    if instance.ip_address and instance.user_agent:
        return
    request = get_current_request()
    if request is None:
        return
    if not instance.ip_address:
        instance.ip_address = _client_ip(request)
    if not instance.user_agent:
        instance.user_agent = (request.META.get('HTTP_USER_AGENT', '') or '')[:500]


def connect():
    """Bind the handlers to the tracked models, and to nothing else.

    Each receiver is registered against its own `sender`. A receiver registered
    without one is called for every save the platform makes, which on this
    system means every CallLog write on the call path - thousands a day, each
    one entering a handler only to look up a dictionary and leave. Binding per
    model means a call never enters this code at all.
    """
    from django.apps import apps

    from .models import ActivityLog
    pre_save.connect(
        on_log_save, sender=ActivityLog, dispatch_uid='activity_log_fill_request',
    )

    for label in TRACKED:
        app_label, model_name = label.split('.')
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            logger.warning('activity log: no model %s, not tracking it', label)
            continue

        pre_save.connect(
            on_pre_save, sender=model, dispatch_uid=f'activity_log_pre_{label}',
        )
        post_save.connect(
            on_save, sender=model, dispatch_uid=f'activity_log_save_{label}',
        )
        post_delete.connect(
            on_delete, sender=model, dispatch_uid=f'activity_log_delete_{label}',
        )

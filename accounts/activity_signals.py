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

from django.db.models.signals import post_delete, post_save

from .current_request import get_current_request, get_current_user

logger = logging.getLogger(__name__)

# The models worth an entry, and how to name one in the feed.
TRACKED = {
    'buyers.Buyer': ('buyer', 'name'),
    'buyers.Destination': ('destination', 'name'),
    'publishers.Publisher': ('publisher', 'name'),
    'campaigns.Campaign': ('campaign', 'name'),
    'routing.RoutingRule': ('routing_rule', 'name'),
    'phone_numbers.PhoneNumber': ('phone_number', 'number'),
    'notifications.NotificationRule': ('notification_rule', 'name'),
    'accounts.User': ('user', 'email'),
}


def _label(instance) -> str:
    return f'{instance._meta.app_label}.{instance._meta.object_name}'


def _write(instance, action, extra=None):
    from .models import ActivityLog

    tracked = TRACKED.get(_label(instance))
    if tracked is None:
        return

    target_type, name_field = tracked

    user = get_current_user()
    if user is None:
        # A management command, a Celery task, or the seeding code. There is no
        # person to attribute it to, and an entry with no actor is worse than
        # none: the feed is there to answer "who did this".
        return

    request = get_current_request()

    try:
        ActivityLog.objects.create(
            user=user,
            action=action,
            ip_address=_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else '')[:500],
            metadata={
                'target_type': target_type,
                'target_id': str(instance.pk),
                'target_name': str(getattr(instance, name_field, '') or ''),
                **(extra or {}),
            },
        )
    except Exception:
        # Never let the audit line take the save down with it.
        logger.exception(
            'activity log write failed: action=%s target=%s id=%s',
            action, target_type, instance.pk,
        )


def _client_ip(request):
    if request is None:
        return None
    # RealClientIPMiddleware has already rewritten REMOTE_ADDR from
    # CF-Connecting-IP, so this is the caller's address and not Cloudflare's.
    ip = request.META.get('REMOTE_ADDR') or None
    return ip


def on_save(sender, instance, created, **kwargs):
    from .models import ActivityLog

    # Every login saves the user to stamp `last_login`, and a password reset
    # saves it again. Both already have their own entry written by hand in
    # `accounts/services.py`, so logging the save as well would double every
    # login in the feed.
    update_fields = kwargs.get('update_fields')
    if update_fields and set(update_fields) <= {'last_login', 'password', 'updated_at'}:
        return

    _write(
        instance,
        ActivityLog.Action.RECORD_CREATED if created else ActivityLog.Action.RECORD_UPDATED,
        {'fields': sorted(update_fields)} if update_fields else None,
    )


def on_delete(sender, instance, **kwargs):
    from .models import ActivityLog

    _write(instance, ActivityLog.Action.RECORD_DELETED)


def connect():
    """Bind the handlers to the tracked models, and to nothing else.

    Each receiver is registered against its own `sender`. A receiver registered
    without one is called for every save the platform makes, which on this
    system means every CallLog write on the call path - thousands a day, each
    one entering a handler only to look up a dictionary and leave. Binding per
    model means a call never enters this code at all.
    """
    from django.apps import apps

    for label in TRACKED:
        app_label, model_name = label.split('.')
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            logger.warning('activity log: no model %s, not tracking it', label)
            continue

        post_save.connect(
            on_save, sender=model, dispatch_uid=f'activity_log_save_{label}',
        )
        post_delete.connect(
            on_delete, sender=model, dispatch_uid=f'activity_log_delete_{label}',
        )

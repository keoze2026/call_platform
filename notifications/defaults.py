"""Give every workspace the notification rules it should have had from day one.

The detectors worked and the delivery worked, but nothing connected them: an
alert is only sent if a rule exists for that event, and rules could only be
created by hand in the UI. Every workspace had none, so cap warnings, low
balance and missed-call spikes were detected and then dropped in silence.

Requiring someone to build the same six rules by hand for every new client is
the kind of setup step that gets forgotten once and then never noticed, because
the symptom is an alert that does not arrive.

Runs on every alert sweep, so existing workspaces are repaired without anyone
touching the server, and a new workspace has its rules before its first call.

Deliberately conservative about existing data:

  - a rule that already exists is never modified
  - recipients are only filled in when the list is empty, so removing yourself
    from an alert stays removed
  - a rule pointing at an event or channel that cannot be delivered is switched
    off rather than deleted, so the record of it survives
"""
import logging

from django.conf import settings

from .models import NotificationRule

logger = logging.getLogger(__name__)


# The events a workspace should hear about without asking. Overridable in
# settings so the set can change without a deploy.
DEFAULT_EVENTS = getattr(settings, 'DEFAULT_NOTIFICATION_EVENTS', None) or [
    'low.balance',
    'campaign.cap_reached',
    'buyer.cap_reached',
    'destination.cap_reached',
    'buyer.missed',
    'aht.low',
]

NAMES = {
    'low.balance': 'Low balance',
    'campaign.cap_reached': 'Campaign cap reached',
    'buyer.cap_reached': 'Buyer cap reached',
    'destination.cap_reached': 'Destination cap reached',
    'buyer.missed': 'Buyer missing calls',
    'aht.low': 'Average handle time dropped',
    'campaign.paused': 'Campaign paused',
    'daily.summary': 'Daily summary',
}


def admin_emails(organization) -> list:
    """Who hears about this workspace by default."""
    return list(
        organization.members
        .filter(role__in=['admin', 'reseller'], is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )


def ensure_default_rules(organization) -> dict:
    """Create any missing default rules, and repair ones that cannot deliver.

    Returns a small summary so the caller can log what changed. Safe to call as
    often as you like: it does nothing once the workspace is in good shape.
    """
    valid_events = {v for v, _ in NotificationRule.Event.choices}
    valid_channels = {v for v, _ in NotificationRule.Channel.choices}

    created = filled = disabled = 0
    recipients = admin_emails(organization)

    # Switch off rules that can never deliver: an event no detector raises, or a
    # channel with no sending code behind it. These look active in the UI, which
    # is worse than being absent, because the workspace believes it is covered.
    for rule in NotificationRule.objects.filter(organization=organization, is_active=True):
        if rule.event not in valid_events or rule.channel not in valid_channels:
            NotificationRule.objects.filter(pk=rule.pk).update(is_active=False)
            disabled += 1
            logger.warning(
                'disabled undeliverable notification rule: org=%s rule=%s event=%r channel=%r',
                organization.id, rule.id, rule.event, rule.channel,
            )

    for event in DEFAULT_EVENTS:
        if event not in valid_events:
            logger.warning('DEFAULT_NOTIFICATION_EVENTS contains unknown event %r', event)
            continue

        rule, was_created = NotificationRule.objects.get_or_create(
            organization=organization,
            event=event,
            channel=NotificationRule.Channel.EMAIL,
            defaults={
                'name': NAMES.get(event, event),
                'recipients': recipients,
                'is_active': True,
            },
        )
        if was_created:
            created += 1
            continue

        # An existing rule with nobody on it delivers nothing. Filling an empty
        # list is a repair; a list someone has edited is left alone.
        if not rule.recipients and recipients:
            NotificationRule.objects.filter(pk=rule.pk).update(recipients=recipients)
            filled += 1

    if created or filled or disabled:
        logger.info(
            'notification rules for %s: %s created, %s given recipients, %s disabled',
            organization.name, created, filled, disabled,
        )

    return {'created': created, 'filled': filled, 'disabled': disabled}

"""CH-578: every row carries the account it belongs to.

Sir, 5 Nov (customer accounts, point 1): "Every record ... carries an
account_id." The main tables (campaigns, buyers, publishers, destinations,
TFNs, calls ...) always had it. These 36 did not: they reached their account
only through a parent (a buyer's cap through the buyer, a campaign line through
its routing rule). Row-level security (CH-579) needs the account on the row
itself, so each of them now has `organization`, and this module fills it.

Three kinds:

  PARENT   the account of the parent row - always, on every save, so a row
           moved to another parent moves with it. Required in the database.
  USER     the account of the login the row is about (activity log, tokens,
           PIN attempts, preferences). A login without an account has rows
           without one; under row-level security nobody sees those.
  WEBSITE  rows from the public website, before anyone logs in (support chat,
           contact form, access requests, visitor counts): our main account
           (settings.MAIN_ORGANIZATION_SLUG). Kept if already set.

Filled in `pre_save`, so every path that saves a model fills it; nothing in
the code inserts these rows with raw SQL or bulk_create (checked 5 Nov). The
migrations fill the rows that already exist the same way.
"""
from django.conf import settings
from django.db.models.signals import pre_save

# label -> the attribute whose account the row takes
PARENT = {
    'campaigns.CampaignCap': 'campaign',
    'campaigns.CampaignSchedule': 'campaign',
    'integrations.IntegrationActivity': 'integration',
    'buyers.BuyerCap': 'buyer',
    'buyers.BuyerCampaign': 'buyer',
    'buyers.BuyerSchedule': 'buyer',
    'buyers.BuyerTelegramReport': 'buyer',
    'publishers.PublisherCap': 'publisher',
    'publishers.PublisherCampaign': 'publisher',
    'routing.RuleCondition': 'rule',
    'routing.RuleDestination': 'rule',
    'referrals.Referral': 'program',
    'referrals.ReferralEarning': 'program',
    'ivr.IVRNode': 'flow',
    'ivr.IVRNodeTransition': 'from_node',
    'dni.DNINumber': 'pool',
    'dni.DNISession': 'pool',
    'dni.DNILander': 'pool',
    'dni.DNICall': 'pool',
    'rtb.RTBBid': 'auction',
    'white_label.WhiteLabelDomain': 'white_label',
    'webhooks.WebhookDelivery': 'webhook',
    'webhooks.ConversionEvent': 'pixel',
}
USER = {
    'accounts.ActivityLog': 'user',
    'accounts.PasswordResetToken': 'user',
    'accounts.SetupToken': 'user',
    'accounts.TelegramLinkCode': 'user',
    'reports_security.ReportsPinUnlock': 'user',
    'reports_security.ReportsPinAttempt': 'user',
    'notifications.NotificationPreference': 'user',
    'marketplace.FavoriteService': 'user',
}
WEBSITE = {
    'accounts.AccessRequest': None,
    'accounts.ContactMessage': None,
    'analytics.GuestVisit': None,
    'support.SupportSession': None,
    'support.SupportMessage': 'session',
}


def main_organization_id():
    """Our main account, or None when it does not exist (tests, a new server)."""
    from accounts.models import Organization
    slug = getattr(settings, 'MAIN_ORGANIZATION_SLUG', '')
    if not slug:
        return None
    # Rehearsal B, 10 Nov: looked up as system. Under row-level security a
    # customer login cannot see our account's row, so the answer was None -
    # "no main account" - and every customer login counted as main (point 3).
    from accounts import rls
    with rls.system():
        return Organization.objects.filter(slug=slug).values_list('id', flat=True).first()


def _account_of(row):
    """A parent row's account, wherever it keeps it."""
    if row is None:
        return None
    org = getattr(row, 'organization_id', None)
    if org is None and hasattr(row, 'flow'):          # an IVR step saved before CH-578
        org = getattr(row.flow, 'organization_id', None)
    return org


def account_for(instance):
    """(the account this row must carry, whether it must replace what is there)."""
    explicit = getattr(instance, '_avx_account', None)
    if explicit is not None:
        # CH-637: an owner audit row written into the account it is about.
        return explicit, True
    label = instance._meta.label
    if label in PARENT:
        return _account_of(getattr(instance, PARENT[label], None)), True
    if label in USER:
        return _account_of(getattr(instance, USER[label], None)), True
    parent = WEBSITE.get(label)
    if parent:
        org = _account_of(getattr(instance, parent, None))
        return (org if org is not None else main_organization_id()), False
    return main_organization_id(), False


def fill(sender, instance, raw=False, **kwargs):
    if raw:                       # loaddata: the fixture says what it is
        return
    org, always = account_for(instance)
    if always or instance.organization_id is None:
        instance.organization_id = org


def connect():
    from django.apps import apps
    for label in (*PARENT, *USER, *WEBSITE):
        pre_save.connect(fill, sender=apps.get_model(label), dispatch_uid=f'ch578-{label}')


def fill_existing(apps, labels):
    """For the CH-578 migrations: give the rows already there their account,
    the same way `fill` does on save. One UPDATE per table."""
    from django.db.models import OuterRef, Subquery
    main = None
    for label in labels:
        model = apps.get_model(label)
        todo = model.objects.filter(organization__isnull=True)
        attr = PARENT.get(label) or USER.get(label) or WEBSITE.get(label)
        if attr:
            fk = model._meta.get_field(attr)
            parent = fk.related_model
            todo.update(organization_id=Subquery(
                parent.objects.filter(pk=OuterRef(fk.attname)).values('organization_id')[:1]))
        if label in WEBSITE:
            if main is None:
                slug = getattr(settings, 'MAIN_ORGANIZATION_SLUG', '')
                org = apps.get_model('accounts', 'Organization')
                main = org.objects.filter(slug=slug).values_list('id', flat=True).first() if slug else None
            if main is not None:
                model.objects.filter(organization__isnull=True).update(organization_id=main)


def fill_migration(*labels):
    """A RunPython operation filling these tables (order matters: a parent first)."""
    from django.db import migrations
    return migrations.RunPython(lambda apps, schema_editor: fill_existing(apps, labels),
                                migrations.RunPython.noop)

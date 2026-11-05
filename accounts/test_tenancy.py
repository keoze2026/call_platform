"""CH-578: every row carries the account it belongs to.

Run:  python manage.py test accounts.test_tenancy
"""
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts import tenancy
from accounts.models import ActivityLog, Organization, User
from buyers.models import Buyer, BuyerCap
from campaigns.models import Campaign
from ivr.models import IVRFlow, IVRNode, IVRNodeTransition
from routing.models import RoutingRule, RuleDestination
from support.models import SupportMessage, SupportSession

# Tables shared by every account by design: catalogues and our carrier rates.
SHARED = {'accounts.Organization', 'integrations.Integration', 'phone_numbers.RoutingRate', 'billing.Plan',
          'marketplace.ServiceCategory', 'marketplace.MarketplaceService'}
OURS = {'accounts', 'campaigns', 'integrations', 'buyers', 'publishers', 'phone_numbers', 'routing',
        'spam_protection', 'reports_security', 'referrals', 'support', 'ivr', 'dni', 'analytics', 'rtb',
        'white_label', 'webhooks', 'notifications', 'billing', 'marketplace'}


def has_account(model):
    return any(f.attname == 'organization_id' for f in model._meta.concrete_fields)


class EveryTableTests(TestCase):
    def test_every_table_carries_an_account(self):
        """A new table without an account fails here until it gets one (or is
        added to SHARED on purpose)."""
        missing = [m._meta.label for m in apps.get_models()
                   if m._meta.app_label in OURS and m._meta.label not in SHARED and not has_account(m)]
        self.assertEqual(missing, [])

    def test_the_36_are_filled_by_tenancy(self):
        labels = {*tenancy.PARENT, *tenancy.USER, *tenancy.WEBSITE}
        self.assertEqual(len(labels), 36)
        for label in labels:
            self.assertTrue(has_account(apps.get_model(label)), label)

    def test_rows_with_a_parent_must_have_an_account(self):
        for label in tenancy.PARENT:
            field = apps.get_model(label)._meta.get_field('organization')
            self.assertFalse(field.null, label)

    def test_each_parent_kind_gives_its_account(self):
        """Every PARENT / USER table takes the account of its parent row."""
        for label, attr in {**tenancy.PARENT, **tenancy.USER}.items():
            row = apps.get_model(label)()
            row._state.fields_cache[attr] = SimpleNamespace(organization_id='acct-1')
            self.assertEqual(tenancy.account_for(row), ('acct-1', True), label)


@override_settings(ALLOWED_HOSTS=['*'], MAIN_ORGANIZATION_SLUG='interns',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class FillTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.main = Organization.objects.create(name='Avortyx', slug='interns')
        self.other = Organization.objects.create(name='Test A', slug='test-a')

    def test_a_buyer_cap_takes_the_buyer_account(self):
        buyer = Buyer.objects.create(organization=self.other, name='B1')
        cap = BuyerCap.objects.create(buyer=buyer)
        self.assertEqual(cap.organization_id, self.other.id)

    def test_a_campaign_line_follows_its_rule(self):
        rules = []
        for org in (self.main, self.other):
            c = Campaign.objects.create(organization=org, name=f'C-{org.slug}')
            rules.append(RoutingRule.objects.create(organization=org, campaign=c, name='r', rule_type='priority'))
        line = RuleDestination.objects.create(rule=rules[0], destination='+18885480775')
        self.assertEqual(line.organization_id, self.main.id)
        line.rule = rules[1]
        line.save()
        self.assertEqual(line.organization_id, self.other.id)

    def test_ivr_steps_take_the_flow_account(self):
        flow = IVRFlow.objects.create(organization=self.other, name='f')
        a = IVRNode.objects.create(flow=flow, node_type='menu', name='a')
        t = IVRNodeTransition.objects.create(from_node=a, trigger='1')
        self.assertEqual((a.organization_id, t.organization_id), (self.other.id, self.other.id))

    def test_activity_log_takes_the_login_account_and_none_without_one(self):
        u = User.objects.create(username='u', email='u@x.io', organization=self.other, role='admin')
        loose = User.objects.create(username='h', email='h@x.io', role='agent')   # no account
        a = ActivityLog.objects.create(user=u, action=ActivityLog.Action.LOGIN)
        b = ActivityLog.objects.create(user=loose, action=ActivityLog.Action.LOGIN)
        self.assertEqual((a.organization_id, b.organization_id), (self.other.id, None))

    def test_the_website_support_chat_is_ours(self):
        r = Client().post('/api/support/chat', {'name': 'V', 'email': 'v@x.io', 'message': 'Hi'},
                          content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        s = SupportSession.objects.get()
        self.assertEqual(s.organization_id, self.main.id)
        self.assertEqual(SupportMessage.objects.get().organization_id, self.main.id)

    def test_an_account_set_on_a_website_row_is_kept(self):
        s = SupportSession.objects.create(name='x', organization=self.other)
        self.assertEqual(s.organization_id, self.other.id)

    def test_an_api_login_creates_rows_of_its_own_account(self):
        u = User.objects.create(username='a', email='a@x.io', organization=self.other, role='admin',
                                is_superuser=True)
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')
        r = api.post('/api/buyers/', {'name': 'From API'}, content_type='application/json')
        self.assertIn(r.status_code, (200, 201), r.content)
        buyer = Buyer.objects.get(name='From API')
        self.assertEqual(BuyerCap.objects.get_or_create(buyer=buyer)[0].organization_id, self.other.id)


class MigrationFillTests(TestCase):
    """The migrations fill rows that are already there the same way."""

    @override_settings(MAIN_ORGANIZATION_SLUG='interns')
    def test_existing_rows_get_their_account(self):
        main = Organization.objects.create(name='Avortyx', slug='interns')
        other = Organization.objects.create(name='Test A', slug='test-a')
        u = User.objects.create(username='u', email='u@x.io', organization=other, role='admin')
        log = ActivityLog.objects.create(user=u, action=ActivityLog.Action.LOGIN)
        chat = SupportSession.objects.create(name='v')
        flow = IVRFlow.objects.create(organization=other, name='f')
        node = IVRNode.objects.create(flow=flow, node_type='menu', name='a')
        step = IVRNodeTransition.objects.create(from_node=node, trigger='1')
        # As before CH-578 (the user-scoped and website ones can be empty).
        ActivityLog.objects.update(organization=None)
        SupportSession.objects.update(organization=None)
        tenancy.fill_existing(apps, ['accounts.ActivityLog', 'support.SupportSession', 'ivr.IVRNode',
                                     'ivr.IVRNodeTransition'])
        for row in (log, chat, node, step):
            row.refresh_from_db()
        self.assertEqual((log.organization_id, chat.organization_id), (other.id, main.id))
        self.assertEqual((node.organization_id, step.organization_id), (other.id, other.id))

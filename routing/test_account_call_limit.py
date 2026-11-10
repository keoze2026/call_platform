"""CH-655 (customer accounts point 6): a per-customer limit on live calls, so
customer traffic can never affect our own calls. Set by the platform owner on
the account (0 = no limit); our main account is never limited.

Run:  python manage.py test routing.test_account_call_limit
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from routing.engine import RoutingEngine
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'], CACHES=CACHE)
class AccountCallLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ours = Organization.objects.create(name='Avortyx', slug='interns', max_concurrent_calls=0)
        self.theirs = Organization.objects.create(name='Texora', slug='texora', max_concurrent_calls=2)
        self.their_campaign = Campaign.objects.create(organization=self.theirs, name='T-01', status='active')
        self.our_campaign = Campaign.objects.create(organization=self.ours, name='L-10', status='active')
        self.n = 0

    def live(self, campaign, status='in_progress', **kw):
        self.n += 1
        return CallLog.objects.create(organization=campaign.organization, campaign=campaign,
                                      caller_number=f'+1555000{self.n:04d}', called_number='+18005550101',
                                      twilio_call_sid=f's{self.n}', status=status, **kw)

    def check(self, campaign, sid=None):
        return RoutingEngine.check_account_concurrency(campaign, current_sid=sid)

    def test_a_customer_stops_at_its_limit(self):
        self.live(self.their_campaign)
        self.assertIsNone(self.check(self.their_campaign))
        self.live(self.their_campaign, status='ringing')
        self.assertEqual(self.check(self.their_campaign), 'Account call limit reached')

    def test_the_call_being_routed_is_not_counted(self):
        self.live(self.their_campaign)
        newcomer = self.live(self.their_campaign, status='ringing')
        self.assertIsNone(self.check(self.their_campaign, sid=newcomer.twilio_call_sid))

    def test_only_live_calls_count(self):
        self.live(self.their_campaign, status='completed', ended_at=timezone.now())
        self.live(self.their_campaign, ended_at=timezone.now())
        stuck = self.live(self.their_campaign)
        CallLog.objects.filter(pk=stuck.pk).update(created_at=timezone.now() - timedelta(hours=5))
        self.assertIsNone(self.check(self.their_campaign))

    def test_other_accounts_calls_do_not_count(self):
        for _ in range(5):
            self.live(self.our_campaign)
        self.assertIsNone(self.check(self.their_campaign))

    def test_no_limit_and_our_account_are_never_limited(self):
        Organization.objects.filter(pk=self.theirs.pk).update(max_concurrent_calls=0)
        self.their_campaign.refresh_from_db()
        for _ in range(3):
            self.live(self.their_campaign)
        self.assertIsNone(self.check(Campaign.objects.get(pk=self.their_campaign.pk)))
        # Even if a limit were set on our main account, it is not applied.
        Organization.objects.filter(pk=self.ours.pk).update(max_concurrent_calls=1)
        for _ in range(3):
            self.live(self.our_campaign)
        self.assertIsNone(self.check(Campaign.objects.get(pk=self.our_campaign.pk)))

    def test_routing_refuses_the_call_with_the_label(self):
        self.live(self.their_campaign)
        self.live(self.their_campaign)
        result = RoutingEngine.route_call(str(self.their_campaign.id),
                                          {'caller_number': '+15557770000', 'twilio_call_sid': 'new'})
        self.assertEqual(result['error'], 'Account call limit reached')
        self.assertIsNone(result['destination'])


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'], CACHES=CACHE)
class OwnerSetsTheLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ours = Organization.objects.create(name='Avortyx', slug='interns')
        self.theirs = Organization.objects.create(name='Texora', slug='texora')
        self.sir = User.objects.create(username='sir', email='sir@x.io', organization=self.ours,
                                       role='admin', is_superuser=True, is_active=True)
        self.c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.sir)}')

    def patch(self, org, body):
        return self.c.patch(f'/api/owner/organizations/{org.id}', json.dumps(body),
                            content_type='application/json')

    def test_the_owner_sets_and_reads_it(self):
        self.assertEqual(self.patch(self.theirs, {'max_concurrent_calls': '25'}).status_code, 200)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.max_concurrent_calls, 25)
        d = self.c.get(f'/api/owner/organizations/{self.theirs.id}').json()
        self.assertEqual((d['max_concurrent_calls'], d['is_main_account']), (25, False))

    def test_bad_values_and_our_own_account(self):
        for bad in ('-1', 'ten', '10001'):
            self.assertEqual(self.patch(self.theirs, {'max_concurrent_calls': bad}).status_code, 400, bad)
        self.assertEqual(self.patch(self.ours, {'max_concurrent_calls': 5}).status_code, 400)
        # 0 on our own account (the form always sends the field) is not a change.
        self.assertEqual(self.patch(self.ours, {'max_concurrent_calls': 0, 'name': 'Avortyx'}).status_code, 200)

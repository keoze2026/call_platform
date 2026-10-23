"""CH-221: an API key made for a buyer's login sees only that buyer.

Run:  python manage.py test analytics.test_buyer_api_key
"""
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization, User
from accounts.services import APIKeyService
from analytics.models import CallRecord
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=CACHE, ALLOWED_HOSTS=['*'])
class BuyerApiKeyTests(TestCase):
    def setUp(self):
        p = mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.org = Organization.objects.create(name='Org', slug='org')
        self.mine = Buyer.objects.create(organization=self.org, name='ADST')
        self.other = Buyer.objects.create(organization=self.org, name='PDSO')
        self.c_mine = Campaign.objects.create(organization=self.org, name='C-02')
        self.c_other = Campaign.objects.create(organization=self.org, name='C-03')
        self.admin = User.objects.create(username='adm', email='adm@x.io', organization=self.org,
                                         role='admin', is_active=True)
        self.buyer_login = User.objects.create(username='buy', email='buy@x.io', organization=self.org,
                                               role='buyer', buyer=self.mine, is_active=True)
        for buyer, campaign in ((self.mine, self.c_mine), (self.other, self.c_other)):
            CallLog.objects.create(organization=self.org, caller_number='+15551234567',
                                   called_number='+18005550100', status='in_progress',
                                   buyer=buyer, campaign=campaign,
                                   twilio_call_sid=f'live-{buyer.name}')
            CallRecord.objects.create(organization=self.org, caller_number='+15551234567',
                                      called_number='+18005550100', status='completed',
                                      buyer_id=buyer.id, buyer_name=buyer.name,
                                      campaign=campaign, campaign_name=campaign.name,
                                      payout=5, revenue=20, started_at=timezone.now())

    def key_for(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {APIKeyService.create(user, "test")["key"]}')

    def test_a_buyer_key_sees_only_its_own_live_calls(self):
        body = self.key_for(self.buyer_login).get('/api/stats/concurrency').json()
        self.assertEqual(body['total_cc'], 1)
        self.assertEqual([b['name'] for b in body['by_buyer']], ['ADST'])
        self.assertEqual([c['name'] for c in body['by_campaign']], ['C-02'])

    def test_an_admin_key_still_sees_everything(self):
        body = self.key_for(self.admin).get('/api/stats/concurrency').json()
        self.assertEqual(body['total_cc'], 2)

    def test_a_buyer_key_gets_only_its_buyer_and_no_payout(self):
        today = timezone.now().date().isoformat()
        r = self.key_for(self.buyer_login).get(
            f'/api/stats/buyers?date_from={today}&date_to={today}&timezone_name=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()
        self.assertEqual({row.get('buyer_name') or row.get('name') for row in rows}, {'ADST'})
        for row in rows:
            for key in ('payout', 'total_payout', 'profit', 'total_profit'):
                if key in row:
                    self.assertIsNone(row[key], key)

    def test_a_key_for_a_buyer_login_with_no_buyer_sees_nothing(self):
        stray = User.objects.create(username='s', email='s@x.io', organization=self.org,
                                    role='buyer', is_active=True)
        body = self.key_for(stray).get('/api/stats/concurrency').json()
        self.assertEqual(body['total_cc'], 0)

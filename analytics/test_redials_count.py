"""CH-504: a redial that rang a TFN counts on that TFN everywhere.

Sir, 3 Nov (P30: bot 9, Destinations page 7 on the same TFN): "It doesn't
matter if the call is missed or answered - if it's missed on the destination
it counts everywhere in the report and the destination box, because the
incoming is there on that TFN."

Run:  python manage.py test analytics.test_redials_count
"""
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from accounts.services import APIKeyService
from buyers.caps import counted_q
from buyers.destination import Destination
from buyers.destinations_api import gather_destination_counts
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
TFN = '+18886652085'


@override_settings(CACHES=CACHE)
class RedialCountTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin')
        self.p30 = Buyer.objects.create(organization=self.org, name='P30')
        self.camp = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        Destination.objects.create(organization=self.org, buyer=self.p30, name='P30|2085|', tfn=TFN, enabled=True)
        self.n = 0
        for status, hidden, reason in [('completed', False, ''), ('completed', False, ''),
                                       ('no_answer', True, 'No Answer'), ('no_answer', True, 'No Answer'),
                                       ('failed', False, 'No Destination')]:
            self.call(status, hidden, reason)

    def call(self, status, hidden, reason):
        self.n += 1
        return CallLog.objects.create(
            organization=self.org, campaign=self.camp, buyer=self.p30, caller_number=f'+1555000{self.n:04d}',
            called_number='+18668215575', destination_number=TFN, twilio_call_sid=f's{self.n}',
            status=status, retry_hidden=hidden, block_reason=reason)

    def test_destination_box_counts_every_ring_not_refusals(self):
        c = gather_destination_counts(self.org, [TFN])[(str(self.p30.id), TFN)]
        self.assertEqual((c['daily'], c['global']), (4, 4))   # 2 answered + 2 redials, no refusal
        self.assertEqual(CallLog.objects.filter(counted_q()).count(), 4)

    def test_caps_count_one_per_caller_call(self):
        # CH-511: shown 4 rings, but the cap counts the 2 caller calls.
        from buyers.caps import buyer_cap_hit, destination_cap_hit
        from buyers.models import BuyerCap
        line = Destination.objects.get(tfn=TFN)
        line.daily_cap = 3
        line.save()
        self.assertIsNone(destination_cap_hit(line))
        cap = BuyerCap.objects.create(buyer=self.p30, max_calls_daily=3)
        self.assertIsNone(buyer_cap_hit(self.p30, cap))

    def get(self, path, user=None, key=None):
        if key:
            c = Client(HTTP_X_API_KEY=key, HTTP_AUTHORIZATION=f'Bearer {key}')
        else:
            c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user or self.admin)}')
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            return c.get(path)

    def test_redials_endpoint_lists_the_hidden_rings_unbilled(self):
        day = timezone.localtime(timezone.now()).date().isoformat()
        r = self.get(f'/api/analytics/redials?date_from={day}&date_to={day}&timezone=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        items = r.json()['items']
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['is_redial'] and i['destination_number'] == TFN for i in items))
        self.assertTrue(all(float(i['revenue']) == 0 and float(i['payout']) == 0 for i in items))
        self.assertEqual({i['buyer_name'] for i in items}, {'P30'})

    def test_bot_destinations_counts_the_same(self):
        bot = User.objects.create(username='p30bot@avortyx.io', email='p30bot@avortyx.io',
                                  organization=self.org, role='buyer', buyer=self.p30, is_active=True)
        key = APIKeyService.create(bot, 'P30 Stats API')['key']
        r = self.get('/api/stats/destinations?timezone_name=UTC', key=key)
        self.assertEqual(r.status_code, 200, r.content)
        row = next(x for x in r.json() if x['tfn'] == TFN)
        self.assertEqual(row['incoming_calls'], 4)
